"""Unit tests for the USGS Data Ingestion Module."""

from __future__ import annotations

from datetime import UTC

import pytest
import responses

from core.models import SeismicEvent
from ingestion.usgs import (
    IngestionError,
    fetch_feed,
    parse_email_alert,
    parse_feature,
    parse_payload,
    store_event,
)


def feature(**overrides):
    """A GeoJSON Feature shaped like the real USGS summary feed."""
    data = {
        "type": "Feature",
        "id": "us7000abcd",
        "properties": {
            "mag": 6.2,
            "place": "14 km E of Manila, Philippines",
            "time": 1758290000000,
            "url": "https://earthquake.usgs.gov/earthquakes/eventpage/us7000abcd",
        },
        # GeoJSON order: longitude, latitude, depth
        "geometry": {"type": "Point", "coordinates": [121.05, 14.60, 35.0]},
    }
    data["properties"].update(overrides.pop("properties", {}))
    data.update(overrides)
    return data


class TestParseFeature:
    def test_reads_every_field(self):
        parsed = parse_feature(feature())

        assert parsed.usgs_id == "us7000abcd"
        assert parsed.magnitude == 6.2
        assert parsed.place == "14 km E of Manila, Philippines"
        assert parsed.depth_km == 35.0
        assert parsed.detail_url.endswith("us7000abcd")

    def test_reads_coordinates_in_geojson_order(self):
        """Longitude comes first in GeoJSON; swapping them would misplace
        every epicentre and break the Trigger Filter."""
        parsed = parse_feature(feature())

        assert parsed.latitude == 14.60
        assert parsed.longitude == 121.05

    def test_converts_epoch_milliseconds_to_utc(self):
        parsed = parse_feature(feature())

        assert parsed.occurred_at.tzinfo == UTC
        assert parsed.occurred_at.year == 2025

    def test_depth_is_optional(self):
        parsed = parse_feature(
            feature(geometry={"type": "Point", "coordinates": [121.05, 14.60]})
        )

        assert parsed.depth_km is None

    @pytest.mark.parametrize(
        "broken,match",
        [
            ({"geometry": {"coordinates": []}}, "coordinates"),
            ({"properties": {"mag": None}}, "magnitude"),
            ({"properties": {"time": None}}, "event time"),
            ({"id": None, "properties": {"code": None}}, "id"),
        ],
    )
    def test_incomplete_features_are_rejected(self, broken, match):
        with pytest.raises(IngestionError, match=match):
            parse_feature(feature(**broken))

    def test_a_non_object_is_rejected(self):
        with pytest.raises(IngestionError, match="not an object"):
            parse_feature("earthquake")


class TestParsePayload:
    def test_reads_a_feature_collection(self):
        payload = {
            "type": "FeatureCollection",
            "features": [feature(), feature(id="us7000efgh")],
        }

        assert len(parse_payload(payload)) == 2

    def test_skips_unreadable_features_and_keeps_the_rest(self):
        """One malformed entry must not discard a whole feed page."""
        payload = {
            "type": "FeatureCollection",
            "features": [feature(), {"type": "Feature", "properties": {}}],
        }

        parsed = parse_payload(payload)

        assert len(parsed) == 1
        assert parsed[0].usgs_id == "us7000abcd"

    def test_reads_a_bare_feature(self):
        assert len(parse_payload(feature())) == 1

    def test_reads_flat_ens_style_json(self):
        parsed = parse_payload(
            {
                "id": "ens-123",
                "magnitude": 5.4,
                "latitude": 14.6,
                "longitude": 121.0,
                "depth": 20,
                "time": "2026-09-19T04:11:22Z",
                "region": "Luzon",
            }
        )

        assert parsed[0].usgs_id == "ens-123"
        assert parsed[0].magnitude == 5.4
        assert parsed[0].place == "Luzon"

    def test_an_empty_collection_yields_nothing(self):
        assert parse_payload({"type": "FeatureCollection", "features": []}) == []

    def test_an_unrecognised_shape_is_rejected(self):
        with pytest.raises(IngestionError, match="Unrecognised"):
            parse_payload({"hello": "world"})

    def test_a_non_object_is_rejected(self):
        with pytest.raises(IngestionError, match="not a JSON object"):
            parse_payload([1, 2, 3])


