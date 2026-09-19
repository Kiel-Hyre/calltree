"""Unit tests for the Proximity Analysis & Logic Engine (the Trigger Filter)."""

from __future__ import annotations

import pytest

from engine.proximity import evaluate_event, evaluate_location, haversine_km, triggered_locations


class TestHaversine:
    def test_same_point_is_zero(self):
        assert haversine_km(14.55, 121.05, 14.55, 121.05) == pytest.approx(0.0, abs=1e-9)

    def test_manila_to_cebu(self):
        """Known reference distance, ~570 km great-circle."""
        distance = haversine_km(14.5995, 120.9842, 10.3157, 123.8854)

        assert distance == pytest.approx(571, abs=15)

    def test_is_symmetric(self):
        forward = haversine_km(14.55, 121.05, 10.31, 123.88)
        backward = haversine_km(10.31, 123.88, 14.55, 121.05)

        assert forward == pytest.approx(backward)

    def test_handles_antipodal_points_without_a_domain_error(self):
        """asin(sqrt(a)) with a slightly above 1.0 would raise without clamping."""
        distance = haversine_km(0.0, 0.0, 0.0, 180.0)

        assert distance == pytest.approx(20015, abs=5)

    def test_crossing_the_equator_and_the_meridian(self):
        assert haversine_km(-1.0, -1.0, 1.0, 1.0) == pytest.approx(314, abs=5)


@pytest.mark.django_db
class TestTriggerFilter:
    def test_strong_nearby_event_triggers(self, manila_event, bgc):
        match = evaluate_location(manila_event, bgc)

        assert match.magnitude_ok
        assert match.within_radius
        assert match.triggered
        assert match.distance_km < 10

    def test_strong_distant_event_does_not_trigger(self, manila_event, cebu):
        match = evaluate_location(manila_event, cebu)

        assert match.magnitude_ok
        assert not match.within_radius
        assert not match.triggered
        assert "outside the risk radius" in match.reason

    def test_weak_nearby_event_does_not_trigger(self, manila_event, bgc):
        manila_event.magnitude = 3.1

        match = evaluate_location(manila_event, bgc)

        assert not match.magnitude_ok
        assert match.within_radius
        assert not match.triggered
        assert "below the M5.0 threshold" in match.reason

    def test_magnitude_exactly_at_the_threshold_triggers(self, manila_event, bgc):
        """The paper specifies 'above the set threshold (e.g. 5.0)'; a 5.0
        reading is treated as meeting it, not missing it."""
        manila_event.magnitude = 5.0

        assert evaluate_location(manila_event, bgc).magnitude_ok

    def test_distance_exactly_at_the_radius_triggers(self, manila_event, bgc):
        bgc.radius_km = evaluate_location(manila_event, bgc).distance_km

        assert evaluate_location(manila_event, bgc).within_radius

    def test_failing_both_conditions_is_reported_as_both(self, manila_event, cebu):
        manila_event.magnitude = 2.0

        match = evaluate_location(manila_event, cebu)

        assert not match.triggered
        assert "below magnitude threshold and outside the risk radius" == match.reason

    def test_per_location_thresholds_are_independent(self, manila_event, bgc, cebu):
        """A data centre can be set more sensitive than an office."""
        bgc.min_magnitude = 7.0
        cebu.radius_km = 1000.0
        cebu.min_magnitude = 4.0

        assert not evaluate_location(manila_event, bgc).triggered
        assert evaluate_location(manila_event, cebu).triggered


@pytest.mark.django_db
class TestEvaluateEvent:
    def test_orders_matches_nearest_first(self, manila_event, bgc, cebu):
        matches = evaluate_event(manila_event, [cebu, bgc])

        assert [m.location for m in matches] == [bgc, cebu]

    def test_returns_only_the_locations_that_tripped(self, manila_event, bgc, cebu):
        matches = evaluate_event(manila_event, [bgc, cebu])

        assert triggered_locations(matches) == [bgc]

    def test_no_locations_yields_no_matches(self, manila_event):
        assert evaluate_event(manila_event, []) == []
