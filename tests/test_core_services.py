"""Unit tests for core.services: phone normalisation, templating, auditing."""

from __future__ import annotations

import pytest

from core.models import AuditLog, DrillParticipant
from core.services import (
    DEFAULT_TEMPLATE,
    audit,
    normalize_msisdn,
    render_message,
    truncate_sms,
)


class TestNormalizeMsisdn:
    """M360 needs bare E.164 digits; staff type numbers every other way."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("+639171234567", "639171234567"),
            ("639171234567", "639171234567"),
            ("09171234567", "639171234567"),
            ("9171234567", "639171234567"),
            ("+63 917 123 4567", "639171234567"),
            ("0917-123-4567", "639171234567"),
            ("(0917) 123 4567", "639171234567"),
            ("0063 917 123 4567", "639171234567"),
        ],
    )
    def test_accepts_every_local_spelling(self, raw, expected):
        assert normalize_msisdn(raw) == expected

    @pytest.mark.parametrize("raw", ["", None, "not a number", "n/a", "--"])
    def test_unusable_input_returns_empty(self, raw):
        assert normalize_msisdn(raw) == ""

    def test_all_spellings_of_one_number_collapse(self):
        """Inbound SMS matching depends on this: one number, one key."""
        spellings = ["+639171234567", "09171234567", "9171234567", "63 917 123 4567"]
        assert len({normalize_msisdn(s) for s in spellings}) == 1

    def test_country_code_is_configurable(self):
        assert normalize_msisdn("0412345678", default_country_code="61") == "61412345678"


@pytest.mark.django_db
class TestRenderMessage:
    def test_fills_every_placeholder(self, drill, staff, manila_event):
        drill.seismic_event = manila_event
        drill.save()
        participant = DrillParticipant.objects.create(drill=drill, employee=staff)

        body = render_message(DEFAULT_TEMPLATE, participant=participant, event=manila_event)

        assert "Paolo" in body                      # {name} is the first name
        assert "6.2" in body                        # {magnitude}, one decimal
        assert "14 km E of Manila" in body          # {place}
        assert "Use Stairwell B." in body           # {instruction}
        assert participant.status_token in body     # {link}
        assert "{" not in body                      # nothing left unfilled

    def test_falls_back_to_location_when_there_is_no_event(self, drill, staff):
        participant = DrillParticipant.objects.create(drill=drill, employee=staff)

        body = render_message(DEFAULT_TEMPLATE, participant=participant, event=None)

        assert "N/A" in body
        assert "BGC Operations Hub" in body

    def test_unknown_placeholder_does_not_block_the_broadcast(self, drill, staff):
        """A typo in an operator template must not stop an alert going out."""
        participant = DrillParticipant.objects.create(drill=drill, employee=staff)

        body = render_message("Hi {nmae}, evacuate now", participant=participant)

        assert body == "Hi {nmae}, evacuate now"

    def test_uses_the_default_instruction_when_the_site_has_none(self, cebu, drill, staff):
        staff.location = cebu
        staff.save()
        participant = DrillParticipant.objects.create(drill=drill, employee=staff)

        body = render_message("{instruction}", participant=participant)

        assert "Drop, Cover and Hold" in body

    def test_status_url_uses_the_public_base_url(self, drill, staff):
        participant = DrillParticipant.objects.create(drill=drill, employee=staff)

        assert participant.status_url == (
            f"https://calltree.example.com/status/{participant.status_token}"
        )


class TestTruncateSms:
    def test_short_body_is_untouched(self, settings):
        settings.SMS_MAX_LENGTH = 50
        assert truncate_sms("evacuate now") == "evacuate now"

    def test_long_body_is_clipped_with_an_ellipsis(self, settings):
        settings.SMS_MAX_LENGTH = 20
        result = truncate_sms("x" * 100)
        assert len(result) == 20
        assert result.endswith("...")


@pytest.mark.django_db
class TestAudit:
    def test_records_the_action_and_detail(self, officer):
        entry = audit("drill.activated", actor=officer, target="drill:7", participants=12)

        assert entry.action == "drill.activated"
        assert entry.actor == officer
        assert entry.detail == {"participants": 12}
        assert AuditLog.objects.count() == 1

    def test_anonymous_actor_is_stored_as_none(self):
        class Anon:
            is_authenticated = False

        entry = audit("auth.failed", actor=Anon(), target="ghost")

        assert entry.actor is None

    def test_long_target_is_truncated_rather_than_raising(self):
        entry = audit("test", target="x" * 500)

        assert len(entry.target) == 160
