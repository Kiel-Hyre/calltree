"""Great-circle distance and the Trigger Filter.

Module 2 of the architecture: compare a USGS epicentre against each geofenced
DSO location and decide whether the call tree activates.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres between two WGS-84 points."""
    phi1, phi2 = radians(lat1), radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = radians(lon2 - lon1)

    a = sin(d_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(sqrt(min(1.0, a)))


@dataclass(frozen=True)
class LocationMatch:
    """The Trigger Filter's verdict for one location."""

    location: object
    distance_km: float
    magnitude_ok: bool
    within_radius: bool

    @property
    def triggered(self) -> bool:
        return self.magnitude_ok and self.within_radius

    @property
    def reason(self) -> str:
        if self.triggered:
            return (
                f"M{self.location.min_magnitude:.1f}+ threshold met at "
                f"{self.distance_km:.1f} km (radius {self.location.radius_km:.0f} km)"
            )
        if not self.magnitude_ok and not self.within_radius:
            return "below magnitude threshold and outside the risk radius"
        if not self.magnitude_ok:
            return f"below the M{self.location.min_magnitude:.1f} threshold"
        return (
            f"outside the risk radius ({self.distance_km:.1f} km > "
            f"{self.location.radius_km:.0f} km)"
        )


def evaluate_location(event, location) -> LocationMatch:
    """Apply the Trigger Filter to one event/location pair."""
    distance = haversine_km(
        event.latitude, event.longitude, location.latitude, location.longitude
    )
    return LocationMatch(
        location=location,
        distance_km=distance,
        magnitude_ok=event.magnitude >= location.min_magnitude,
        within_radius=distance <= location.radius_km,
    )


def evaluate_event(event, locations) -> list[LocationMatch]:
    """Evaluate an event against every candidate location, nearest first."""
    matches = [evaluate_location(event, location) for location in locations]
    matches.sort(key=lambda m: m.distance_km)
    return matches


def triggered_locations(matches) -> list:
    return [match.location for match in matches if match.triggered]
