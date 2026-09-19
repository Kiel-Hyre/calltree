"""Unit tests for the Accountability & Feedback Module (the two-way loop)."""

from __future__ import annotations

import pytest

from accountability.services import (
    ResolutionError,
    find_participant_by_number,
    handle_inbound_sms,
    handle_web_response,
    resolve_status_token,
)
from core.models import Drill, DrillParticipant, InboundMessage
from engine.services import activate_drill

pytestmark = pytest.mark.django_db


@pytest.fixture
def active(drill, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        activate_drill(drill)
    drill.refresh_from_db()
    return drill


class TestFindParticipantByNumber:
    def test_matches_the_number_as_stored(self, active, staff):
        participant = find_participant_by_number("+639171000100")

        assert participant.employee == staff

    @pytest.mark.parametrize(
        "spelling", ["+639171000100", "639171000100", "09171000100", "9171000100"]
    )
    def test_matches_any_spelling_the_gateway_reports(self, active, staff, spelling):
        assert find_participant_by_number(spelling).employee == staff

    def test_matches_when_the_directory_holds_the_local_format(self, active, emt):
        """The EMT fixture is stored as 09171000001, not +63…"""
        assert find_participant_by_number("+639171000001").employee == emt

    def test_unknown_number_is_reported(self, active):
        with pytest.raises(ResolutionError, match="No active employee"):
            find_participant_by_number("+639998887777")

    def test_unusable_number_is_reported(self, active):
        with pytest.raises(ResolutionError, match="Unusable sender number"):
            find_participant_by_number("garbage")

    def test_a_known_person_with_no_active_drill_is_reported(self, drill, staff):
        with pytest.raises(ResolutionError, match="not part of any active drill"):
            find_participant_by_number("+639171000100")

    def test_a_similar_suffix_does_not_produce_a_false_match(self, active, bgc):
        """Suffix matching narrows the query; normalisation must confirm it."""
        with pytest.raises(ResolutionError, match="No active employee"):
            find_participant_by_number("+639179171000100"[:13])


class TestHandleInboundSms:
    def test_a_safe_reply_is_recorded(self, active, staff):
        message = handle_inbound_sms("+639171000100", "SAFE")

        assert message.processed
        participant = active.participants.get(employee=staff)
        assert participant.status == DrillParticipant.Status.SAFE
        assert participant.response_channel == "sms"
        assert participant.response_text == "SAFE"

    def test_a_help_reply_is_recorded(self, active, staff):
        handle_inbound_sms("09171000100", "help! im trapped")

        assert active.participants.get(employee=staff).status == DrillParticipant.Status.HELP

    def test_the_raw_message_is_always_stored(self, active):
        handle_inbound_sms("+639171000100", "SAFE", raw_payload={"provider": "m360"})

        stored = InboundMessage.objects.get()
        assert stored.sender == "+639171000100"
        assert stored.text == "SAFE"
        assert stored.raw_payload == {"provider": "m360"}

    def test_an_unmatched_sender_is_kept_for_reconciliation(self, active):
        """A reply from an unknown number is evidence, not noise."""
        message = handle_inbound_sms("+639998887777", "SAFE")

        assert not message.processed
        assert message.participant is None
        assert "No active employee" in message.process_note
        assert InboundMessage.objects.count() == 1

    def test_an_unrecognised_keyword_is_linked_but_left_unprocessed(self, active, staff):
        message = handle_inbound_sms("+639171000100", "who is this")

        assert not message.processed
        assert message.participant is not None
        assert "No SAFE/HELP keyword" in message.process_note
        assert active.participants.get(employee=staff).status != DrillParticipant.Status.SAFE

    def test_an_empty_body_does_not_crash(self, active):
        message = handle_inbound_sms("+639171000100", "")

        assert not message.processed

    def test_a_long_message_is_truncated_to_fit(self, active):
        message = handle_inbound_sms("+639171000100", "x" * 900)

        assert len(message.text) == 500


class TestResolveStatusToken:
    def test_finds_the_participant(self, active, staff):
        participant = active.participants.get(employee=staff)

        assert resolve_status_token(participant.status_token) == participant

    def test_an_unknown_token_is_reported(self, active):
        with pytest.raises(ResolutionError, match="not valid"):
            resolve_status_token("nope")


class TestHandleWebResponse:
    def test_records_safe_from_the_web_link(self, active, staff):
        participant = active.participants.get(employee=staff)

        handle_web_response(participant.status_token, "safe")

        participant.refresh_from_db()
        assert participant.status == DrillParticipant.Status.SAFE
        assert participant.response_channel == "web"

    def test_records_help_from_the_web_link(self, active, staff):
        participant = active.participants.get(employee=staff)

        handle_web_response(participant.status_token, "HELP")

        participant.refresh_from_db()
        assert participant.status == DrillParticipant.Status.HELP

    def test_an_invalid_choice_is_rejected(self, active, staff):
        participant = active.participants.get(employee=staff)

        with pytest.raises(ResolutionError, match="SAFE or HELP"):
            handle_web_response(participant.status_token, "maybe")

    def test_a_closed_drill_will_not_accept_a_response(self, active, staff):
        participant = active.participants.get(employee=staff)
        active.status = Drill.Status.COMPLETED
        active.save()

        with pytest.raises(ResolutionError, match="already closed"):
            handle_web_response(participant.status_token, "safe")

    def test_an_unknown_token_is_rejected(self, active):
        with pytest.raises(ResolutionError, match="not valid"):
            handle_web_response("nope", "safe")

    def test_a_web_safe_cannot_undo_an_sms_help(self, active, staff):
        participant = active.participants.get(employee=staff)
        handle_inbound_sms("+639171000100", "HELP")

        handle_web_response(participant.status_token, "safe")

        participant.refresh_from_db()
        assert participant.status == DrillParticipant.Status.HELP
