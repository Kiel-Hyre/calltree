"""Shared helpers: phone normalisation, message rendering, audit logging."""

from __future__ import annotations

import logging
import re

from django.conf import settings

from core.models import AuditLog

logger = logging.getLogger(__name__)

DEFAULT_TEMPLATE = (
    "[DSO EARTHQUAKE DRILL] {name}, an M{magnitude} event was reported near "
    "{place}. {instruction} Once you are safe, reply SAFE or HELP, or tap {link}"
)

_NON_DIGITS = re.compile(r"\D")


def normalize_msisdn(raw: str, default_country_code: str = "63") -> str:
    """Normalise a Philippine mobile number to bare E.164 digits.

    M360 expects ``639171234567`` - no plus sign, no spaces. Accepts the
    common local spellings: ``09171234567``, ``+63 917 123 4567``, ``9171234567``.
    Returns an empty string when the input cannot be read as a number.
    """
    if not raw:
        return ""

    digits = _NON_DIGITS.sub("", str(raw))
    if not digits:
        return ""

    # 00 international prefix
    if digits.startswith("00"):
        digits = digits[2:]
    # Local trunk prefix: 09171234567 -> 639171234567
    if digits.startswith("0"):
        digits = default_country_code + digits[1:]
    # Bare subscriber number: 9171234567 -> 639171234567
    elif len(digits) == 10 and digits.startswith("9"):
        digits = default_country_code + digits

    return digits


def render_message(template: str, *, participant, event=None) -> str:
    """Fill an alert template for one participant.

    Unknown placeholders are left as-is rather than raising, so a typo in an
    operator-authored template degrades to a slightly ugly message instead of
    blocking the broadcast.
    """
    employee = participant.employee
    location = employee.location
    context = {
        "name": employee.full_name.split()[0] if employee.full_name else "Colleague",
        "full_name": employee.full_name,
        "employee_id": employee.employee_id,
        "location": location.name,
        "instruction": location.evacuation_instruction
        or "Drop, Cover and Hold, then proceed to your assembly point.",
        "magnitude": f"{event.magnitude:.1f}" if event else "N/A",
        "place": event.place if event and event.place else location.name,
        "link": participant.status_url,
        "drill": participant.drill.name,
    }

    body = template or DEFAULT_TEMPLATE
    try:
        return body.format(**context)
    except (KeyError, IndexError, ValueError):
        logger.warning("Alert template has an unknown placeholder; sending raw text")
        return body


def truncate_sms(body: str) -> str:
    limit = settings.SMS_MAX_LENGTH
    if len(body) <= limit:
        return body
    return body[: max(0, limit - 3)] + "..."


def audit(action: str, *, actor=None, target: str = "", **detail) -> AuditLog:
    """Append an entry to the operator/system audit trail."""
    return AuditLog.objects.create(
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        target=str(target)[:160],
        detail=detail,
    )
