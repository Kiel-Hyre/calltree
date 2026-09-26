"""Call Tree orchestration - the service layer the API and workers both call.

Responsibilities:
  * build the participant roster for a drill from the geofenced directory
  * activate the call tree (queue the initial broadcast)
  * record an employee's SAFE/HELP response and halt their reminder loop
  * sweep for non-responders: reminders, then EMT escalation
  * compute the compliance statistics the dashboard and CSV export read
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from core import datastore
from core.models import Drill, DrillParticipant, Employee, Notification, SeismicEvent
from core.services import (
    DEFAULT_SMS_TEMPLATE,
    DEFAULT_TEMPLATE,
    audit,
    render_message,
    truncate_sms,
)
from engine.proximity import evaluate_event, triggered_locations

logger = logging.getLogger(__name__)


class CallTreeError(Exception):
    """Raised when an operation is invalid for the drill's current state."""


@dataclass
class ActivationResult:
    drill: Drill
    participants: int
    notifications_queued: int


# ---------------------------------------------------------------------------
# Roster construction
# ---------------------------------------------------------------------------


def build_roster(drill: Drill) -> int:
    """Create a DrillParticipant row per active employee in scope.

    Idempotent: re-running adds only employees who are missing, so a location
    added mid-drill can be folded in without duplicating anyone.
    """
    locations = list(drill.locations.all())
    if not locations:
        raise CallTreeError("The drill has no target locations.")

    employees = Employee.objects.filter(
        location__in=locations, is_active=True
    ).select_related("location")

    existing = set(
        drill.participants.values_list("employee_id", flat=True)
    )
    new_rows = [
        DrillParticipant(drill=drill, employee=employee)
        for employee in employees
        if employee.pk not in existing
    ]
    if new_rows:
        DrillParticipant.objects.bulk_create(new_rows)
    return len(new_rows)


# ---------------------------------------------------------------------------
# Activation
# ---------------------------------------------------------------------------


def activate_drill(drill: Drill, *, actor=None) -> ActivationResult:
    """Start the call tree: freeze the roster and queue the initial broadcast.

    The Notification rows are created inside the transaction; the actual
    hand-off to Pub/Sub happens after commit so a rolled-back activation can
    never leak a real SMS.
    """
    with transaction.atomic():
        # Lock the row, then re-read the status from it: two Safety Officers
        # hitting "Initiate" at once must not both start the broadcast. The
        # caller's instance is the one mutated, so it is never left stale.
        locked = Drill.objects.select_for_update().get(pk=drill.pk)
        if locked.status == Drill.Status.ACTIVE:
            raise CallTreeError("The drill is already active.")
        if locked.status in {Drill.Status.COMPLETED, Drill.Status.CANCELLED}:
            raise CallTreeError(
                f"A {locked.get_status_display().lower()} drill cannot be re-activated."
            )

        build_roster(drill)

        drill.status = Drill.Status.ACTIVE
        drill.started_at = timezone.now()
        drill.initiated_by = actor if getattr(actor, "is_authenticated", False) else None
        drill.save(update_fields=["status", "started_at", "initiated_by", "updated_at"])

        participants = list(
            drill.participants.select_related("employee", "employee__location")
        )
        notifications = queue_notifications(
            drill, participants, purpose=Notification.Purpose.INITIAL
        )

    audit(
        "drill.activated",
        actor=actor,
        target=f"drill:{drill.pk}",
        participants=len(participants),
        notifications=len(notifications),
    )
    datastore.mirror_drill(drill)
    datastore.mirror_participants(participants)

    _publish(notifications)

    return ActivationResult(
        drill=drill,
        participants=len(participants),
        notifications_queued=len(notifications),
    )


