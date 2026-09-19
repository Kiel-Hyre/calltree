"""Cloud Firestore mirror for live safety status.

The relational database stays the system of record. Firestore holds a small,
denormalised copy of each drill's participant status so dashboards (and any
future mobile client) can subscribe to changes in real time.

Every function here is a no-op when ``FIRESTORE_ENABLED`` is false, and every
failure is swallowed and logged: a mirror outage must never stop an alert from
going out.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

_client = None
_client_lock = threading.Lock()
_client_failed = False


def is_enabled() -> bool:
    return bool(settings.FIRESTORE_ENABLED)


def get_client():
    """Return a lazily created Firestore client, or None if unavailable."""
    global _client, _client_failed

    if not is_enabled() or _client_failed:
        return None
    if _client is not None:
        return _client

    with _client_lock:
        if _client is not None:
            return _client
        try:
            from google.cloud import firestore

            kwargs: dict[str, Any] = {}
            if settings.GCP_PROJECT_ID:
                kwargs["project"] = settings.GCP_PROJECT_ID
            if settings.FIRESTORE_DATABASE and settings.FIRESTORE_DATABASE != "(default)":
                kwargs["database"] = settings.FIRESTORE_DATABASE
            _client = firestore.Client(**kwargs)
        except Exception:
            # Missing credentials or library: degrade to relational-only.
            _client_failed = True
            logger.exception("Firestore unavailable; continuing without the mirror")
            return None
    return _client


def reset_client() -> None:
    """Drop the cached client. Used by tests and after a credential change."""
    global _client, _client_failed
    with _client_lock:
        _client = None
        _client_failed = False


def _collection(name: str) -> str:
    prefix = settings.FIRESTORE_COLLECTION_PREFIX.strip("/")
    return f"{prefix}_{name}" if prefix else name


def mirror_drill(drill) -> None:
    """Write the drill header document."""
    client = get_client()
    if client is None:
        return
    try:
        client.collection(_collection("drills")).document(str(drill.pk)).set(
            {
                "id": drill.pk,
                "name": drill.name,
                "kind": drill.kind,
                "status": drill.status,
                "trigger_mode": drill.trigger_mode,
                "started_at": drill.started_at,
                "completed_at": drill.completed_at,
                "response_window_minutes": drill.response_window_minutes,
            },
            merge=True,
        )
    except Exception:
        logger.exception("Firestore drill mirror failed for drill=%s", drill.pk)


def mirror_participant(participant) -> None:
    """Write one participant's live status document."""
    client = get_client()
    if client is None:
        return
    try:
        doc = (
            client.collection(_collection("drills"))
            .document(str(participant.drill_id))
            .collection("participants")
            .document(str(participant.pk))
        )
        doc.set(
            {
                "id": participant.pk,
                "employee_id": participant.employee.employee_id,
                "full_name": participant.employee.full_name,
                "location": participant.employee.location.name,
                "escalation_tier": participant.employee.escalation_tier,
                "status": participant.status,
                "notified_at": participant.notified_at,
                "responded_at": participant.responded_at,
                "response_channel": participant.response_channel,
                "latency_seconds": participant.response_latency_seconds,
            },
            merge=True,
        )
    except Exception:
        logger.exception(
            "Firestore participant mirror failed for participant=%s", participant.pk
        )


def mirror_participants(participants) -> int:
    """Batch-write many participant documents. Returns the number written."""
    client = get_client()
    if client is None:
        return 0
    written = 0
    try:
        batch = client.batch()
        for index, participant in enumerate(participants, start=1):
            doc = (
                client.collection(_collection("drills"))
                .document(str(participant.drill_id))
                .collection("participants")
                .document(str(participant.pk))
            )
            batch.set(
                doc,
                {
                    "id": participant.pk,
                    "employee_id": participant.employee.employee_id,
                    "full_name": participant.employee.full_name,
                    "location": participant.employee.location.name,
                    "status": participant.status,
                    "notified_at": participant.notified_at,
                    "responded_at": participant.responded_at,
                },
                merge=True,
            )
            written += 1
            # Firestore caps a batch at 500 writes.
            if index % 450 == 0:
                batch.commit()
                batch = client.batch()
        batch.commit()
    except Exception:
        logger.exception("Firestore batch mirror failed")
        return 0
    return written