class TestParseEmailAlert:
    BODY = """
    USGS Earthquake Notification Service

    Magnitude 6.1
    Location 14.123N 121.456E
    Depth 35 km
    Time 2026-09-19 04:11:22 UTC
    Event ID us7000wxyz
    """

    def test_reads_a_standard_ens_email(self):
        parsed = parse_email_alert(self.BODY)

        assert parsed.usgs_id == "us7000wxyz"
        assert parsed.magnitude == 6.1
        assert parsed.latitude == pytest.approx(14.123)
        assert parsed.longitude == pytest.approx(121.456)
        assert parsed.depth_km == 35.0
        assert parsed.occurred_at.hour == 4

    def test_applies_the_southern_and_western_signs(self):
        parsed = parse_email_alert("Magnitude 5.5 Location 33.5S 70.6W")

        assert parsed.latitude == pytest.approx(-33.5)
        assert parsed.longitude == pytest.approx(-70.6)

    def test_synthesises_an_id_when_the_email_has_none(self):
        parsed = parse_email_alert("Magnitude 5.5 Location 14.1N 121.4E")

        assert parsed.usgs_id.startswith("ens-")

    def test_an_empty_body_is_rejected(self):
        with pytest.raises(IngestionError, match="Empty email body"):
            parse_email_alert("")

    def test_a_body_without_coordinates_is_rejected(self):
        with pytest.raises(IngestionError, match="missing magnitude or coordinates"):
            parse_email_alert("Magnitude 6.1 but nothing else")


@pytest.mark.django_db
class TestStoreEvent:
    def test_creates_the_event(self):
        event, created = store_event(
            parse_feature(feature()), source=SeismicEvent.Source.USGS_FEED
        )

        assert created
        assert event.usgs_id == "us7000abcd"
        assert SeismicEvent.objects.count() == 1

    def test_the_usgs_id_deduplicates_repeat_reads(self):
        """Polling the same feed page every minute must not pile up rows."""
        parsed = parse_feature(feature())
        store_event(parsed, source=SeismicEvent.Source.USGS_FEED)

        event, created = store_event(parsed, source=SeismicEvent.Source.USGS_FEED)

        assert not created
        assert SeismicEvent.objects.count() == 1

    def test_a_revised_magnitude_updates_the_existing_row(self):
        """USGS revises magnitudes as more stations report in."""
        store_event(parse_feature(feature()), source=SeismicEvent.Source.USGS_FEED)

        revised = parse_feature(feature(properties={"mag": 6.7}))
        event, created = store_event(revised, source=SeismicEvent.Source.USGS_FEED)

        assert not created
        assert event.magnitude == 6.7
        assert SeismicEvent.objects.count() == 1


@pytest.mark.django_db
class TestFetchFeed:
    @responses.activate
    def test_reads_the_live_feed(self, settings):
        responses.get(
            settings.USGS_FEED_URL,
            json={"type": "FeatureCollection", "features": [feature()]},
        )

        parsed = fetch_feed()

        assert len(parsed) == 1
        assert parsed[0].magnitude == 6.2

    @responses.activate
    def test_an_http_error_becomes_an_ingestion_error(self, settings):
        responses.get(settings.USGS_FEED_URL, status=503)

        with pytest.raises(IngestionError, match="Could not read the USGS feed"):
            fetch_feed()

    @responses.activate
    def test_a_non_json_body_becomes_an_ingestion_error(self, settings):
        responses.get(settings.USGS_FEED_URL, body="<html>down</html>")

        with pytest.raises(IngestionError, match="Could not read the USGS feed"):
            fetch_feed()
