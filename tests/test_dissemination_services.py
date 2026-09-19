"""Unit tests for the Cloud Dissemination Module."""

from __future__ import annotations

import pytest
import requests
import responses
from django.core import mail

from core.models import DrillParticipant, Notification
from dissemination import pubsub
from dissemination.gateways import EmailChannel, M360Client
from dissemination.services import (
    deliver_notification,
    dispatch_notifications,
    health_check,
    send_now,
)
from engine.services import build_roster, queue_notifications

pytestmark = pytest.mark.django_db


@pytest.fixture
def queued(drill):
    """Two people, SMS only, all messages queued and unsent."""
    drill.send_email = False
    drill.save()
    build_roster(drill)
    participants = list(drill.participants.select_related("employee__location"))
    return queue_notifications(drill, participants)


class TestM360Client:
    def test_simulates_delivery_when_disabled(self, settings):
        settings.M360_ENABLED = False

        result = M360Client().send("+639171234567", "evacuate")

        assert result.ok
        assert result.simulated
        assert result.provider_message_id == "sim-639171234567"

    def test_rejects_an_unusable_number_before_calling_out(self):
        result = M360Client().send("not a number", "evacuate")

        assert not result.ok
        assert "Unusable mobile number" in result.error

    @responses.activate
    def test_posts_normalised_digits_to_the_gateway(self, settings):
        settings.M360_ENABLED = True
        settings.M360_APP_KEY = "key"
        settings.M360_APP_SECRET = "secret"
        settings.M360_SHORTCODE_MASK = "DSO"
        responses.post(settings.M360_BROADCAST_URL, json={"code": 0, "message_id": "m-1"})

        result = M360Client().send("0917 123 4567", "evacuate")

        assert result.ok
        assert result.provider_message_id == "m-1"
        sent = responses.calls[0].request
        assert b'"msisdn": "639171234567"' in sent.body
        assert b'"shortcode_mask": "DSO"' in sent.body

    @responses.activate
    def test_http_error_is_reported_not_raised(self, settings):
        settings.M360_ENABLED = True
        settings.M360_APP_KEY = "key"
        responses.post(settings.M360_BROADCAST_URL, status=502, body="bad gateway")

        result = M360Client().send("+639171234567", "evacuate")

        assert not result.ok
        assert "502" in result.error

    @responses.activate
    def test_application_level_failure_code_is_a_failure(self, settings):
        """A 200 with a non-zero code means the gateway rejected the message."""
        settings.M360_ENABLED = True
        settings.M360_APP_KEY = "key"
        responses.post(
            settings.M360_BROADCAST_URL,
            json={"code": 105, "message": "insufficient credits"},
        )

        result = M360Client().send("+639171234567", "evacuate")

        assert not result.ok
        assert "insufficient credits" in result.error

    @responses.activate
    def test_a_network_error_is_reported_not_raised(self, settings):
        settings.M360_ENABLED = True
        settings.M360_APP_KEY = "key"
        responses.post(
            settings.M360_BROADCAST_URL, body=requests.ConnectionError("no route")
        )

        result = M360Client().send("+639171234567", "evacuate")

        assert not result.ok
        assert "M360 request failed" in result.error

    @responses.activate
    def test_a_non_json_success_body_still_counts_as_accepted(self, settings):
        settings.M360_ENABLED = True
        settings.M360_APP_KEY = "key"
        responses.post(settings.M360_BROADCAST_URL, body="OK", status=200)

        assert M360Client().send("+639171234567", "evacuate").ok

    def test_balance_reports_the_simulated_state_when_disabled(self, settings):
        settings.M360_ENABLED = False

        assert M360Client().balance() == {
            "enabled": False,
            "simulated": True,
            "balance": None,
        }

    @responses.activate
    def test_balance_failure_is_reported_not_raised(self, settings):
        settings.M360_ENABLED = True
        settings.M360_APP_KEY = "key"
        responses.get(settings.M360_BALANCE_URL, status=500)

        result = M360Client().balance()

        assert result["balance"] is None
        assert "error" in result


class TestEmailChannel:
    def test_sends_through_the_configured_backend(self):
        with EmailChannel(subject="[DSO] Drill") as channel:
            result = channel.send("paolo@example.com", "evacuate now")

        assert result.ok
        assert len(mail.outbox) == 1
        assert mail.outbox[0].subject == "[DSO] Drill"
        assert mail.outbox[0].to == ["paolo@example.com"]

    def test_missing_address_is_a_reported_failure(self):
        result = EmailChannel().send("", "evacuate")

        assert not result.ok
        assert "No email address" in result.error


