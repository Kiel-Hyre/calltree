"""Machine-to-machine endpoints.

These are called by external systems, not by the SPA, so they are exempt from
session authentication and CSRF. Each one is instead guarded by a shared
secret supplied in the URL query string or an ``X-Webhook-Token`` header.
"""

from __future__ import annotations

import base64
import json
import logging

from django.conf import settings
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accountability.services import handle_inbound_sms
from core.models import SeismicEvent
from core.services import audit
from dissemination.services import deliver_notification
from engine.services import evaluate_seismic_event
from ingestion.usgs import IngestionError, parse_email_alert, parse_payload, store_event

logger = logging.getLogger(__name__)


def _token_ok(request, expected: str) -> bool:
    """Compare the presented webhook token against the configured secret.

    An unset secret means the endpoint is open, which is only acceptable in
    development; the README and .env.example both flag this.
    """
    if not expected:
        logger.warning(
            "Webhook %s is unauthenticated: set its token before deploying",
            request.path,
        )
        return True
    presented = (
        request.headers.get("X-Webhook-Token")
        or request.query_params.get("token")
        or ""
    )
    return secrets_equal(presented, expected)


def secrets_equal(a: str, b: str) -> bool:
    from hmac import compare_digest

    return compare_digest(str(a), str(b))


class UsgsWebhookView(APIView):
    """USGS Data Ingestion Module - webhook entry point.

    Accepts a GeoJSON Feature/FeatureCollection, a flat ENS JSON object, or
    ``{"email_body": "..."}`` carrying a forwarded ENS text alert.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def post(self, request):
        if not _token_ok(request, settings.USGS_WEBHOOK_TOKEN):
            return Response({"detail": "Invalid token."}, status=status.HTTP_403_FORBIDDEN)

        payload = request.data if isinstance(request.data, dict) else {}

        try:
            if "email_body" in payload:
                parsed_events = [parse_email_alert(payload["email_body"])]
                source = SeismicEvent.Source.USGS_EMAIL
            else:
                parsed_events = parse_payload(payload)
                source = SeismicEvent.Source.USGS_WEBHOOK
        except IngestionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        results = []
        for parsed in parsed_events:
            event, created = store_event(parsed, source=source)
            matches, drill = evaluate_seismic_event(event)
            results.append(
                {
                    "usgs_id": event.usgs_id,
                    "created": created,
                    "magnitude": event.magnitude,
                    "triggered": event.triggered,
                    "triggered_locations": [m.location.name for m in matches if m.triggered],
                    "drill_id": drill.pk if drill else None,
                }
            )

        audit("ingest.usgs", target=source, events=len(results))
        return Response({"ingested": len(results), "events": results})


class M360InboundView(APIView):
    """Accountability & Feedback Module - inbound SMS from M360.

    M360 has posted both JSON and form-encoded callbacks across versions, and
    has used several names for the sender field, so this reads whichever of
    the known spellings is present.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]

    SENDER_KEYS = ("msisdn", "sender", "from", "mobile", "number", "source")
    TEXT_KEYS = ("message", "content", "text", "body", "sms")

    def post(self, request):
        if not _token_ok(request, settings.M360_WEBHOOK_TOKEN):
            return Response({"detail": "Invalid token."}, status=status.HTTP_403_FORBIDDEN)

        payload = request.data if isinstance(request.data, dict) else {}
        sender = self._first(payload, self.SENDER_KEYS)
        text = self._first(payload, self.TEXT_KEYS)

        if not sender:
            return Response(
                {"detail": "No sender number in the callback payload."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        message = handle_inbound_sms(sender, text, raw_payload=dict(payload))
        return Response(
            {
                "received": True,
                "processed": message.processed,
                "note": message.process_note,
            }
        )

    @staticmethod
    def _first(payload: dict, keys) -> str:
        for key in keys:
            value = payload.get(key)
            if value:
                return str(value)
        return ""


class PubSubPushView(APIView):
    """Cloud Pub/Sub push subscription endpoint.

    Pub/Sub guarantees at-least-once delivery, so this must be idempotent -
    ``deliver_notification`` short-circuits on an already-sent row. A 200 acks
    the message; anything else makes Pub/Sub redeliver.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def post(self, request):
        if not _token_ok(request, settings.PUBSUB_PUSH_TOKEN):
            return Response({"detail": "Invalid token."}, status=status.HTTP_403_FORBIDDEN)

        envelope = request.data if isinstance(request.data, dict) else {}
        message = envelope.get("message") or {}
        encoded = message.get("data")
        if not encoded:
            # Malformed: ack it rather than letting Pub/Sub retry forever.
            logger.warning("Pub/Sub push with no data field; acking")
            return Response({"detail": "No data."}, status=status.HTTP_200_OK)

        try:
            decoded = base64.b64decode(encoded).decode("utf-8")
            body = json.loads(decoded)
            notification_id = int(body["notification_id"])
        except (ValueError, KeyError, TypeError) as exc:
            logger.warning("Unreadable Pub/Sub payload (%s); acking", exc)
            return Response({"detail": "Unreadable payload."}, status=status.HTTP_200_OK)

        result = deliver_notification(notification_id)
        return Response({"notification_id": notification_id, "status": result})
