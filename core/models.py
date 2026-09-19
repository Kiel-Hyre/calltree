"""Domain models for the Automated Call Tree System.

The relational layer is the system of record and the audit trail. Live safety
status is mirrored into Cloud Firestore by ``core.datastore`` so the dashboard
can read it in real time without hammering the transactional database.
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


def new_token() -> str:
    """URL-safe token for an employee's one-tap web status link."""
    return secrets.token_urlsafe(24)


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Location(TimeStampedModel):
    """A geofenced work site of the DSO Team.

    ``radius_km`` is the risk radius used by the Trigger Filter: a seismic
    event within this distance of the site, at or above ``min_magnitude``,
    activates the call tree for everyone assigned here.
    """

    name = models.CharField(max_length=120, unique=True)
    code = models.SlugField(max_length=32, unique=True)
    address = models.CharField(max_length=255, blank=True)
    latitude = models.FloatField(
        validators=[MinValueValidator(-90.0), MaxValueValidator(90.0)]
    )
    longitude = models.FloatField(
        validators=[MinValueValidator(-180.0), MaxValueValidator(180.0)]
    )
    radius_km = models.FloatField(
        default=settings.TRIGGER_RADIUS_KM,
        validators=[MinValueValidator(0.1)],
        help_text="Risk radius in kilometres used by the Trigger Filter.",
    )
    min_magnitude = models.FloatField(
        default=settings.TRIGGER_MIN_MAGNITUDE,
        validators=[MinValueValidator(0.0), MaxValueValidator(10.0)],
        help_text="Minimum USGS magnitude that activates the call tree here.",
    )
    evacuation_instruction = models.TextField(
        blank=True,
        help_text="Location-specific guidance appended to every alert "
        "(assembly point, stairwell, floor warden).",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Employee(TimeStampedModel):
    """A member of the Digital Service Operations Team."""

    class Role(models.TextChoices):
        STAFF = "staff", "DSO Staff"
        FLOOR_WARDEN = "floor_warden", "Floor Warden"
        EMT = "emt", "Emergency Management Team"
        SAFETY_OFFICER = "safety_officer", "Safety Officer"

    employee_id = models.CharField(max_length=32, unique=True)
    full_name = models.CharField(max_length=160)
    email = models.EmailField(blank=True)
    # E.164 without the plus, e.g. 639171234567 - the format M360 expects.
    mobile_number = models.CharField(
        max_length=20,
        help_text="Mobile number in E.164 form, e.g. +639171234567.",
    )
    location = models.ForeignKey(
        Location, on_delete=models.PROTECT, related_name="employees"
    )
    department = models.CharField(max_length=120, blank=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.STAFF)
    # Escalation order within a location: lower numbers are contacted first and
    # are the ones the EMT chases when a branch of the tree goes quiet.
    escalation_tier = models.PositiveSmallIntegerField(default=1)
    supervisor = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="direct_reports",
    )
    sms_opt_in = models.BooleanField(default=True)
    email_opt_in = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["escalation_tier", "full_name"]
        indexes = [models.Index(fields=["location", "is_active"])]

    def __str__(self) -> str:
        return f"{self.full_name} ({self.employee_id})"

    @property
    def is_emt(self) -> bool:
        return self.role in {self.Role.EMT, self.Role.SAFETY_OFFICER}