class TestDeliverNotification:
    def test_marks_the_row_sent_and_stamps_the_time(self, queued):
        status = deliver_notification(queued[0].pk)

        queued[0].refresh_from_db()
        assert status == Notification.Status.SENT
        assert queued[0].sent_at is not None
        assert queued[0].attempts == 1
        assert queued[0].dispatch_latency_seconds >= 0

    def test_moves_the_participant_to_notified(self, queued):
        deliver_notification(queued[0].pk)

        participant = DrillParticipant.objects.get(pk=queued[0].participant_id)
        assert participant.status == DrillParticipant.Status.NOTIFIED
        assert participant.notified_at is not None

    def test_redelivery_does_not_send_twice(self, queued):
        """Pub/Sub is at-least-once, so this has to be idempotent."""
        deliver_notification(queued[0].pk)
        queued[0].refresh_from_db()
        first_sent_at = queued[0].sent_at

        deliver_notification(queued[0].pk)

        queued[0].refresh_from_db()
        assert queued[0].attempts == 1
        assert queued[0].sent_at == first_sent_at

    def test_delivery_does_not_overwrite_a_status_the_employee_already_set(self, queued):
        """A reply can beat the email leg of the same broadcast."""
        participant = DrillParticipant.objects.get(pk=queued[0].participant_id)
        participant.status = DrillParticipant.Status.SAFE
        participant.save()

        deliver_notification(queued[0].pk)

        participant.refresh_from_db()
        assert participant.status == DrillParticipant.Status.SAFE

    def test_a_missing_row_is_reported_not_raised(self):
        assert deliver_notification(999999) == Notification.Status.FAILED

    def test_a_skipped_row_is_left_alone(self, drill, staff):
        staff.email = ""
        staff.save()
        build_roster(drill)
        participants = list(drill.participants.select_related("employee__location"))
        queue_notifications(drill, participants)
        skipped = Notification.objects.get(status=Notification.Status.SKIPPED)

        assert deliver_notification(skipped.pk) == Notification.Status.SKIPPED
        skipped.refresh_from_db()
        assert skipped.attempts == 0

    @responses.activate
    def test_a_gateway_failure_is_recorded_on_the_row(self, queued, settings):
        settings.M360_ENABLED = True
        settings.M360_APP_KEY = "key"
        responses.post(settings.M360_BROADCAST_URL, status=500, body="boom")

        status = deliver_notification(queued[0].pk)

        queued[0].refresh_from_db()
        assert status == Notification.Status.FAILED
        assert "500" in queued[0].error_message
        assert queued[0].sent_at is None
        assert queued[0].attempts == 1

    def test_the_subject_reflects_the_purpose(self, drill):
        drill.send_sms = False
        drill.save()
        build_roster(drill)
        participants = list(drill.participants.select_related("employee__location"))
        rows = queue_notifications(
            drill, participants, purpose=Notification.Purpose.ESCALATION
        )

        deliver_notification(rows[0].pk)

        assert "ESCALATION" in mail.outbox[0].subject


class TestSendNow:
    def test_summarises_the_outcome(self, queued):
        summary = send_now([n.pk for n in queued])

        assert summary.sent == 2
        assert summary.failed == 0
        assert summary.total == 2

    def test_an_empty_batch_is_a_no_op(self):
        summary = send_now([])

        assert summary.total == 0

    def test_one_bad_recipient_does_not_stop_the_rest(self, queued, staff):
        broken = queued[0]
        broken.recipient = "not a number"
        broken.save()

        summary = send_now([n.pk for n in queued])

        assert summary.sent == 1
        assert summary.failed == 1


class TestDispatchNotifications:
    def test_falls_back_to_local_sending_when_pubsub_is_off(self, queued, settings):
        settings.PUBSUB_ENABLED = False

        summary = dispatch_notifications([n.pk for n in queued])

        assert summary.published == 0
        assert summary.sent == 2

    def test_publishes_to_pubsub_when_it_is_available(self, queued, settings, monkeypatch):
        settings.PUBSUB_ENABLED = True
        settings.GCP_PROJECT_ID = "demo-project"
        monkeypatch.setattr(pubsub, "publish_batch", lambda ids: len(list(ids)))

        summary = dispatch_notifications([n.pk for n in queued])

        assert summary.published == 2
        # Nothing was sent in-process; the push subscription does that.
        assert summary.sent == 0
        assert Notification.objects.filter(status=Notification.Status.SENT).count() == 0

    def test_an_empty_list_short_circuits(self):
        assert dispatch_notifications([]).total == 0


class TestPubSubPublisher:
    def test_disabled_publisher_reports_nothing_published(self, settings):
        settings.PUBSUB_ENABLED = False
        pubsub.reset_publisher()

        assert pubsub.publish_batch([1, 2, 3]) == 0

    def test_enabled_needs_a_project_id(self, settings):
        settings.PUBSUB_ENABLED = True
        settings.GCP_PROJECT_ID = ""

        assert not pubsub.is_enabled()

    def test_topic_path_is_fully_qualified(self, settings):
        settings.GCP_PROJECT_ID = "demo-project"
        settings.PUBSUB_TOPIC_NOTIFICATIONS = "calltree-notifications"

        assert pubsub.topic_path() == (
            "projects/demo-project/topics/calltree-notifications"
        )


class TestHealthCheck:
    def test_reports_every_subsystem(self):
        report = health_check()

        assert set(report) >= {
            "database",
            "sms_gateway",
            "email",
            "pubsub",
            "firestore",
            "queue",
            "ok",
        }
        assert report["database"]["ok"]

    def test_is_healthy_with_everything_simulated(self):
        assert health_check()["ok"]

    def test_names_the_local_fallback_dispatch_mode(self, settings):
        settings.PUBSUB_ENABLED = False

        assert health_check()["pubsub"]["mode"] == "local-thread-pool"

    def test_counts_the_queue_and_flags_failures(self, queued):
        report = health_check()
        assert report["queue"]["queued"] == 2

        Notification.objects.update(status=Notification.Status.FAILED)

        report = health_check()
        assert report["queue"]["failed"] == 2
        assert not report["queue"]["ok"]
        assert not report["ok"]
