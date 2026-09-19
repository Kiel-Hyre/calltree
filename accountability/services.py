"""Accountability & Feedback Module.

Closes the two-way loop: an inbound SMS from M360, or a tap on the unique web
status link, resolves to one DrillParticipant and updates their safety status.
"""

from __future__ import annotations

import logging

from django.db.models import Q
from django.utils import timezone

from core.models import Drill, DrillParticipant, Employee, InboundMessage
from core.services import normalize_msisdn
from engine.services import interpret_reply, record_response

logger = logging.getLogger(__name__)


class ResolutionError(Exception):
    """The inbound message could not be tied to an open drill participant."""


def find_participant_by_number(msisdn: str) -> DrillParticipant:
    """Resolve a sender's number to their participant row in the newest drill.

    A member can only be in one active drill at a time in practice; if there
    are several, the most recently started one wins, because that is the event
    the person is replying to.
    """
    number = normalize_msisdn(msisdn)
    if not number:
        raise ResolutionError(f"Unusable sender number: {msisdn!r}")

    # Numbers are stored as entered, so match on the normalised suffix.
    suffix = number[-10:]
    employees = [
        employee
        for employee in Employee.objects.filter(
            Q(mobile_number__contains=suffix) | Q(mobile_number__endswith=suffix),
            is_active=True,
        )
        if normalize_msisdn(employee.mobile_number) == number
    ]
    if not employees:
        raise ResolutionError(f"No active employee has the number {number}.")

    participant = (
        DrillParticipant.objects.select_related("employee", "drill")
        .filter(employee__in=employees, drill__status=Drill.Status.ACTIVE)
        .order_by("-drill__started_at")
        .first()
    )
    if participant is None:
        raise ResolutionError(
            f"{employees[0].full_name} is not part of any active drill."
        )
    return participant


def handle_inbound_sms(sender: str, text: str, raw_payload: dict | None = None) -> InboundMessage:
    """Store and interpret one inbound SMS.

    The raw message is always persisted first: a reply that cannot be matched
    is still evidence that the employee tried to respond, and the Safety
    Officer can reconcile it by hand from the dashboard.
    """
    message = InboundMessage.objects.create(
        sender=sender or "",
        text=(text or "")[:500],
        received_at=timezone.now(),
        raw_payload=raw_payload or {},
    )

    try:
        participant = find_participant_by_number(sender)
    except ResolutionError as exc:
        message.process_note = str(exc)[:255]
        message.save(update_fields=["process_note", "updated_at"])
        logger.info("Unmatched inbound SMS from %s: %s", sender, exc)
        return message

    message.participant = participant

    status = interpret_reply(text)
    if status is None:
        message.process_note = "No SAFE/HELP keyword recognised."
        message.save(update_fields=["participant", "process_note", "updated_at"])
        return message

    record_response(participant, status, channel="sms", text=text)
    message.processed = True
    message.process_note = f"Recorded as {status}."
    message.save(
        update_fields=["participant", "processed", "process_note", "updated_at"]
    )
    return message


def resolve_status_token(token: str) -> DrillParticipant:
    """Look up a participant by their one-tap web status link token."""
    participant = (
        DrillParticipant.objects.select_related(
            "employee", "employee__location", "drill", "drill__seismic_event"
        )
        .filter(status_token=token)
        .first()
    )
    if participant is None:
        raise ResolutionError("This status link is not valid.")
    return participant


def handle_web_response(token: str, status: str) -> DrillParticipant:
    """Record a SAFE/HELP tap from the employee's web status page."""
    participant = resolve_status_token(token)

    if participant.drill.status != Drill.Status.ACTIVE:
        raise ResolutionError("This drill is already closed.")

    normalised = (status or "").strip().lower()
    mapping = {
        "safe": DrillParticipant.Status.SAFE,
        "help": DrillParticipant.Status.HELP,
    }
    if normalised not in mapping:
        raise ResolutionError("Choose either SAFE or HELP.")

    return record_response(
        participant, mapping[normalised], channel="web", text=normalised.upper()
    )
