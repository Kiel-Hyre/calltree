"""Outbound channel adapters: SMS gateways and SMTP email.

Every adapter returns a ``DeliveryResult`` instead of raising, so one bad
recipient can never abort a broadcast loop.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests
from django.conf import settings
from django.core.mail import EmailMessage, get_connection

from core.services import normalize_msisdn

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeliveryResult:
    ok: bool
    provider_message_id: str = ""
    error: str = ""
    simulated: bool = False


class M360Client:
    """Thin client for the M360 Philippines broadcast API.

    The endpoint URL and credentials come from settings so that an API
    revision is a .env change rather than a code change. With
    ``M360_ENABLED`` false the client simulates delivery, which is what local
    development and the automated tests run against.
    """

    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(settings.M360_ENABLED and settings.M360_APP_KEY)

    def send(self, msisdn: str, body: str) -> DeliveryResult:
        number = normalize_msisdn(msisdn)
        if not number:
            return DeliveryResult(ok=False, error=f"Unusable mobile number: {msisdn!r}")

        if not self.enabled:
            logger.info("[SMS simulated] -> %s: %s", number, body)
            return DeliveryResult(
                ok=True, provider_message_id=f"sim-{number}", simulated=True
            )

        payload = {
            "app_key": settings.M360_APP_KEY,
            "app_secret": settings.M360_APP_SECRET,
            "msisdn": number,
            "content": body,
        }
        if settings.M360_SHORTCODE_MASK:
            payload["shortcode_mask"] = settings.M360_SHORTCODE_MASK

        try:
            response = self.session.post(
                settings.M360_BROADCAST_URL,
                json=payload,
                timeout=settings.M360_TIMEOUT,
            )
        except requests.RequestException as exc:
            return DeliveryResult(ok=False, error=f"M360 request failed: {exc}")

        if response.status_code >= 400:
            return DeliveryResult(
                ok=False,
                error=f"M360 HTTP {response.status_code}: {response.text[:200]}",
            )

        try:
            data = response.json()
        except ValueError:
            # A 2xx with a non-JSON body still means the gateway accepted it.
            return DeliveryResult(ok=True, provider_message_id="")

        message_id = str(
            data.get("message_id") or data.get("messageId") or data.get("id") or ""
        )
        # M360 signals application-level failure with a non-zero "code".
        code = data.get("code")
        if code not in (None, 0, "0", 200, "200"):
            return DeliveryResult(
                ok=False,
                provider_message_id=message_id,
                error=f"M360 code {code}: {data.get('message', '')}"[:200],
            )
        return DeliveryResult(ok=True, provider_message_id=message_id)

    def balance(self) -> dict:
        """Read the remaining SMS credit for the System Health Check."""
        if not self.enabled:
            return {"enabled": False, "simulated": True, "balance": None}
        try:
            response = self.session.get(
                settings.M360_BALANCE_URL,
                params={
                    "app_key": settings.M360_APP_KEY,
                    "app_secret": settings.M360_APP_SECRET,
                },
                timeout=settings.M360_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            return {"enabled": True, "error": str(exc)[:200], "balance": None}
        return {
            "enabled": True,
            "balance": data.get("balance", data.get("credits")),
            "raw": data,
        }


class TextBeeClient:
    """Thin client for the TextBee.dev SMS gateway.

    TextBee turns an Android phone into a gateway: the API queues a job for
    that device's app, which sends it through the phone's own SIM. The base
    URL and payload shape are configurable for the same reason as M360's -
    if TextBee revises their API, that is a .env change, not a code change.
    With ``TEXTBEE_ENABLED`` false the client simulates delivery, matching
    every other gateway in this module.
    """

    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(
            settings.TEXTBEE_ENABLED
            and settings.TEXTBEE_API_KEY
            and settings.TEXTBEE_DEVICE_ID
        )

    def _headers(self) -> dict:
        return {"x-api-key": settings.TEXTBEE_API_KEY}

    def send(self, msisdn: str, body: str) -> DeliveryResult:
        number = normalize_msisdn(msisdn)
        if not number:
            return DeliveryResult(ok=False, error=f"Unusable mobile number: {msisdn!r}")

        if not self.enabled:
            logger.info("[SMS simulated via textbee] -> %s: %s", number, body)
            return DeliveryResult(
                ok=True, provider_message_id=f"sim-{number}", simulated=True
            )

        url = (
            f"{settings.TEXTBEE_BASE_URL.rstrip('/')}"
            f"/gateway/devices/{settings.TEXTBEE_DEVICE_ID}/send-sms"
        )
        # TextBee expects the recipient with a leading '+'; normalize_msisdn
        # returns bare digits so every gateway in this module shares one
        # number format internally.
        payload = {"recipients": [f"+{number}"], "message": body}

        try:
            response = self.session.post(
                url,
                json=payload,
                headers=self._headers(),
                timeout=settings.TEXTBEE_TIMEOUT,
            )
        except requests.RequestException as exc:
            return DeliveryResult(ok=False, error=f"TextBee request failed: {exc}")

        if response.status_code >= 400:
            return DeliveryResult(
                ok=False,
                error=f"TextBee HTTP {response.status_code}: {response.text[:200]}",
            )

        try:
            data = response.json()
        except ValueError:
            # A 2xx with a non-JSON body still means the gateway accepted it.
            return DeliveryResult(ok=True, provider_message_id="")

        # TextBee has replied with both a top-level "success" flag and a
        # nested "data" object depending on version; check what is present
        # rather than assuming one shape.
        success = data.get("success", True)
        if success in (False, "false", 0, "0"):
            return DeliveryResult(
                ok=False,
                error=f"TextBee reported failure: {data.get('message', '')}"[:200],
            )

        inner = data.get("data") if isinstance(data.get("data"), dict) else data
        message_id = str(inner.get("id") or inner.get("messageId") or "")
        return DeliveryResult(ok=True, provider_message_id=message_id)

    def device_status(self) -> dict:
        """Read the gateway phone's status for the System Health Check.

        TextBee has no prepaid credit balance the way M360 does - the SMS
        cost is whatever the phone's own SIM plan charges - so this reports
        whether the paired device is registered and reachable instead.
        """
        if not self.enabled:
            return {"enabled": False, "simulated": True, "device": None}
        try:
            response = self.session.get(
                f"{settings.TEXTBEE_BASE_URL.rstrip('/')}"
                f"/gateway/devices/{settings.TEXTBEE_DEVICE_ID}",
                headers=self._headers(),
                timeout=settings.TEXTBEE_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            return {"enabled": True, "error": str(exc)[:200], "device": None}
        return {"enabled": True, "device": data}


def get_sms_client():
    """Return the active outbound SMS client, chosen by ``SMS_PROVIDER``.

    Centralising the choice here means the dispatcher and the health check
    never hardcode a provider, so adding a third gateway later is a new
    client class plus one more branch in this function.
    """
    provider = settings.SMS_PROVIDER
    if provider == "textbee":
        return TextBeeClient()
    if provider == "m360":
        return M360Client()
    logger.warning("Unknown SMS_PROVIDER %r; falling back to m360", provider)
    return M360Client()


class EmailChannel:
    """SMTP backup channel. Uses Django's mail backend and connection pool."""

    def __init__(self, subject: str = "[DSO] Emergency Notification"):
        self.subject = subject
        self._connection = None

    def open(self):
        self._connection = get_connection(fail_silently=False)
        return self

    def close(self):
        if self._connection is not None:
            try:
                self._connection.close()
            except Exception:  # pragma: no cover - backend specific
                logger.debug("Closing the SMTP connection failed", exc_info=True)
            self._connection = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc_info):
        self.close()
        return False

    def send(self, recipient: str, body: str, subject: str | None = None) -> DeliveryResult:
        if not recipient:
            return DeliveryResult(ok=False, error="No email address on file.")
        try:
            message = EmailMessage(
                subject=subject or self.subject,
                body=body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[recipient],
                connection=self._connection,
            )
            sent = message.send(fail_silently=False)
        except Exception as exc:
            return DeliveryResult(ok=False, error=f"SMTP send failed: {exc}"[:200])

        if not sent:
            return DeliveryResult(ok=False, error="SMTP backend accepted 0 messages.")
        return DeliveryResult(ok=True, simulated=not settings.EMAIL_ENABLED)