class SeismicEvent(TimeStampedModel):
    """An earthquake reported by the USGS Earthquake Notification Service."""

    class Source(models.TextChoices):
        USGS_FEED = "usgs_feed", "USGS GeoJSON feed"
        USGS_WEBHOOK = "usgs_webhook", "USGS ENS webhook"
        USGS_EMAIL = "usgs_email", "USGS ENS email alert"
        MANUAL = "manual", "Manual / simulated"

    usgs_id = models.CharField(
        max_length=64,
        unique=True,
        help_text="USGS event id; the idempotency key for repeated feed reads.",
    )
    magnitude = models.FloatField()
    place = models.CharField(max_length=255, blank=True)
    latitude = models.FloatField()
    longitude = models.FloatField()
    depth_km = models.FloatField(null=True, blank=True)
    occurred_at = models.DateTimeField(db_index=True)
    source = models.CharField(
        max_length=20, choices=Source.choices, default=Source.USGS_FEED
    )
    detail_url = models.URLField(blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    evaluated_at = models.DateTimeField(null=True, blank=True)
    triggered = models.BooleanField(
        default=False, help_text="Whether the Trigger Filter activated a call tree."
    )
    evaluation_note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-occurred_at"]

    def __str__(self) -> str:
        return f"M{self.magnitude} {self.place or 'unknown'} @ {self.occurred_at:%Y-%m-%d %H:%M}"


class Drill(TimeStampedModel):
    """One activation of the call tree: a scheduled drill or a live incident."""

    class Kind(models.TextChoices):
        DRILL = "drill", "Scheduled drill"
        INCIDENT = "incident", "Live seismic incident"
        TEST = "test", "System test"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    class TriggerMode(models.TextChoices):
        MANUAL = "manual", "Manual (Safety Officer)"
        AUTOMATIC = "automatic", "Automatic (USGS trigger)"

    name = models.CharField(max_length=160)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.DRILL)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True
    )
    trigger_mode = models.CharField(
        max_length=16, choices=TriggerMode.choices, default=TriggerMode.MANUAL
    )
    seismic_event = models.ForeignKey(
        SeismicEvent,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="drills",
    )
    locations = models.ManyToManyField(Location, related_name="drills")
    message_template = models.TextField(
        blank=True,
        help_text="Alert body. Placeholders: {name}, {location}, {magnitude}, "
        "{place}, {instruction}, {link}.",
    )
    response_window_minutes = models.PositiveIntegerField(
        default=settings.DEFAULT_RESPONSE_WINDOW_MINUTES,
        help_text="Compliance window. Non-responders are flagged after this.",
    )
    reminder_interval_minutes = models.PositiveIntegerField(
        default=settings.DEFAULT_REMINDER_INTERVAL_MINUTES
    )
    max_reminders = models.PositiveSmallIntegerField(
        default=settings.DEFAULT_MAX_REMINDERS
    )
    send_sms = models.BooleanField(default=True)
    send_email = models.BooleanField(default=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="initiated_drills",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.name

    @property
    def deadline(self):
        if not self.started_at:
            return None
        return self.started_at + timedelta(minutes=self.response_window_minutes)

    @property
    def is_window_open(self) -> bool:
        deadline = self.deadline
        return bool(deadline and timezone.now() <= deadline)


class DrillParticipant(TimeStampedModel):
    """An employee's accountability record for one drill.

    This is the row the dashboard headcount counts and the row the escalation
    loop reads: it holds the safety status, when it arrived, and how many
    reminders have already gone out.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Awaiting response"
        NOTIFIED = "notified", "Alert delivered"
        SAFE = "safe", "Safe"
        HELP = "help", "Needs help"
        UNREACHABLE = "unreachable", "Unreachable"
        NON_COMPLIANT = "non_compliant", "Non-compliant"

    RESPONDED_STATUSES = {Status.SAFE, Status.HELP}

    drill = models.ForeignKey(Drill, on_delete=models.CASCADE, related_name="participants")
    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name="participations"
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    status_token = models.CharField(max_length=64, default=new_token, unique=True)
    notified_at = models.DateTimeField(null=True, blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    response_channel = models.CharField(max_length=16, blank=True)
    response_text = models.CharField(max_length=255, blank=True)
    reminders_sent = models.PositiveSmallIntegerField(default=0)
    escalated_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["employee__escalation_tier", "employee__full_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["drill", "employee"], name="unique_participant_per_drill"
            )
        ]
        indexes = [models.Index(fields=["drill", "status"])]

    def __str__(self) -> str:
        return f"{self.employee.full_name} - {self.get_status_display()}"

    @property
    def has_responded(self) -> bool:
        return self.status in self.RESPONDED_STATUSES

    @property
    def response_latency_seconds(self) -> float | None:
        """Seconds from alert delivery to the employee's reply."""
        if not (self.notified_at and self.responded_at):
            return None
        return (self.responded_at - self.notified_at).total_seconds()

    @property
    def status_url(self) -> str:
        base = settings.PUBLIC_BASE_URL.rstrip("/")
        return f"{base}/status/{self.status_token}"


class Notification(TimeStampedModel):
    """A single outbound message. One row per channel, per attempt.

    Delivery timestamps here are what the latency measurement in the testing
    procedure reads: ``queued_at`` is the trigger action, ``sent_at`` is when
    the gateway accepted the message.
    """

    class Channel(models.TextChoices):
        SMS = "sms", "SMS (M360)"
        EMAIL = "email", "Email (SMTP)"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped"

    class Purpose(models.TextChoices):
        INITIAL = "initial", "Initial alert"
        REMINDER = "reminder", "Reminder"
        ESCALATION = "escalation", "EMT escalation"
        ALL_CLEAR = "all_clear", "All clear"

    participant = models.ForeignKey(
        DrillParticipant, on_delete=models.CASCADE, related_name="notifications"
    )
    channel = models.CharField(max_length=10, choices=Channel.choices)
    purpose = models.CharField(
        max_length=16, choices=Purpose.choices, default=Purpose.INITIAL
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.QUEUED, db_index=True
    )
    recipient = models.CharField(max_length=255)
    body = models.TextField()
    queued_at = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(null=True, blank=True)
    provider_message_id = models.CharField(max_length=128, blank=True)
    error_message = models.CharField(max_length=500, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["-queued_at"]
        indexes = [models.Index(fields=["participant", "channel"])]

    def __str__(self) -> str:
        return f"{self.get_channel_display()} -> {self.recipient} ({self.status})"

    @property
    def dispatch_latency_seconds(self) -> float | None:
        if not self.sent_at:
            return None
        return (self.sent_at - self.queued_at).total_seconds()


class InboundMessage(TimeStampedModel):
    """A raw inbound SMS from the M360 webhook, kept before interpretation.

    Storing the raw payload first means a reply that cannot be matched to a
    participant is still auditable rather than silently dropped.
    """

    sender = models.CharField(max_length=32, db_index=True)
    text = models.CharField(max_length=500)
    received_at = models.DateTimeField(default=timezone.now)
    raw_payload = models.JSONField(default=dict, blank=True)
    participant = models.ForeignKey(
        DrillParticipant,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="inbound_messages",
    )
    processed = models.BooleanField(default=False)
    process_note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-received_at"]

    def __str__(self) -> str:
        return f"{self.sender}: {self.text[:40]}"


class AuditLog(TimeStampedModel):
    """Append-only record of operator and system actions."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    action = models.CharField(max_length=64, db_index=True)
    target = models.CharField(max_length=160, blank=True)
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.action} {self.target}"
