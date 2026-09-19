"""Google Cloud Pub/Sub publisher.

Decoupling the broadcast from the request cycle is what lets the call tree
queue thousands of alerts without the web container blocking. When Pub/Sub is
disabled, ``publish_batch`` reports that fact and the caller falls back to the
local thread-pool dispatcher.
"""

from __future__ import annotations

import json
import logging
import threading

from django.conf import settings

logger = logging.getLogger(__name__)

_publisher = None
_publisher_lock = threading.Lock()
_publisher_failed = False


def is_enabled() -> bool:
    return bool(settings.PUBSUB_ENABLED and settings.GCP_PROJECT_ID)


def get_publisher():
    global _publisher, _publisher_failed

    if not is_enabled() or _publisher_failed:
        return None
    if _publisher is not None:
        return _publisher

    with _publisher_lock:
        if _publisher is not None:
            return _publisher
        try:
            from google.cloud import pubsub_v1

            _publisher = pubsub_v1.PublisherClient()
        except Exception:
            _publisher_failed = True
            logger.exception("Pub/Sub unavailable; falling back to local dispatch")
            return None
    return _publisher


def reset_publisher() -> None:
    """Drop the cached publisher. Used by tests."""
    global _publisher, _publisher_failed
    with _publisher_lock:
        _publisher = None
        _publisher_failed = False


def topic_path() -> str:
    return f"projects/{settings.GCP_PROJECT_ID}/topics/{settings.PUBSUB_TOPIC_NOTIFICATIONS}"


def publish_batch(notification_ids) -> int:
    """Publish one message per notification id. Returns the number published.

    Returns 0 when Pub/Sub is unavailable, which the caller reads as
    'dispatch these locally instead'.
    """
    publisher = get_publisher()
    if publisher is None:
        return 0

    path = topic_path()
    futures = []
    for notification_id in notification_ids:
        data = json.dumps({"notification_id": notification_id}).encode("utf-8")
        try:
            futures.append(publisher.publish(path, data))
        except Exception:
            logger.exception("Pub/Sub publish failed for notification=%s", notification_id)

    published = 0
    for future in futures:
        try:
            future.result(timeout=30)
            published += 1
        except Exception:
            logger.exception("Pub/Sub publish did not confirm")
    return published
