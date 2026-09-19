"""Dispatch queued notifications through the configured channels.

Two paths, one behaviour:

  * Cloud path - publish each notification id to Pub/Sub; a push subscription
    calls back into ``/webhooks/pubsub/`` and each message is delivered by a
    separate Cloud Run invocation.
  * Local path - a bounded thread pool sends them in-process. This is what
    runs in development, in Docker Compose, and whenever Pub/Sub is
    unreachable, so the system never silently stops alerting.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

from core import datastore
from core.models import DrillParticipant, Notification
from dissemination import pubsub
from dissemination.gateways import EmailChannel, M360Client

logger = logging.getLogger(__name__)


@dataclass
class DispatchSummary:
    published: int = 0
    sent: int = 0
    failed: int = 0
    skipped: int = 0

    @property
    def total(self) -> int:
        return self.sent + self.failed + self.skipped


def dispatch_notifications(notification_ids) -> DispatchSummary:
    """Entry point used by the engine after a call tree activates."""
    ids = list(notification_ids)
    if not ids:
        return DispatchSummary()

    published = pubsub.publish_batch(ids)
    if published:
        logger.info("Published %s notification(s) to Pub/Sub", published)
        return DispatchSummary(published=published)

    return send_now(ids)


def send_now(notification_ids) -> DispatchSummary:
    """Deliver the given notifications in-process."""
    ids = list(notification_ids)
    summary = DispatchSummary()
    if not ids:
        return summary

    workers = max(1, min(settings.DISPATCH_FALLBACK_THREADS, len(ids)))
    if workers == 1:
        results = [deliver_notification(nid) for nid in ids]
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(_deliver_in_thread, ids))

    for status in results:
        if status == Notification.Status.SENT:
            summary.sent += 1
        elif status == Notification.Status.SKIPPED:
            summary.skipped += 1
        else:
            summary.failed += 1
    return summary


def _deliver_in_thread(notification_id: int) -> str:
    """Thread entry point: each worker gets a clean database connection."""
    try:
        return deliver_notification(notification_id)
    finally:
        close_old_connections()


def deliver_notification(notification_id: int) -> str:
    """Send one notification and record the outcome. Never raises.

    Returns the resulting ``Notification.Status``.
    """
    try:
        notification = Notification.objects.select_related(
            "participant", "participant__employee", "participant__drill"
        ).get(pk=notification_id)
    except Notification.DoesNotExist:
        logger.warning("Notification %s no longer exists", notification_id)
        return Notification.Status.FAILED

    if notification.status == Notification.Status.SENT:
        # Pub/Sub delivers at least once; a redelivery must not double-send.
        return Notification.Status.SENT
    if notification.status == Notification.Status.SKIPPED:
        return Notification.Status.SKIPPED

    notification.attempts += 1

    if notification.channel == Notification.Channel.SMS:
        result = M360Client().send(notification.recipient, notification.body)
    else:
        subject = _subject_for(notification)
        with EmailChannel(subject=subject) as channel:
            result = channel.send(notification.recipient, notification.body, subject)

    now = timezone.now()
    if result.ok:
        notification.status = Notification.Status.SENT
        notification.sent_at = now
        notification.provider_message_id = result.provider_message_id[:128]
        notification.error_message = ""
    else:
        notification.status = Notification.Status.FAILED
        notification.error_message = result.error[:500]

    notification.save(
        update_fields=[
            "status",
            "sent_at",
            "provider_message_id",
            "error_message",
            "attempts",
            "updated_at",
        ]
    )

    if result.ok:
        _mark_notified(notification)

    return notification.status


def _subject_for(notification: Notification) -> str:
    drill = notification.participant.drill
    if notification.purpose == Notification.Purpose.ESCALATION:
        return f"[DSO ESCALATION] {drill.name}"
    if notification.purpose == Notification.Purpose.REMINDER:
        return f"[DSO REMINDER] {drill.name} - response required"
    return f"[DSO EMERGENCY] {drill.name} - respond SAFE or HELP"


def _mark_notified(notification: Notification) -> None:
    """Stamp the participant as reached; this starts the latency clock."""
    participant = notification.participant
    fields = ["notified_at", "updated_at"]
    participant.notified_at = notification.sent_at

    if participant.status == DrillParticipant.Status.PENDING:
        participant.status = DrillParticipant.Status.NOTIFIED
        fields.insert(0, "status")

    participant.save(update_fields=fields)
    datastore.mirror_participant(participant)


def health_check() -> dict:
    """System Health Check for the Safety Officer's pre-drill audit."""
    from django.db import connection

    checks: dict = {}

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["database"] = {"ok": True}
    except Exception as exc:
        checks["database"] = {"ok": False, "error": str(exc)[:200]}

    checks["sms_gateway"] = M360Client().balance()
    checks["sms_gateway"]["ok"] = "error" not in checks["sms_gateway"]

    checks["email"] = {
        "ok": True,
        "enabled": settings.EMAIL_ENABLED,
        "backend": settings.EMAIL_BACKEND,
    }
    checks["pubsub"] = {
        "ok": True,
        "enabled": pubsub.is_enabled(),
        "topic": settings.PUBSUB_TOPIC_NOTIFICATIONS if pubsub.is_enabled() else None,
        "mode": "pubsub" if pubsub.is_enabled() else "local-thread-pool",
    }
    checks["firestore"] = {
        "ok": True,
        "enabled": datastore.is_enabled(),
        "mode": "mirror" if datastore.is_enabled() else "relational-only",
    }

    queued = Notification.objects.filter(status=Notification.Status.QUEUED).count()
    failed = Notification.objects.filter(status=Notification.Status.FAILED).count()
    checks["queue"] = {"ok": failed == 0, "queued": queued, "failed": failed}

    checks["ok"] = all(
        section.get("ok", True) for section in checks.values() if isinstance(section, dict)
    )
    return checks