def queue_notifications(
    drill: Drill,
    participants,
    *,
    purpose: str = Notification.Purpose.INITIAL,
) -> list[Notification]:
    """Create queued Notification rows for the given participants.

    One row per enabled channel per participant. An employee with no usable
    address on a channel gets a SKIPPED row rather than silence, so the audit
    trail shows why they were never reached.
    """
    template = drill.message_template or DEFAULT_TEMPLATE
    # Only the default template avoids {link} in SMS; a drill's own custom
    # message_template is sent as written on every enabled channel, since an
    # officer who types {link} into it has done so deliberately.
    sms_template = drill.message_template or DEFAULT_SMS_TEMPLATE
    event = drill.seismic_event
    now = timezone.now()
    rows: list[Notification] = []

    for participant in participants:
        employee = participant.employee
        body = render_message(template, participant=participant, event=event)
        sms_body = (
            body
            if drill.message_template
            else render_message(sms_template, participant=participant, event=event)
        )

        if drill.send_sms:
            if employee.sms_opt_in and employee.mobile_number:
                rows.append(
                    Notification(
                        participant=participant,
                        channel=Notification.Channel.SMS,
                        purpose=purpose,
                        recipient=employee.mobile_number,
                        body=truncate_sms(sms_body),
                        queued_at=now,
                    )
                )
            else:
                rows.append(
                    Notification(
                        participant=participant,
                        channel=Notification.Channel.SMS,
                        purpose=purpose,
                        status=Notification.Status.SKIPPED,
                        recipient=employee.mobile_number or "",
                        body=truncate_sms(sms_body),
                        queued_at=now,
                        error_message="No mobile number on file or SMS opt-out.",
                    )
                )

        if drill.send_email:
            if employee.email_opt_in and employee.email:
                rows.append(
                    Notification(
                        participant=participant,
                        channel=Notification.Channel.EMAIL,
                        purpose=purpose,
                        recipient=employee.email,
                        body=body,
                        queued_at=now,
                    )
                )
            else:
                rows.append(
                    Notification(
                        participant=participant,
                        channel=Notification.Channel.EMAIL,
                        purpose=purpose,
                        status=Notification.Status.SKIPPED,
                        recipient=employee.email or "",
                        body=body,
                        queued_at=now,
                        error_message="No email address on file or email opt-out.",
                    )
                )

    created = Notification.objects.bulk_create(rows)
    return [n for n in created if n.status == Notification.Status.QUEUED]


def _publish(notifications) -> None:
    """Hand queued notifications to the dissemination module after commit."""
    if not notifications:
        return
    ids = [n.pk for n in notifications]

    def _dispatch():
        from dissemination.services import dispatch_notifications

        dispatch_notifications(ids)

    transaction.on_commit(_dispatch)


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------

SAFE_KEYWORDS = {"safe", "ok", "okay", "ligtas", "safe.", "im safe", "i am safe"}
HELP_KEYWORDS = {"help", "sos", "tulong", "assist", "injured", "need help"}


def interpret_reply(text: str) -> str | None:
    """Map free-text SMS to a participant status.

    Employees reply under stress, so this is deliberately forgiving: the first
    recognised keyword anywhere in the message wins, and HELP outranks SAFE
    when both appear.
    """
    if not text:
        return None
    normalised = " ".join(text.lower().split())
    stripped = normalised.strip(".!,")

    if stripped in HELP_KEYWORDS or any(word in normalised for word in ("help", "sos", "tulong")):
        return DrillParticipant.Status.HELP
    if stripped in SAFE_KEYWORDS or any(word in normalised for word in ("safe", "ligtas")):
        return DrillParticipant.Status.SAFE
    return None


def record_response(
    participant: DrillParticipant,
    status: str,
    *,
    channel: str = "sms",
    text: str = "",
) -> DrillParticipant:
    """Record a safety status and halt this participant's reminder loop.

    The first response wins: a later SAFE cannot overwrite an earlier HELP,
    which keeps a duplicate reply from quietly cancelling a rescue.
    """
    if status not in DrillParticipant.RESPONDED_STATUSES:
        raise CallTreeError(f"Unsupported response status: {status}")

    if participant.status == DrillParticipant.Status.HELP and status == DrillParticipant.Status.SAFE:
        logger.info(
            "Ignoring SAFE after HELP for participant=%s; keeping HELP", participant.pk
        )
        return participant

    participant.status = status
    participant.responded_at = participant.responded_at or timezone.now()
    participant.response_channel = channel
    participant.response_text = (text or "")[:255]
    participant.save(
        update_fields=[
            "status",
            "responded_at",
            "response_channel",
            "response_text",
            "updated_at",
        ]
    )

    datastore.mirror_participant(participant)
    audit(
        "participant.responded",
        target=f"participant:{participant.pk}",
        status=status,
        channel=channel,
        latency_seconds=participant.response_latency_seconds,
    )
    return participant


