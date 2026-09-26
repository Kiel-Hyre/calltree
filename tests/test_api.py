"""Integration tests for the REST API and the machine-to-machine webhooks."""

from __future__ import annotations

import base64
import json

import pytest

from core.models import Drill, DrillParticipant, InboundMessage, Notification, SeismicEvent
from engine.services import activate_drill, build_roster

pytestmark = pytest.mark.django_db


@pytest.fixture
def client_in(client, officer):
    client.force_login(officer)
    return client


@pytest.fixture
def active(drill, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        activate_drill(drill)
    drill.refresh_from_db()
    return drill


class TestAuthentication:
    def test_the_api_is_closed_to_anonymous_callers(self, client):
        assert client.get("/api/overview/").status_code == 403

    def test_session_reports_anonymous_and_seeds_a_csrf_token(self, client):
        response = client.get("/api/auth/session/")

        assert response.status_code == 200
        assert response.json()["authenticated"] is False
        assert response.json()["csrf_token"]

    def test_login_succeeds_for_a_staff_account(self, client, officer):
        response = client.post(
            "/api/auth/login/",
            data=json.dumps({"username": "safetyofficer", "password": "drill-pass-4821"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        assert response.json()["user"]["username"] == "safetyofficer"

    def test_a_wrong_password_is_rejected(self, client, officer):
        response = client.post(
            "/api/auth/login/",
            data=json.dumps({"username": "safetyofficer", "password": "wrong"}),
            content_type="application/json",
        )

        assert response.status_code == 401

    def test_a_non_staff_account_cannot_use_the_portal(self, client, django_user_model):
        django_user_model.objects.create_user(username="visitor", password="pw-12345678")

        response = client.post(
            "/api/auth/login/",
            data=json.dumps({"username": "visitor", "password": "pw-12345678"}),
            content_type="application/json",
        )

        assert response.status_code == 403


class TestDrillEndpoints:
    def test_activate_starts_the_call_tree(self, client_in, drill, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            response = client_in.post(f"/api/drills/{drill.pk}/activate/")

        assert response.status_code == 200
        assert response.json()["participants"] == 2
        drill.refresh_from_db()
        assert drill.status == Drill.Status.ACTIVE

    def test_activating_twice_is_a_400_not_a_500(self, client_in, active):
        response = client_in.post(f"/api/drills/{active.pk}/activate/")

        assert response.status_code == 400
        assert "already active" in response.json()["detail"]

    def test_monitor_returns_stats_and_the_roster(self, client_in, active):
        payload = client_in.get(f"/api/drills/{active.pk}/monitor/").json()

        assert payload["statistics"]["total"] == 2
        assert len(payload["participants"]) == 2
        assert "server_time" in payload

    def test_the_csv_export_has_a_row_per_person(self, client_in, active):
        response = client_in.get(f"/api/drills/{active.pk}/report.csv/")

        assert response.status_code == 200
        assert response["Content-Type"] == "text/csv"
        lines = response.content.decode().strip().splitlines()
        assert len(lines) == 3  # header + 2 people
        assert "Employee ID" in lines[0]

    def test_a_drill_needs_at_least_one_location(self, client_in):
        response = client_in.post(
            "/api/drills/",
            data=json.dumps({"name": "Nowhere drill", "locations": []}),
            content_type="application/json",
        )

        assert response.status_code == 400
        assert "at least one location" in str(response.json())


class TestParticipantOverride:
    def test_the_officer_can_reconcile_a_status_by_hand(self, client_in, active, staff):
        participant = active.participants.get(employee=staff)

        response = client_in.post(
            f"/api/participants/{participant.pk}/override/",
            data=json.dumps({"status": "safe", "note": "Reported over radio"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        participant.refresh_from_db()
        assert participant.status == DrillParticipant.Status.SAFE
        assert participant.response_channel == "manual"

    def test_an_invalid_status_is_rejected(self, client_in, active, staff):
        participant = active.participants.get(employee=staff)

        response = client_in.post(
            f"/api/participants/{participant.pk}/override/",
            data=json.dumps({"status": "vibes"}),
            content_type="application/json",
        )

        assert response.status_code == 400


class TestPublicStatusPage:
    def test_an_employee_can_read_their_page_without_signing_in(self, client, active, staff):
        participant = active.participants.get(employee=staff)

        payload = client.get(f"/api/status/{participant.status_token}/").json()

        assert payload["employee_name"] == "Paolo Mendoza"
        assert payload["is_open"] is True
        assert payload["instruction"] == "Use Stairwell B."

    def test_an_employee_can_report_safe_without_signing_in(self, client, active, staff):
        participant = active.participants.get(employee=staff)

        response = client.post(
            f"/api/status/{participant.status_token}/",
            data=json.dumps({"status": "safe"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        participant.refresh_from_db()
        assert participant.status == DrillParticipant.Status.SAFE

    def test_an_unknown_token_is_a_404(self, client):
        assert client.get("/api/status/nope/").status_code == 404


class TestUsgsWebhook:
    FEATURE = {
        "type": "Feature",
        "id": "us7000hook",
        "properties": {"mag": 6.4, "place": "Luzon", "time": 1758290000000},
        "geometry": {"type": "Point", "coordinates": [121.05, 14.60, 30.0]},
    }

    def test_ingests_and_evaluates_a_geojson_feature(self, client, bgc):
        response = client.post(
            "/api/webhooks/usgs/",
            data=json.dumps(self.FEATURE),
            content_type="application/json",
        )

        assert response.status_code == 200
        body = response.json()
        assert body["ingested"] == 1
        assert body["events"][0]["triggered"] is True
        assert body["events"][0]["triggered_locations"] == ["BGC Operations Hub"]
        assert SeismicEvent.objects.count() == 1

    def test_ingests_a_forwarded_ens_email(self, client, bgc):
        response = client.post(
            "/api/webhooks/usgs/",
            data=json.dumps(
                {"email_body": "Magnitude 6.1 Location 14.6N 121.0E Event ID us7000mail"}
            ),
            content_type="application/json",
        )

        assert response.status_code == 200
        assert SeismicEvent.objects.get().source == SeismicEvent.Source.USGS_EMAIL

    def test_a_malformed_payload_is_a_400(self, client):
        response = client.post(
            "/api/webhooks/usgs/",
            data=json.dumps({"nonsense": True}),
            content_type="application/json",
        )

        assert response.status_code == 400

    def test_the_token_is_enforced_when_one_is_configured(self, client, settings):
        settings.USGS_WEBHOOK_TOKEN = "s3cret"

        response = client.post(
            "/api/webhooks/usgs/",
            data=json.dumps(self.FEATURE),
            content_type="application/json",
        )

        assert response.status_code == 403

    def test_a_correct_token_is_accepted(self, client, settings, bgc):
        settings.USGS_WEBHOOK_TOKEN = "s3cret"

        response = client.post(
            "/api/webhooks/usgs/?token=s3cret",
            data=json.dumps(self.FEATURE),
            content_type="application/json",
        )

        assert response.status_code == 200

    def test_the_token_may_travel_in_a_header(self, client, settings, bgc):
        settings.USGS_WEBHOOK_TOKEN = "s3cret"

        response = client.post(
            "/api/webhooks/usgs/",
            data=json.dumps(self.FEATURE),
            content_type="application/json",
            headers={"X-Webhook-Token": "s3cret"},
        )

        assert response.status_code == 200


class TestM360InboundWebhook:
    def test_a_safe_reply_updates_the_dashboard(self, client, active, staff):
        response = client.post(
            "/api/webhooks/m360/",
            data=json.dumps({"msisdn": "639171000100", "message": "SAFE"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        assert response.json()["processed"] is True
        assert active.participants.get(employee=staff).status == DrillParticipant.Status.SAFE

    @pytest.mark.parametrize("sender_key", ["msisdn", "sender", "from", "mobile", "number"])
    def test_accepts_the_field_names_m360_has_used(self, client, active, sender_key):
        response = client.post(
            "/api/webhooks/m360/",
            data=json.dumps({sender_key: "639171000100", "message": "SAFE"}),
            content_type="application/json",
        )

        assert response.json()["processed"] is True

    @pytest.mark.parametrize("text_key", ["message", "content", "text", "body", "sms"])
    def test_accepts_the_body_field_names_m360_has_used(self, client, active, text_key):
        response = client.post(
            "/api/webhooks/m360/",
            data=json.dumps({"msisdn": "639171000100", text_key: "SAFE"}),
            content_type="application/json",
        )

        assert response.json()["processed"] is True

    def test_accepts_a_form_encoded_callback(self, client, active, staff):
        response = client.post(
            "/api/webhooks/m360/", data={"msisdn": "639171000100", "message": "SAFE"}
        )

        assert response.status_code == 200
        assert active.participants.get(employee=staff).status == DrillParticipant.Status.SAFE

    def test_a_payload_with_no_sender_is_a_400(self, client, active):
        response = client.post(
            "/api/webhooks/m360/",
            data=json.dumps({"message": "SAFE"}),
            content_type="application/json",
        )

        assert response.status_code == 400

    def test_an_unmatched_reply_is_accepted_and_kept(self, client, active):
        response = client.post(
            "/api/webhooks/m360/",
            data=json.dumps({"msisdn": "639990000000", "message": "SAFE"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        assert response.json()["processed"] is False
        assert InboundMessage.objects.count() == 1


class TestTextBeeInboundWebhook:
    def test_a_safe_reply_updates_the_dashboard(self, client, active, staff):
        response = client.post(
            "/api/webhooks/textbee/",
            data=json.dumps({"sender": "639171000100", "message": "SAFE"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        assert response.json()["processed"] is True
        assert active.participants.get(employee=staff).status == DrillParticipant.Status.SAFE

    @pytest.mark.parametrize(
        "sender_key", ["sender", "from", "phoneNumber", "phone", "receivedFrom", "number"]
    )
    def test_accepts_the_field_names_textbee_might_use(self, client, active, sender_key):
        response = client.post(
            "/api/webhooks/textbee/",
            data=json.dumps({sender_key: "639171000100", "message": "SAFE"}),
            content_type="application/json",
        )

        assert response.json()["processed"] is True

    @pytest.mark.parametrize("text_key", ["message", "text", "body", "content", "sms"])
    def test_accepts_the_body_field_names_textbee_might_use(self, client, active, text_key):
        response = client.post(
            "/api/webhooks/textbee/",
            data=json.dumps({"sender": "639171000100", text_key: "SAFE"}),
            content_type="application/json",
        )

        assert response.json()["processed"] is True

    def test_a_payload_with_no_sender_is_a_400(self, client, active):
        response = client.post(
            "/api/webhooks/textbee/",
            data=json.dumps({"message": "SAFE"}),
            content_type="application/json",
        )

        assert response.status_code == 400

    def test_an_unmatched_reply_is_accepted_and_kept(self, client, active):
        response = client.post(
            "/api/webhooks/textbee/",
            data=json.dumps({"sender": "639990000000", "message": "SAFE"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        assert response.json()["processed"] is False
        assert InboundMessage.objects.count() == 1

    def test_with_no_token_configured_the_callback_needs_no_credential(self, client, active, staff, settings):
        """TextBee's own webhook config cannot send a header or a token, so
        the default (blank secret) has to work with nothing supplied at all."""
        settings.TEXTBEE_WEBHOOK_TOKEN = ""

        response = client.post(
            "/api/webhooks/textbee/",
            data=json.dumps({"sender": "639171000100", "message": "SAFE"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        assert active.participants.get(employee=staff).status == DrillParticipant.Status.SAFE

    def test_a_configured_token_is_enforced(self, client, active, settings):
        settings.TEXTBEE_WEBHOOK_TOKEN = "s3cret"

        response = client.post(
            "/api/webhooks/textbee/",
            data=json.dumps({"sender": "639171000100", "message": "SAFE"}),
            content_type="application/json",
        )

        assert response.status_code == 403

    def test_the_token_travels_in_the_url_with_no_header_needed(self, client, active, staff, settings):
        """This is the whole point of the design: TextBee cannot set a
        custom header, so the secret has to work from the query string alone."""
        settings.TEXTBEE_WEBHOOK_TOKEN = "s3cret"

        response = client.post(
            "/api/webhooks/textbee/?token=s3cret",
            data=json.dumps({"sender": "639171000100", "message": "SAFE"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        assert active.participants.get(employee=staff).status == DrillParticipant.Status.SAFE


class TestPubSubPushWebhook:
    def _envelope(self, notification_id):
        data = base64.b64encode(
            json.dumps({"notification_id": notification_id}).encode()
        ).decode()
        return {"message": {"data": data, "messageId": "1"}}

    @pytest.fixture
    def queued(self, drill):
        drill.send_email = False
        drill.save()
        build_roster(drill)
        from engine.services import queue_notifications

        participants = list(drill.participants.select_related("employee__location"))
        return queue_notifications(drill, participants)

    def test_delivers_the_referenced_notification(self, client, queued):
        response = client.post(
            "/api/webhooks/pubsub/",
            data=json.dumps(self._envelope(queued[0].pk)),
            content_type="application/json",
        )

        assert response.status_code == 200
        queued[0].refresh_from_db()
        assert queued[0].status == Notification.Status.SENT

    def test_a_redelivered_message_does_not_send_twice(self, client, queued):
        payload = json.dumps(self._envelope(queued[0].pk))
        client.post("/api/webhooks/pubsub/", data=payload, content_type="application/json")
        client.post("/api/webhooks/pubsub/", data=payload, content_type="application/json")

        queued[0].refresh_from_db()
        assert queued[0].attempts == 1

    def test_an_unreadable_envelope_is_acked_not_retried(self, client):
        """Returning non-2xx would make Pub/Sub redeliver poison forever."""
        response = client.post(
            "/api/webhooks/pubsub/",
            data=json.dumps({"message": {"data": "not-base64-json"}}),
            content_type="application/json",
        )

        assert response.status_code == 200

    def test_an_envelope_with_no_data_is_acked(self, client):
        response = client.post(
            "/api/webhooks/pubsub/",
            data=json.dumps({"message": {}}),
            content_type="application/json",
        )

        assert response.status_code == 200

    def test_the_push_token_is_enforced(self, client, settings, queued):
        settings.PUBSUB_PUSH_TOKEN = "push-secret"

        response = client.post(
            "/api/webhooks/pubsub/",
            data=json.dumps(self._envelope(queued[0].pk)),
            content_type="application/json",
        )

        assert response.status_code == 403


class TestSimulationAndHealth:
    def test_simulating_an_event_runs_the_trigger_filter(self, client_in, bgc):
        response = client_in.post(
            "/api/events/simulate/",
            data=json.dumps(
                {"magnitude": 6.5, "latitude": 14.6, "longitude": 121.05, "auto_trigger": False}
            ),
            content_type="application/json",
        )

        assert response.status_code == 201
        body = response.json()
        assert body["matches"][0]["triggered"] is True
        assert body["drill"] is None

    def test_simulating_with_auto_trigger_activates_a_drill(self, client_in, bgc, staff, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            response = client_in.post(
                "/api/events/simulate/",
                data=json.dumps(
                    {"magnitude": 6.5, "latitude": 14.6, "longitude": 121.05, "auto_trigger": True}
                ),
                content_type="application/json",
            )

        assert response.json()["drill"]["status"] == Drill.Status.ACTIVE

    def test_an_out_of_range_magnitude_is_rejected(self, client_in):
        response = client_in.post(
            "/api/events/simulate/",
            data=json.dumps({"magnitude": 42, "latitude": 14.6, "longitude": 121.05}),
            content_type="application/json",
        )

        assert response.status_code == 400

    def test_the_health_endpoint_reports_every_subsystem(self, client_in):
        payload = client_in.get("/api/health/").json()

        assert payload["ok"] is True
        assert payload["database"]["ok"] is True

    def test_the_liveness_probe_needs_no_authentication(self, client):
        response = client.get("/api/healthz/")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"


class TestSpaFallback:
    def test_an_unknown_path_is_handed_to_the_vue_router(self, client):
        response = client.get("/drills/7")

        # 200 with the built shell, or 501 when the bundle has not been built.
        assert response.status_code in (200, 501)

    def test_an_unknown_api_path_is_a_404_not_the_spa_shell(self, client):
        assert client.get("/api/does-not-exist/").status_code == 404
