"""Shared fixtures.

Every test runs with the external gateways disabled, so the M360 client and
the SMTP backend simulate delivery instead of reaching the network. Tests that
care about the wire format stub ``requests`` explicitly.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from core.models import Drill, Employee, Location, SeismicEvent


@pytest.fixture(autouse=True)
def offline_gateways(settings):
    """Keep every test off the network and out of GCP."""
    settings.SMS_PROVIDER = "m360"
    settings.M360_ENABLED = False
    settings.TEXTBEE_ENABLED = False
    settings.EMAIL_ENABLED = False
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.PUBSUB_ENABLED = False
    settings.FIRESTORE_ENABLED = False
    # The test database is SQLite, which will not take concurrent writers.
    # Dispatch serially here; the thread pool is a production path over
    # Postgres, and send_now() takes the same code path at either width.
    settings.DISPATCH_FALLBACK_THREADS = 1
    settings.AUTO_TRIGGER_ENABLED = False
    settings.PUBLIC_BASE_URL = "https://calltree.example.com"
    # Pin the webhook secrets rather than inheriting whatever the developer
    # happens to have in .env. Tests that assert enforcement set their own.
    settings.USGS_WEBHOOK_TOKEN = ""
    settings.M360_WEBHOOK_TOKEN = ""
    settings.TEXTBEE_WEBHOOK_TOKEN = ""
    settings.PUBSUB_PUSH_TOKEN = ""
    return settings


@pytest.fixture
def bgc(db):
    """A geofence centred on Bonifacio Global City."""
    return Location.objects.create(
        name="BGC Operations Hub",
        code="bgc",
        latitude=14.5507,
        longitude=121.0506,
        radius_km=250.0,
        min_magnitude=5.0,
        evacuation_instruction="Use Stairwell B.",
    )


@pytest.fixture
def cebu(db):
    """A second geofence far enough away to fall outside a Manila event."""
    return Location.objects.create(
        name="Cebu Satellite Office",
        code="cebu",
        latitude=10.3157,
        longitude=123.8854,
        radius_km=100.0,
        min_magnitude=5.0,
    )


@pytest.fixture
def staff(bgc):
    return Employee.objects.create(
        employee_id="DSO-100",
        full_name="Paolo Mendoza",
        email="paolo@example.com",
        mobile_number="+639171000100",
        location=bgc,
        role=Employee.Role.STAFF,
        escalation_tier=3,
    )


@pytest.fixture
def emt(bgc):
    return Employee.objects.create(
        employee_id="DSO-001",
        full_name="Maria Santos",
        email="maria@example.com",
        mobile_number="09171000001",
        location=bgc,
        role=Employee.Role.EMT,
        escalation_tier=1,
    )


@pytest.fixture
def drill(bgc, staff, emt):
    """A draft drill scoped to BGC with a 5-minute compliance window."""
    drill = Drill.objects.create(
        name="Q3 NSED Exercise",
        kind=Drill.Kind.DRILL,
        response_window_minutes=5,
        reminder_interval_minutes=2,
        max_reminders=2,
    )
    drill.locations.set([bgc])
    return drill


@pytest.fixture
def manila_event(db):
    """M6.2 near Metro Manila: inside the BGC radius, outside Cebu's."""
    return SeismicEvent.objects.create(
        usgs_id="us-test-0001",
        magnitude=6.2,
        place="14 km E of Manila",
        latitude=14.60,
        longitude=121.05,
        depth_km=35.0,
        occurred_at=timezone.now(),
        source=SeismicEvent.Source.MANUAL,
    )


@pytest.fixture
def officer(db):
    return get_user_model().objects.create_user(
        username="safetyofficer",
        password="drill-pass-4821",
        is_staff=True,
    )
