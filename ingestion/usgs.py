"""USGS Earthquake Notification Service ingestion.

Three entry points, one normalised ``SeismicEvent``:

  * the public GeoJSON summary feed (polled)
  * an ENS webhook POST carrying a GeoJSON Feature or FeatureCollection
  * a forwarded ENS email alert, parsed from its plain-text body

``usgs_id`` is the idempotency key, so re-reading the same feed page or
receiving a duplicate webhook never creates a second event.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime

import requests
from django.conf import settings

from core.models import SeismicEvent

logger = logging.getLogger(__name__)


class IngestionError(Exception):
    """The payload could not be read as a USGS earthquake report."""


@dataclass(frozen=True)
class ParsedEvent:
    usgs_id: str
    magnitude: float
    place: str
    latitude: float
    longitude: float
    depth_km: float | None
    occurred_at: datetime
    detail_url: str = ""


def _epoch_ms_to_datetime(value) -> datetime:
    return datetime.fromtimestamp(float(value) / 1000.0, tz=UTC)


def parse_feature(feature: dict) -> ParsedEvent:
    """Normalise one GeoJSON Feature from the USGS feed."""
    if not isinstance(feature, dict):
        raise IngestionError("Feature is not an object.")

    properties = feature.get("properties") or {}
    geometry = feature.get("geometry") or {}
    coordinates = geometry.get("coordinates") or []

    if len(coordinates) < 2:
        raise IngestionError("Feature has no usable coordinates.")

    magnitude = properties.get("mag")
    if magnitude is None:
        raise IngestionError("Feature has no magnitude.")

    event_time = properties.get("time")
    if event_time is None:
        raise IngestionError("Feature has no event time.")

    usgs_id = feature.get("id") or properties.get("code")
    if not usgs_id:
        raise IngestionError("Feature has no id.")

    # GeoJSON order is [longitude, latitude, depth].
    longitude, latitude = float(coordinates[0]), float(coordinates[1])
    depth = float(coordinates[2]) if len(coordinates) > 2 else None

    return ParsedEvent(
        usgs_id=str(usgs_id),
        magnitude=float(magnitude),
        place=str(properties.get("place") or "")[:255],
        latitude=latitude,
        longitude=longitude,
        depth_km=depth,
        occurred_at=_epoch_ms_to_datetime(event_time),
        detail_url=str(properties.get("url") or ""),
    )


def parse_payload(payload: dict) -> list[ParsedEvent]:
    """Accept a Feature, a FeatureCollection, or a bare ENS-style dict."""
    if not isinstance(payload, dict):
        raise IngestionError("Payload is not a JSON object.")

    kind = payload.get("type")
    if kind == "FeatureCollection":
        parsed = []
        for feature in payload.get("features") or []:
            try:
                parsed.append(parse_feature(feature))
            except IngestionError as exc:
                logger.warning("Skipping unreadable feature: %s", exc)
        return parsed

    if kind == "Feature":
        return [parse_feature(payload)]

    # Flat ENS-style JSON: {"id":..., "magnitude":..., "latitude":...}
    required = {"magnitude", "latitude", "longitude"}
    if required.issubset(payload.keys()):
        event_time = payload.get("time") or payload.get("occurred_at")
        if isinstance(event_time, (int, float)):
            occurred_at = _epoch_ms_to_datetime(event_time)
        elif isinstance(event_time, str):
            occurred_at = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
        else:
            raise IngestionError("Payload has no event time.")
        identifier = payload.get("id") or payload.get("eventid")
        if not identifier:
            raise IngestionError("Payload has no id.")
        return [
            ParsedEvent(
                usgs_id=str(identifier),
                magnitude=float(payload["magnitude"]),
                place=str(payload.get("place") or payload.get("region") or "")[:255],
                latitude=float(payload["latitude"]),
                longitude=float(payload["longitude"]),
                depth_km=float(payload["depth"]) if payload.get("depth") is not None else None,
                occurred_at=occurred_at,
                detail_url=str(payload.get("url") or ""),
            )
        ]

    raise IngestionError("Unrecognised USGS payload shape.")


# ENS emails carry lines such as:
#   Magnitude 6.1
#   Location 14.123N 121.456E
#   Depth 35 km
#   Time 2026-09-19 04:11:22 UTC
#   Event ID us7000abcd
_EMAIL_PATTERNS = {
    "magnitude": re.compile(r"magnitude[:\s]+([0-9]+(?:\.[0-9]+)?)", re.I),
    "latitude": re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*([NS])", re.I),
    "longitude": re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*([EW])", re.I),
    "depth": re.compile(r"depth[:\s]+([0-9]+(?:\.[0-9]+)?)", re.I),
    "event_id": re.compile(r"event\s*id[:\s]+([A-Za-z0-9_-]+)", re.I),
    "time": re.compile(r"time[:\s]+([0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}:[0-9]{2})", re.I),
    "place": re.compile(r"(?:location|region)[:\s]+(.+)", re.I),
}


def parse_email_alert(body: str) -> ParsedEvent:
    """Parse a forwarded USGS ENS plain-text email alert."""
    if not body:
        raise IngestionError("Empty email body.")

    def _find(key: str):
        match = _EMAIL_PATTERNS[key].search(body)
        return match if match else None

    magnitude_match = _find("magnitude")
    latitude_match = _find("latitude")
    longitude_match = _find("longitude")
    if not (magnitude_match and latitude_match and longitude_match):
        raise IngestionError("Email alert is missing magnitude or coordinates.")

    latitude = float(latitude_match.group(1))
    if latitude_match.group(2).upper() == "S":
        latitude = -latitude
    longitude = float(longitude_match.group(1))
    if longitude_match.group(2).upper() == "W":
        longitude = -longitude

    time_match = _find("time")
    if time_match:
        occurred_at = datetime.fromisoformat(
            time_match.group(1).replace(" ", "T")
        ).replace(tzinfo=UTC)
    else:
        occurred_at = datetime.now(tz=UTC)

    id_match = _find("event_id")
    usgs_id = (
        id_match.group(1)
        if id_match
        else f"ens-{occurred_at:%Y%m%d%H%M%S}-{magnitude_match.group(1)}"
    )

    depth_match = _find("depth")
    place_match = _find("place")

    return ParsedEvent(
        usgs_id=usgs_id,
        magnitude=float(magnitude_match.group(1)),
        place=(place_match.group(1).strip()[:255] if place_match else ""),
        latitude=latitude,
        longitude=longitude,
        depth_km=float(depth_match.group(1)) if depth_match else None,
        occurred_at=occurred_at,
    )


def store_event(parsed: ParsedEvent, *, source: str) -> tuple[SeismicEvent, bool]:
    """Persist a parsed event, de-duplicating on the USGS id."""
    event, created = SeismicEvent.objects.get_or_create(
        usgs_id=parsed.usgs_id,
        defaults={
            "magnitude": parsed.magnitude,
            "place": parsed.place,
            "latitude": parsed.latitude,
            "longitude": parsed.longitude,
            "depth_km": parsed.depth_km,
            "occurred_at": parsed.occurred_at,
            "source": source,
            "detail_url": parsed.detail_url,
        },
    )
    if not created and event.magnitude != parsed.magnitude:
        # USGS revises magnitudes as more stations report in.
        event.magnitude = parsed.magnitude
        event.place = parsed.place or event.place
        event.save(update_fields=["magnitude", "place", "updated_at"])
    return event, created


def fetch_feed(url: str | None = None, *, session=None) -> list[ParsedEvent]:
    """Read the USGS GeoJSON summary feed."""
    url = url or settings.USGS_FEED_URL
    http = session or requests
    try:
        response = http.get(url, timeout=20)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise IngestionError(f"Could not read the USGS feed: {exc}") from exc
    return parse_payload(payload)