# ---------------------------------------------------------------------------
# Non-compliance: reminders and escalation
# ---------------------------------------------------------------------------


@dataclass
class SweepResult:
    reminders: int = 0
    escalated: int = 0
    flagged: int = 0


def sweep_drill(drill: Drill, *, now=None) -> SweepResult:
    """Chase non-responders.

    Inside the response window: re-broadcast at the reminder interval, up to
    ``max_reminders``. Past the window: flag the participant non-compliant and
    escalate them to the EMT once.
    """
    now = now or timezone.now()
    result = SweepResult()

    if drill.status != Drill.Status.ACTIVE or not drill.started_at:
        return result

    pending = list(
        drill.participants.select_related("employee", "employee__location").exclude(
            status__in=DrillParticipant.RESPONDED_STATUSES
        )
    )
    if not pending:
        return result

    deadline = drill.deadline
    interval = timedelta(minutes=drill.reminder_interval_minutes)

    due_for_reminder = []
    for participant in pending:
        if now > deadline:
            continue
        if participant.reminders_sent >= drill.max_reminders:
            continue
        last = participant.notified_at or drill.started_at
        if now - last >= interval:
            due_for_reminder.append(participant)

    if due_for_reminder:
        notifications = queue_notifications(
            drill, due_for_reminder, purpose=Notification.Purpose.REMINDER
        )
        DrillParticipant.objects.filter(
            pk__in=[p.pk for p in due_for_reminder]
        ).update(reminders_sent=F("reminders_sent") + 1, updated_at=now)
        result.reminders = len(due_for_reminder)
        _publish(notifications)

    if now > deadline:
        overdue = [p for p in pending if p.escalated_at is None]
        for participant in overdue:
            participant.status = DrillParticipant.Status.NON_COMPLIANT
            participant.escalated_at = now
            participant.save(
                update_fields=["status", "escalated_at", "updated_at"]
            )
            datastore.mirror_participant(participant)
        result.flagged = len(overdue)
        result.escalated = escalate_to_emt(drill, overdue)

    if result.reminders or result.flagged:
        audit(
            "drill.swept",
            target=f"drill:{drill.pk}",
            reminders=result.reminders,
            flagged=result.flagged,
            escalated=result.escalated,
        )
    return result


def escalate_to_emt(drill: Drill, non_responders) -> int:
    """Notify the Emergency Management Team about unaccounted-for staff."""
    if not non_responders:
        return 0

    emt_participants = list(
        drill.participants.select_related("employee", "employee__location").filter(
            employee__role__in=[Employee.Role.EMT, Employee.Role.SAFETY_OFFICER]
        )
    )
    if not emt_participants:
        logger.warning("No EMT member is part of drill=%s; cannot escalate", drill.pk)
        return 0

    names = ", ".join(p.employee.full_name for p in non_responders[:10])
    more = len(non_responders) - 10
    if more > 0:
        names += f" (+{more} more)"

    body = (
        f"[DSO ESCALATION] {len(non_responders)} unaccounted for in "
        f"'{drill.name}' after {drill.response_window_minutes} min: {names}"
    )

    now = timezone.now()
    rows = []
    for participant in emt_participants:
        employee = participant.employee
        if drill.send_sms and employee.mobile_number:
            rows.append(
                Notification(
                    participant=participant,
                    channel=Notification.Channel.SMS,
                    purpose=Notification.Purpose.ESCALATION,
                    recipient=employee.mobile_number,
                    body=truncate_sms(body),
                    queued_at=now,
                )
            )
        if drill.send_email and employee.email:
            rows.append(
                Notification(
                    participant=participant,
                    channel=Notification.Channel.EMAIL,
                    purpose=Notification.Purpose.ESCALATION,
                    recipient=employee.email,
                    body=body,
                    queued_at=now,
                )
            )

    created = Notification.objects.bulk_create(rows)
    _publish(created)
    return len(created)


