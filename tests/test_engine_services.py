"""Unit tests for engine.services - the call tree orchestration layer."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import Drill, DrillParticipant, Employee, Notification
from engine.services import (
    CallTreeError,
    activate_drill,
    build_roster,
    complete_drill,
    drill_statistics,
    escalate_to_emt,
    evaluate_seismic_event,
    interpret_reply,
    queue_notifications,
    record_response,
    sweep_drill,
)

pytestmark = pytest.mark.django_db


class TestBuildRoster:
    def test_includes_every_active_employee_at_a_targeted_location(self, drill):
        created = build_roster(drill)

        assert created == 2
        assert drill.participants.count() == 2

    def test_excludes_inactive_employees(self, drill, staff):
        staff.is_active = False
        staff.save()

        build_roster(drill)

        assert drill.participants.count() == 1

    def test_excludes_employees_at_untargeted_locations(self, drill, cebu, bgc):
        Employee.objects.create(
            employee_id="DSO-900",
            full_name="Off Site",
            mobile_number="+639179999999",
            location=cebu,
        )

        build_roster(drill)

        assert drill.participants.count() == 2
        assert not drill.participants.filter(employee__location=cebu).exists()

    def test_is_idempotent(self, drill):
        build_roster(drill)
        second_pass = build_roster(drill)

        assert second_pass == 0
        assert drill.participants.count() == 2

    def test_adds_only_the_missing_people_on_a_re_run(self, drill, bgc):
        build_roster(drill)
        Employee.objects.create(
            employee_id="DSO-101",
            full_name="Late Joiner",
            mobile_number="+639171000101",
            location=bgc,
        )

        added = build_roster(drill)

        assert added == 1
        assert drill.participants.count() == 3

    def test_a_drill_with_no_locations_is_rejected(self, drill):
        drill.locations.clear()

        with pytest.raises(CallTreeError, match="no target locations"):
            build_roster(drill)

    def test_every_participant_gets_a_distinct_status_token(self, drill):
        build_roster(drill)

        tokens = list(drill.participants.values_list("status_token", flat=True))

        assert len(tokens) == len(set(tokens))
        assert all(len(token) > 20 for token in tokens)


class TestQueueNotifications:
    def test_queues_one_row_per_channel(self, drill):
        build_roster(drill)
        participants = list(drill.participants.select_related("employee__location"))

        queued = queue_notifications(drill, participants)

        assert len(queued) == 4  # 2 people x SMS + email
        assert Notification.objects.filter(status=Notification.Status.QUEUED).count() == 4

    def test_respects_the_channel_switches(self, drill):
        drill.send_email = False
        drill.save()
        build_roster(drill)
        participants = list(drill.participants.select_related("employee__location"))

        queued = queue_notifications(drill, participants)

        assert len(queued) == 2
        assert {n.channel for n in queued} == {Notification.Channel.SMS}

    def test_missing_address_produces_a_skipped_row_not_silence(self, drill, staff):
        """The audit trail has to show why someone was never reached."""
        staff.email = ""
        staff.save()
        build_roster(drill)
        participants = list(
            drill.participants.select_related("employee__location").filter(employee=staff)
        )

        queued = queue_notifications(drill, participants)

        assert len(queued) == 1  # SMS only
        skipped = Notification.objects.get(status=Notification.Status.SKIPPED)
        assert skipped.channel == Notification.Channel.EMAIL
        assert "No email address" in skipped.error_message

    def test_opt_out_is_honoured(self, drill, staff):
        staff.sms_opt_in = False
        staff.save()
        build_roster(drill)
        participants = list(
            drill.participants.select_related("employee__location").filter(employee=staff)
        )

        queue_notifications(drill, participants)

        skipped = Notification.objects.get(status=Notification.Status.SKIPPED)
        assert skipped.channel == Notification.Channel.SMS

    def test_sms_body_is_clipped_to_the_segment_limit(self, drill, settings):
        settings.SMS_MAX_LENGTH = 40
        drill.message_template = "y" * 500
        drill.save()
        build_roster(drill)
        participants = list(drill.participants.select_related("employee__location"))

        queue_notifications(drill, participants)

        sms = Notification.objects.filter(channel=Notification.Channel.SMS).first()
        email = Notification.objects.filter(channel=Notification.Channel.EMAIL).first()
        assert len(sms.body) == 40
        assert len(email.body) == 500  # email is not clipped


class TestActivateDrill:
    def test_activates_and_queues_the_broadcast(self, drill, officer, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            result = activate_drill(drill, actor=officer)

        drill.refresh_from_db()
        assert drill.status == Drill.Status.ACTIVE
        assert drill.started_at is not None
        assert drill.initiated_by == officer
        assert result.participants == 2
        assert result.notifications_queued == 4

    def test_dispatch_is_deferred_until_the_transaction_commits(self, drill, django_capture_on_commit_callbacks):
        """A rolled-back activation must never leak a real SMS."""
        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            activate_drill(drill)

        assert len(callbacks) == 1
        assert Notification.objects.filter(status=Notification.Status.SENT).count() == 0

    def test_messages_are_actually_sent_once_committed(self, drill, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            activate_drill(drill)

        assert Notification.objects.filter(status=Notification.Status.SENT).count() == 4

    def test_participants_move_to_notified(self, drill, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            activate_drill(drill)

        statuses = set(drill.participants.values_list("status", flat=True))
        assert statuses == {DrillParticipant.Status.NOTIFIED}
        assert all(p.notified_at for p in drill.participants.all())

    def test_the_deadline_follows_the_response_window(self, drill, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            activate_drill(drill)

        drill.refresh_from_db()
        assert drill.deadline == drill.started_at + timedelta(minutes=5)
        assert drill.is_window_open

    def test_cannot_activate_twice(self, drill, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            activate_drill(drill)

        with pytest.raises(CallTreeError, match="already active"):
            activate_drill(drill)

    def test_cannot_reactivate_a_completed_drill(self, drill):
        drill.status = Drill.Status.COMPLETED
        drill.save()

        with pytest.raises(CallTreeError, match="cannot be re-activated"):
            activate_drill(drill)


class TestInterpretReply:
    @pytest.mark.parametrize(
        "text",
        ["SAFE", "safe", " Safe ", "safe.", "I am safe", "ok", "Ligtas na ako"],
    )
    def test_recognises_safe(self, text):
        assert interpret_reply(text) == DrillParticipant.Status.SAFE

    @pytest.mark.parametrize(
        "text", ["HELP", "help", "sos", "Need help!", "tulong", "I am injured, help"]
    )
    def test_recognises_help(self, text):
        assert interpret_reply(text) == DrillParticipant.Status.HELP

    def test_help_outranks_safe_when_both_appear(self):
        """'not safe, need help' must never be read as an all-clear."""
        assert interpret_reply("not safe, need help") == DrillParticipant.Status.HELP

    @pytest.mark.parametrize("text", ["", None, "what is going on", "stop", "12345"])
    def test_unrecognised_text_returns_none(self, text):
        assert interpret_reply(text) is None


class TestRecordResponse:
    @pytest.fixture
    def participant(self, drill, staff):
        build_roster(drill)
        participant = drill.participants.get(employee=staff)
        participant.status = DrillParticipant.Status.NOTIFIED
        participant.notified_at = timezone.now() - timedelta(seconds=45)
        participant.save()
        return participant

    def test_records_safe_and_stamps_the_latency(self, participant):
        record_response(participant, DrillParticipant.Status.SAFE, channel="sms", text="SAFE")

        participant.refresh_from_db()
        assert participant.status == DrillParticipant.Status.SAFE
        assert participant.responded_at is not None
        assert participant.response_channel == "sms"
        assert 44 <= participant.response_latency_seconds <= 50

    def test_records_help(self, participant):
        record_response(participant, DrillParticipant.Status.HELP, channel="web")

        participant.refresh_from_db()
        assert participant.status == DrillParticipant.Status.HELP

    def test_safe_after_help_is_ignored(self, participant):
        """A duplicate reply must not quietly cancel a rescue."""
        record_response(participant, DrillParticipant.Status.HELP)
        first_responded_at = DrillParticipant.objects.get(pk=participant.pk).responded_at

        record_response(participant, DrillParticipant.Status.SAFE)

        participant.refresh_from_db()
        assert participant.status == DrillParticipant.Status.HELP
        assert participant.responded_at == first_responded_at

    def test_help_after_safe_is_an_upgrade_that_sticks(self, participant):
        record_response(participant, DrillParticipant.Status.SAFE)
        record_response(participant, DrillParticipant.Status.HELP)

        participant.refresh_from_db()
        assert participant.status == DrillParticipant.Status.HELP

    def test_the_first_response_timestamp_wins(self, participant):
        record_response(participant, DrillParticipant.Status.SAFE)
        original = DrillParticipant.objects.get(pk=participant.pk).responded_at

        record_response(participant, DrillParticipant.Status.HELP)

        participant.refresh_from_db()
        assert participant.responded_at == original

    def test_rejects_a_status_that_is_not_a_response(self, participant):
        with pytest.raises(CallTreeError, match="Unsupported response status"):
            record_response(participant, DrillParticipant.Status.PENDING)

    def test_latency_is_none_before_the_alert_lands(self, drill, staff):
        build_roster(drill)
        participant = drill.participants.get(employee=staff)

        record_response(participant, DrillParticipant.Status.SAFE)

        assert participant.response_latency_seconds is None


class TestSweepDrill:
    @pytest.fixture
    def active(self, drill, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            activate_drill(drill)
        drill.refresh_from_db()
        return drill

    def test_does_nothing_for_a_draft_drill(self, drill):
        result = sweep_drill(drill)

        assert (result.reminders, result.flagged, result.escalated) == (0, 0, 0)

    def test_no_reminder_before_the_interval_elapses(self, active):
        result = sweep_drill(active)

        assert result.reminders == 0

    def test_reminds_non_responders_after_the_interval(self, active):
        later = timezone.now() + timedelta(minutes=3)

        result = sweep_drill(active, now=later)

        assert result.reminders == 2
        assert Notification.objects.filter(
            purpose=Notification.Purpose.REMINDER
        ).count() == 4

    def test_does_not_remind_people_who_already_replied(self, active, staff):
        participant = active.participants.get(employee=staff)
        record_response(participant, DrillParticipant.Status.SAFE)

        result = sweep_drill(active, now=timezone.now() + timedelta(minutes=3))

        assert result.reminders == 1

    def test_reminders_stop_at_the_configured_maximum(self, active):
        active.participants.update(reminders_sent=2)  # max_reminders is 2

        result = sweep_drill(active, now=timezone.now() + timedelta(minutes=3))

        assert result.reminders == 0

    def test_flags_non_responders_past_the_deadline(self, active):
        past_deadline = timezone.now() + timedelta(minutes=6)

        result = sweep_drill(active, now=past_deadline)

        assert result.flagged == 2
        assert active.participants.filter(
            status=DrillParticipant.Status.NON_COMPLIANT
        ).count() == 2

    def test_no_reminders_are_sent_past_the_deadline(self, active):
        result = sweep_drill(active, now=timezone.now() + timedelta(minutes=6))

        assert result.reminders == 0

    def test_escalates_to_the_emt_once_past_the_deadline(self, active):
        result = sweep_drill(active, now=timezone.now() + timedelta(minutes=6))

        assert result.escalated > 0
        escalations = Notification.objects.filter(
            purpose=Notification.Purpose.ESCALATION
        )
        assert escalations.exists()
        assert "unaccounted for" in escalations.first().body

    def test_a_second_sweep_does_not_escalate_the_same_people_again(self, active):
        past_deadline = timezone.now() + timedelta(minutes=6)
        sweep_drill(active, now=past_deadline)

        result = sweep_drill(active, now=past_deadline + timedelta(minutes=1))

        assert result.flagged == 0
        assert result.escalated == 0

    def test_someone_who_replied_is_never_flagged(self, active, staff):
        record_response(active.participants.get(employee=staff), DrillParticipant.Status.SAFE)

        sweep_drill(active, now=timezone.now() + timedelta(minutes=6))

        assert active.participants.get(employee=staff).status == DrillParticipant.Status.SAFE


class TestEscalateToEmt:
    def test_returns_zero_when_nobody_is_missing(self, drill):
        build_roster(drill)

        assert escalate_to_emt(drill, []) == 0

    def test_returns_zero_when_the_drill_has_no_emt_member(self, drill, emt):
        build_roster(drill)
        drill.participants.filter(employee=emt).delete()
        missing = list(drill.participants.all())

        assert escalate_to_emt(drill, missing) == 0

    def test_lists_the_missing_names_in_the_escalation(self, drill, staff):
        build_roster(drill)
        missing = list(drill.participants.filter(employee=staff))

        escalate_to_emt(drill, missing)

        body = Notification.objects.filter(
            purpose=Notification.Purpose.ESCALATION
        ).first().body
        assert "Paolo Mendoza" in body

    def test_long_lists_are_summarised(self, drill, bgc, emt):
        for index in range(15):
            Employee.objects.create(
                employee_id=f"DSO-2{index:02d}",
                full_name=f"Person {index}",
                mobile_number=f"+6391720000{index:02d}",
                location=bgc,
            )
        build_roster(drill)
        missing = list(drill.participants.exclude(employee=emt))

        escalate_to_emt(drill, missing)

        body = Notification.objects.filter(
            purpose=Notification.Purpose.ESCALATION
        ).first().body
        assert "+6 more" in body


class TestCompleteDrill:
    def test_closes_the_drill(self, drill, officer, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            activate_drill(drill)

        complete_drill(drill, actor=officer)

        drill.refresh_from_db()
        assert drill.status == Drill.Status.COMPLETED
        assert drill.completed_at is not None

    def test_outstanding_people_are_marked_non_compliant(self, drill, staff, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            activate_drill(drill)
        record_response(drill.participants.get(employee=staff), DrillParticipant.Status.SAFE)

        complete_drill(drill)

        assert drill.participants.filter(status=DrillParticipant.Status.SAFE).count() == 1
        assert drill.participants.filter(
            status=DrillParticipant.Status.NON_COMPLIANT
        ).count() == 1

    def test_only_an_active_drill_can_be_completed(self, drill):
        with pytest.raises(CallTreeError, match="Only an active drill"):
            complete_drill(drill)


class TestDrillStatistics:
    def test_empty_drill_reports_zeroes_without_dividing_by_zero(self, drill):
        stats = drill_statistics(drill)

        assert stats["total"] == 0
        assert stats["response_rate"] == 0.0
        assert stats["avg_response_seconds"] is None

    def test_counts_and_rates(self, drill, staff, emt, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            activate_drill(drill)
        record_response(drill.participants.get(employee=staff), DrillParticipant.Status.SAFE)
        record_response(drill.participants.get(employee=emt), DrillParticipant.Status.HELP)

        stats = drill_statistics(drill)

        assert stats["total"] == 2
        assert stats["safe"] == 1
        assert stats["help"] == 1
        assert stats["responded"] == 2
        assert stats["pending"] == 0
        assert stats["response_rate"] == 100.0

    def test_partial_response_rate_is_rounded_to_one_decimal(self, drill, bgc, staff, django_capture_on_commit_callbacks):
        Employee.objects.create(
            employee_id="DSO-300",
            full_name="Third Person",
            mobile_number="+639173000000",
            location=bgc,
        )
        with django_capture_on_commit_callbacks(execute=True):
            activate_drill(drill)
        record_response(drill.participants.get(employee=staff), DrillParticipant.Status.SAFE)

        stats = drill_statistics(drill)

        assert stats["total"] == 3
        assert stats["response_rate"] == 33.3

    def test_reports_response_latency_spread(self, drill, staff, emt, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            activate_drill(drill)

        now = timezone.now()
        for participant, seconds in (
            (drill.participants.get(employee=staff), 20),
            (drill.participants.get(employee=emt), 80),
        ):
            participant.notified_at = now - timedelta(seconds=seconds)
            participant.responded_at = now
            participant.status = DrillParticipant.Status.SAFE
            participant.save()

        stats = drill_statistics(drill)

        assert stats["fastest_response_seconds"] == pytest.approx(20, abs=1)
        assert stats["slowest_response_seconds"] == pytest.approx(80, abs=1)
        assert stats["avg_response_seconds"] == pytest.approx(50, abs=1)

    def test_measures_dispatch_latency_for_the_delivery_test(self, drill, django_capture_on_commit_callbacks):
        """Testing Procedure: trigger action to gateway acceptance."""
        with django_capture_on_commit_callbacks(execute=True):
            activate_drill(drill)

        stats = drill_statistics(drill)

        assert stats["avg_dispatch_latency_seconds"] is not None
        assert stats["avg_dispatch_latency_seconds"] >= 0


class TestEvaluateSeismicEvent:
    def test_marks_the_event_evaluated(self, manila_event, bgc):
        matches, created_drill = evaluate_seismic_event(manila_event)

        manila_event.refresh_from_db()
        assert manila_event.evaluated_at is not None
        assert manila_event.triggered
        assert created_drill is None  # auto-trigger off
        assert len(matches) == 1

    def test_records_why_nothing_matched(self, manila_event, cebu):
        evaluate_seismic_event(manila_event)

        manila_event.refresh_from_db()
        assert not manila_event.triggered
        assert "no location met the Trigger Filter" in manila_event.evaluation_note

    def test_auto_trigger_creates_and_activates_a_drill(self, manila_event, bgc, staff, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            _, created_drill = evaluate_seismic_event(manila_event, auto_trigger=True)

        assert created_drill is not None
        assert created_drill.status == Drill.Status.ACTIVE
        assert created_drill.trigger_mode == Drill.TriggerMode.AUTOMATIC
        assert created_drill.kind == Drill.Kind.INCIDENT
        assert created_drill.seismic_event == manila_event
        assert list(created_drill.locations.all()) == [bgc]

    def test_auto_trigger_scopes_the_drill_to_the_matching_sites_only(self, manila_event, bgc, cebu, staff, django_capture_on_commit_callbacks):
        Employee.objects.create(
            employee_id="DSO-800",
            full_name="Cebu Person",
            mobile_number="+639178000000",
            location=cebu,
        )

        with django_capture_on_commit_callbacks(execute=True):
            _, created_drill = evaluate_seismic_event(manila_event, auto_trigger=True)

        assert list(created_drill.locations.all()) == [bgc]
        assert not created_drill.participants.filter(employee__location=cebu).exists()

    def test_no_drill_when_nothing_trips_the_filter(self, manila_event, bgc):
        manila_event.magnitude = 2.0
        manila_event.save()

        _, created_drill = evaluate_seismic_event(manila_event, auto_trigger=True)

        assert created_drill is None
        assert Drill.objects.count() == 0

    def test_inactive_locations_are_out_of_scope(self, manila_event, bgc):
        bgc.is_active = False
        bgc.save()

        matches, _ = evaluate_seismic_event(manila_event, auto_trigger=True)

        assert matches == []
        assert Drill.objects.count() == 0

    def test_the_settings_flag_is_the_default(self, manila_event, bgc, staff, settings, django_capture_on_commit_callbacks):
        settings.AUTO_TRIGGER_ENABLED = True

        with django_capture_on_commit_callbacks(execute=True):
            _, created_drill = evaluate_seismic_event(manila_event)

        assert created_drill is not None
