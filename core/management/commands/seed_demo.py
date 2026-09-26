"""Seed a demonstration dataset for the evaluation walkthrough.

Creates a Safety Officer account, two Metro Manila geofences and a small DSO
roster, so the ISO/IEC 25010 evaluation session has something to run against.

    python manage.py seed_demo --admin-password 'choose-something'
"""

from __future__ import annotations

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Employee, Location

LOCATIONS = [
    {
        "name": "BGC Operations Hub",
        "code": "bgc",
        "address": "Bonifacio Global City, Taguig",
        "latitude": 14.5507,
        "longitude": 121.0506,
        "radius_km": 250.0,
        "min_magnitude": 5.0,
        "evacuation_instruction": "Drop, Cover and Hold. Use Stairwell B and "
        "assemble at the 7th Avenue open lot.",
    },
    {
        "name": "Ortigas Data Centre",
        "code": "ortigas",
        "address": "Ortigas Center, Pasig",
        "latitude": 14.5866,
        "longitude": 121.0614,
        "radius_km": 250.0,
        "min_magnitude": 5.0,
        "evacuation_instruction": "Drop, Cover and Hold. Shut the cold aisle "
        "doors, then assemble at the Emerald Avenue muster point.",
    },
]

EMPLOYEES = [
    ("DSO-001", "Maria Santos", "+639171000001", "safety_officer", 1, "bgc"),
    ("DSO-002", "Jose Rivera", "+639171000002", "emt", 1, "bgc"),
    ("DSO-003", "Ana Dela Cruz", "+639171000003", "floor_warden", 2, "bgc"),
    ("DSO-004", "Paolo Mendoza", "+639171000004", "staff", 3, "bgc"),
    ("DSO-005", "Liza Ramos", "+639171000005", "staff", 3, "bgc"),
    ("DSO-006", "Carlo Villanueva", "+639171000006", "emt", 1, "ortigas"),
    ("DSO-007", "Grace Lim", "+639171000007", "floor_warden", 2, "ortigas"),
    ("DSO-008", "Miguel Torres", "+639171000008", "staff", 3, "ortigas"),
]


class Command(BaseCommand):
    help = "Create demo locations, employees and a Safety Officer account."

    def add_arguments(self, parser):
        parser.add_argument("--admin-username", default="safetyofficer")
        parser.add_argument("--admin-email", default="safety@example.com")
        parser.add_argument(
            "--admin-password",
            default=None,
            help="Falls back to DJANGO_SUPERUSER_PASSWORD.",
        )
        parser.add_argument(
            "--skip-demo-data",
            action="store_true",
            help="Skip the demo locations/employees; only ensure the admin account. "
            "Use this once the personnel directory holds real data, so this "
            "command stays safe to run on every deploy without undoing edits.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options["skip_demo_data"]:
            self.stdout.write("Skipping demo locations/employees (--skip-demo-data).")
        else:
            locations = {}
            for spec in LOCATIONS:
                location, created = Location.objects.update_or_create(
                    code=spec["code"], defaults=spec
                )
                locations[spec["code"]] = location
                self.stdout.write(
                    f"{'Created' if created else 'Updated'} location {location.name}"
                )

            for emp_id, name, mobile, role, tier, location_code in EMPLOYEES:
                slug = name.lower().replace(" ", ".")
                Employee.objects.update_or_create(
                    employee_id=emp_id,
                    defaults={
                        "full_name": name,
                        "email": f"{slug}@example.com",
                        "mobile_number": mobile,
                        "location": locations[location_code],
                        "role": role,
                        "escalation_tier": tier,
                        "department": "Digital Service Operations",
                    },
                )
            self.stdout.write(f"Seeded {len(EMPLOYEES)} employees.")

        password = options["admin_password"] or os.getenv("DJANGO_SUPERUSER_PASSWORD")
        if not password:
            self.stdout.write(
                self.style.WARNING(
                    "No admin password given; skipping the Safety Officer account. "
                    "Pass --admin-password or set DJANGO_SUPERUSER_PASSWORD."
                )
            )
            return

        User = get_user_model()
        user, created = User.objects.get_or_create(
            username=options["admin_username"],
            defaults={"email": options["admin_email"], "is_staff": True, "is_superuser": True},
        )
        user.set_password(password)
        user.is_staff = True
        user.is_superuser = True
        user.save()
        self.stdout.write(
            self.style.SUCCESS(
                f"{'Created' if created else 'Updated'} Safety Officer account "
                f"'{user.username}'."
            )
        )
