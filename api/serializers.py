"""DRF serializers for the Vue 3 dashboard."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from rest_framework import serializers

from core.models import (
    AuditLog,
    Drill,
    DrillParticipant,
    Employee,
    InboundMessage,
    Location,
    Notification,
    SeismicEvent,
)
from core.services import normalize_msisdn


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = get_user_model()
        fields = ["id", "username", "first_name", "last_name", "email", "is_staff", "is_superuser"]


class LocationSerializer(serializers.ModelSerializer):
    employee_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Location
        fields = [
            "id",
            "name",
            "code",
            "address",
            "latitude",
            "longitude",
            "radius_km",
            "min_magnitude",
            "evacuation_instruction",
            "is_active",
            "employee_count",
        ]


class EmployeeSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source="location.name", read_only=True)
    role_display = serializers.CharField(source="get_role_display", read_only=True)

    class Meta:
        model = Employee
        fields = [
            "id",
            "employee_id",
            "full_name",
            "email",
            "mobile_number",
            "location",
            "location_name",
            "department",
            "role",
            "role_display",
            "escalation_tier",
            "supervisor",
            "sms_opt_in",
            "email_opt_in",
            "is_active",
        ]

    def validate_mobile_number(self, value):
        if value and not normalize_msisdn(value):
            raise serializers.ValidationError(
                "Enter a reachable mobile number, e.g. +639171234567."
            )
        return value


class SeismicEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = SeismicEvent
        fields = [
            "id",
            "usgs_id",
            "magnitude",
            "place",
            "latitude",
            "longitude",
            "depth_km",
            "occurred_at",
            "source",
            "detail_url",
            "evaluated_at",
            "triggered",
            "evaluation_note",
        ]
        read_only_fields = ["evaluated_at", "triggered", "evaluation_note"]


class ParticipantSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    employee_code = serializers.CharField(source="employee.employee_id", read_only=True)
    location_name = serializers.CharField(source="employee.location.name", read_only=True)
    role = serializers.CharField(source="employee.role", read_only=True)
    escalation_tier = serializers.IntegerField(
        source="employee.escalation_tier", read_only=True
    )
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    response_latency_seconds = serializers.FloatField(read_only=True)
    status_url = serializers.CharField(read_only=True)

    class Meta:
        model = DrillParticipant
        fields = [
            "id",
            "employee",
            "employee_name",
            "employee_code",
            "location_name",
            "role",
            "escalation_tier",
            "status",
            "status_display",
            "notified_at",
            "responded_at",
            "response_channel",
            "response_text",
            "reminders_sent",
            "escalated_at",
            "response_latency_seconds",
            "status_url",
            "note",
        ]
        read_only_fields = fields


class NotificationSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(
        source="participant.employee.full_name", read_only=True
    )
    dispatch_latency_seconds = serializers.FloatField(read_only=True)

    class Meta:
        model = Notification
        fields = [
            "id",
            "participant",
            "employee_name",
            "channel",
            "purpose",
            "status",
            "recipient",
            "body",
            "queued_at",
            "sent_at",
            "provider_message_id",
            "error_message",
            "attempts",
            "dispatch_latency_seconds",
        ]
        read_only_fields = fields


class DrillSerializer(serializers.ModelSerializer):
    location_names = serializers.SerializerMethodField()
    initiated_by_name = serializers.CharField(
        source="initiated_by.get_username", read_only=True, default=""
    )
    seismic_event_detail = SeismicEventSerializer(source="seismic_event", read_only=True)
    deadline = serializers.DateTimeField(read_only=True)
    participant_count = serializers.IntegerField(read_only=True)
    # A call tree with no geofence in scope would notify nobody, so reject it
    # at the field with a message the dashboard can show as-is.
    locations = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Location.objects.all(),
        allow_empty=False,
        error_messages={"empty": "Select at least one location for the call tree."},
    )

    class Meta:
        model = Drill
        fields = [
            "id",
            "name",
            "kind",
            "status",
            "trigger_mode",
            "seismic_event",
            "seismic_event_detail",
            "locations",
            "location_names",
            "message_template",
            "response_window_minutes",
            "reminder_interval_minutes",
            "max_reminders",
            "send_sms",
            "send_email",
            "started_at",
            "completed_at",
            "deadline",
            "initiated_by_name",
            "participant_count",
            "created_at",
        ]
        read_only_fields = [
            "status",
            "trigger_mode",
            "started_at",
            "completed_at",
            "initiated_by_name",
        ]

    def get_location_names(self, obj) -> list[str]:
        return [location.name for location in obj.locations.all()]


class InboundMessageSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(
        source="participant.employee.full_name", read_only=True, default=""
    )

    class Meta:
        model = InboundMessage
        fields = [
            "id",
            "sender",
            "text",
            "received_at",
            "participant",
            "employee_name",
            "processed",
            "process_note",
        ]
        read_only_fields = fields


class AuditLogSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(
        source="actor.get_username", read_only=True, default="system"
    )

    class Meta:
        model = AuditLog
        fields = ["id", "created_at", "action", "target", "actor_name", "detail"]
        read_only_fields = fields


# --------------------------------------------------------------------------
# Action / input serializers
# --------------------------------------------------------------------------


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(style={"input_type": "password"}, trim_whitespace=False)


class SimulateEventSerializer(serializers.Serializer):
    """Lets the Safety Officer inject a simulated USGS alert for a drill."""

    magnitude = serializers.FloatField(min_value=0.0, max_value=10.0)
    latitude = serializers.FloatField(min_value=-90.0, max_value=90.0)
    longitude = serializers.FloatField(min_value=-180.0, max_value=180.0)
    place = serializers.CharField(max_length=255, required=False, allow_blank=True)
    depth_km = serializers.FloatField(required=False, allow_null=True)
    auto_trigger = serializers.BooleanField(default=False)


class WebResponseSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["safe", "help"])