def complete_drill(drill: Drill, *, actor=None) -> Drill:
    """Close the drill and freeze its statistics."""
    if drill.status != Drill.Status.ACTIVE:
        raise CallTreeError("Only an active drill can be completed.")

    drill.status = Drill.Status.COMPLETED
    drill.completed_at = timezone.now()
    drill.save(update_fields=["status", "completed_at", "updated_at"])

    drill.participants.filter(
        status__in=[DrillParticipant.Status.PENDING, DrillParticipant.Status.NOTIFIED]
    ).update(status=DrillParticipant.Status.NON_COMPLIANT, updated_at=timezone.now())

    datastore.mirror_drill(drill)
    audit("drill.completed", actor=actor, target=f"drill:{drill.pk}")
    return drill


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def drill_statistics(drill: Drill) -> dict:
    """Compliance and latency figures for the dashboard and the CSV export."""
    participants = list(drill.participants.all())
    total = len(participants)

    counts = {value: 0 for value, _ in DrillParticipant.Status.choices}
    latencies = []
    for participant in participants:
        counts[participant.status] = counts.get(participant.status, 0) + 1
        latency = participant.response_latency_seconds
        if latency is not None:
            latencies.append(latency)

    responded = counts[DrillParticipant.Status.SAFE] + counts[DrillParticipant.Status.HELP]

    dispatch_latencies = [
        n.dispatch_latency_seconds
        for n in Notification.objects.filter(
            participant__drill=drill, status=Notification.Status.SENT
        )
        if n.dispatch_latency_seconds is not None
    ]

    return {
        "total": total,
        "responded": responded,
        "safe": counts[DrillParticipant.Status.SAFE],
        "help": counts[DrillParticipant.Status.HELP],
        "pending": counts[DrillParticipant.Status.PENDING]
        + counts[DrillParticipant.Status.NOTIFIED],
        "non_compliant": counts[DrillParticipant.Status.NON_COMPLIANT],
        "unreachable": counts[DrillParticipant.Status.UNREACHABLE],
        "response_rate": round(responded / total * 100, 1) if total else 0.0,
        "avg_response_seconds": round(sum(latencies) / len(latencies), 1)
        if latencies
        else None,
        "fastest_response_seconds": round(min(latencies), 1) if latencies else None,
        "slowest_response_seconds": round(max(latencies), 1) if latencies else None,
        "avg_dispatch_latency_seconds": round(
            sum(dispatch_latencies) / len(dispatch_latencies), 2
        )
        if dispatch_latencies
        else None,
        "deadline": drill.deadline,
        "window_open": drill.is_window_open,
    }


# ---------------------------------------------------------------------------
# Automatic activation from a seismic event
# ---------------------------------------------------------------------------


def evaluate_seismic_event(event: SeismicEvent, *, auto_trigger: bool | None = None):
    """Run the Trigger Filter for an ingested event and optionally activate.

    Returns ``(matches, drill_or_None)``. The drill is created only when at
    least one location trips the filter and auto-triggering is enabled.
    """
    from core.models import Location

    auto_trigger = (
        settings.AUTO_TRIGGER_ENABLED if auto_trigger is None else auto_trigger
    )
    locations = list(Location.objects.filter(is_active=True))
    matches = evaluate_event(event, locations)
    hits = triggered_locations(matches)

    event.evaluated_at = timezone.now()
    event.triggered = bool(hits)
    event.evaluation_note = (
        f"{len(hits)} of {len(locations)} location(s) in scope"
        if hits
        else "no location met the Trigger Filter"
    )
    event.save(update_fields=["evaluated_at", "triggered", "evaluation_note", "updated_at"])

    if not hits or not auto_trigger:
        return matches, None

    drill = Drill.objects.create(
        name=f"Auto: M{event.magnitude:.1f} {event.place or 'seismic event'}"[:160],
        kind=Drill.Kind.INCIDENT,
        trigger_mode=Drill.TriggerMode.AUTOMATIC,
        seismic_event=event,
    )
    drill.locations.set(hits)
    activate_drill(drill)
    return matches, drill
