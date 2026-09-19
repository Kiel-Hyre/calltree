"""Django admin: the 'administrative terminal' used for directory upkeep.

Day-to-day drill operation happens in the Vue dashboard; this is the fallback
surface for bulk directory edits and for inspecting the audit trail.
"""

from django.contrib import admin

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


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "latitude", "longitude", "radius_km", "min_magnitude", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "code", "address")


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ("employee_id", "full_name", "mobile_number", "location", "role", "escalation_tier", "is_active")
    list_filter = ("location", "role", "is_active", "sms_opt_in", "email_opt_in")
    search_fields = ("employee_id", "full_name", "email", "mobile_number")
    autocomplete_fields = ("location", "supervisor")


@admin.register(SeismicEvent)
class SeismicEventAdmin(admin.ModelAdmin):
    list_display = ("usgs_id", "magnitude", "place", "occurred_at", "source", "triggered")
    list_filter = ("source", "triggered")
    search_fields = ("usgs_id", "place")
    readonly_fields = ("raw_payload",)


class DrillParticipantInline(admin.TabularInline):
    model = DrillParticipant
    extra = 0
    fields = ("employee", "status", "notified_at", "responded_at", "reminders_sent")
    readonly_fields = ("notified_at", "responded_at", "reminders_sent")


@admin.register(Drill)
class DrillAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "status", "trigger_mode", "started_at", "completed_at")
    list_filter = ("kind", "status", "trigger_mode")
    search_fields = ("name",)
    filter_horizontal = ("locations",)
    inlines = [DrillParticipantInline]


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("recipient", "channel", "purpose", "status", "queued_at", "sent_at", "attempts")
    list_filter = ("channel", "purpose", "status")
    search_fields = ("recipient", "provider_message_id")


@admin.register(InboundMessage)
class InboundMessageAdmin(admin.ModelAdmin):
    list_display = ("sender", "text", "received_at", "processed", "process_note")
    list_filter = ("processed",)
    search_fields = ("sender", "text")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "target", "actor")
    list_filter = ("action",)
    search_fields = ("action", "target")
    readonly_fields = ("created_at", "action", "target", "actor", "detail")
