"""REST API consumed by the Vue 3 dashboard."""

from __future__ import annotations

import csv
import logging

from django.contrib.auth import authenticate, login, logout
from django.db.models import Count
from django.http import HttpResponse
from django.middleware.csrf import get_token
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accountability.services import (
    ResolutionError,
    handle_web_response,
    resolve_status_token,
)
from api.serializers import (
    AuditLogSerializer,
    DrillSerializer,
    EmployeeSerializer,
    InboundMessageSerializer,
    LocationSerializer,
    LoginSerializer,
    NotificationSerializer,
    ParticipantSerializer,
    SeismicEventSerializer,
    SimulateEventSerializer,
    UserSerializer,
    WebResponseSerializer,
)
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
from core.services import audit
from dissemination.services import health_check
from engine.proximity import evaluate_event
from engine.services import (
    CallTreeError,
    activate_drill,
    complete_drill,
    drill_statistics,
    evaluate_seismic_event,
    sweep_drill,
)

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Session authentication
# --------------------------------------------------------------------------


class SessionView(APIView):
    """Report who is signed in and hand the SPA a CSRF token."""

    permission_classes = [AllowAny]

    def get(self, request):
        csrf_token = get_token(request)
        if request.user.is_authenticated:
            return Response(
                {"authenticated": True, "user": UserSerializer(request.user).data, "csrf_token": csrf_token}
            )
        return Response({"authenticated": False, "user": None, "csrf_token": csrf_token})


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = authenticate(
            request,
            username=serializer.validated_data["username"],
            password=serializer.validated_data["password"],
        )
        if user is None:
            audit("auth.failed", target=serializer.validated_data["username"])
            return Response(
                {"detail": "Incorrect username or password."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        if not user.is_staff:
            return Response(
                {"detail": "This account is not authorised for the Safety Officer portal."},
                status=status.HTTP_403_FORBIDDEN,
            )

        login(request, user)
        audit("auth.login", actor=user, target=user.get_username())
        return Response({"authenticated": True, "user": UserSerializer(user).data})


class LogoutView(APIView):
    def post(self, request):
        audit("auth.logout", actor=request.user, target=request.user.get_username())
        logout(request)
        return Response({"authenticated": False})


# --------------------------------------------------------------------------
# Directory
# --------------------------------------------------------------------------


class LocationViewSet(viewsets.ModelViewSet):
    serializer_class = LocationSerializer
    queryset = Location.objects.annotate(employee_count=Count("employees"))

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.query_params.get("active") == "true":
            queryset = queryset.filter(is_active=True)
        return queryset


class EmployeeViewSet(viewsets.ModelViewSet):
    serializer_class = EmployeeSerializer
    queryset = Employee.objects.select_related("location")

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params
        if location := params.get("location"):
            queryset = queryset.filter(location_id=location)
        if params.get("active") == "true":
            queryset = queryset.filter(is_active=True)
        if search := params.get("search"):
            queryset = queryset.filter(full_name__icontains=search)
        return queryset


# --------------------------------------------------------------------------
# Seismic events
# --------------------------------------------------------------------------


class SeismicEventViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = SeismicEventSerializer
    queryset = SeismicEvent.objects.all()

    @action(detail=True, methods=["get"])
    def proximity(self, request, pk=None):
        """Show the Trigger Filter verdict per location for this event."""
        event = self.get_object()
        matches = evaluate_event(event, Location.objects.filter(is_active=True))
        return Response(
            [
                {
                    "location_id": match.location.pk,
                    "location": match.location.name,
                    "distance_km": round(match.distance_km, 2),
                    "magnitude_ok": match.magnitude_ok,
                    "within_radius": match.within_radius,
                    "triggered": match.triggered,
                    "reason": match.reason,
                }
                for match in matches
            ]
        )

    @action(detail=True, methods=["post"])
    def evaluate(self, request, pk=None):
        """Re-run the Trigger Filter, optionally activating a call tree."""
        event = self.get_object()
        auto = str(request.data.get("auto_trigger", "")).lower() in {"1", "true", "yes"}
        matches, drill = evaluate_seismic_event(event, auto_trigger=auto)
        audit(
            "event.evaluated",
            actor=request.user,
            target=f"event:{event.pk}",
            auto_trigger=auto,
            drill=drill.pk if drill else None,
        )
        return Response(
            {
                "event": SeismicEventSerializer(event).data,
                "triggered_locations": [m.location.name for m in matches if m.triggered],
                "drill": DrillSerializer(drill).data if drill else None,
            }
        )


class SimulateEventView(APIView):
    """Inject a simulated USGS alert - step 2 of the operational procedure."""

    def post(self, request):
        serializer = SimulateEventSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        now = timezone.now()
        event = SeismicEvent.objects.create(
            usgs_id=f"sim-{now:%Y%m%d%H%M%S%f}",
            magnitude=data["magnitude"],
            place=data.get("place") or "Simulated drill epicentre",
            latitude=data["latitude"],
            longitude=data["longitude"],
            depth_km=data.get("depth_km"),
            occurred_at=now,
            source=SeismicEvent.Source.MANUAL,
        )
        matches, drill = evaluate_seismic_event(
            event, auto_trigger=data["auto_trigger"]
        )
        audit(
            "event.simulated",
            actor=request.user,
            target=f"event:{event.pk}",
            magnitude=data["magnitude"],
        )
        return Response(
            {
                "event": SeismicEventSerializer(event).data,
                "matches": [
                    {
                        "location": m.location.name,
                        "distance_km": round(m.distance_km, 2),
                        "triggered": m.triggered,
                        "reason": m.reason,
                    }
                    for m in matches
                ],
                "drill": DrillSerializer(drill).data if drill else None,
            },
            status=status.HTTP_201_CREATED,
        )


# --------------------------------------------------------------------------
# Drills
# --------------------------------------------------------------------------


class DrillViewSet(viewsets.ModelViewSet):
    serializer_class = DrillSerializer
    queryset = (
        Drill.objects.select_related("seismic_event", "initiated_by")
        .prefetch_related("locations")
        .annotate(participant_count=Count("participants", distinct=True))
    )

    def get_queryset(self):
        queryset = super().get_queryset()
        if state := self.request.query_params.get("status"):
            queryset = queryset.filter(status=state)
        return queryset

    def perform_destroy(self, instance):
        if instance.status == Drill.Status.ACTIVE:
            raise CallTreeError("Cancel the drill before deleting it.")
        audit("drill.deleted", actor=self.request.user, target=f"drill:{instance.pk}")
        instance.delete()

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        """The 'Initiate Call Tree Alert' button."""
        drill = self.get_object()
        try:
            result = activate_drill(drill, actor=request.user)
        except CallTreeError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "drill": DrillSerializer(result.drill).data,
                "participants": result.participants,
                "notifications_queued": result.notifications_queued,
            }
        )

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        drill = self.get_object()
        try:
            complete_drill(drill, actor=request.user)
        except CallTreeError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(DrillSerializer(drill).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        drill = self.get_object()
        drill.status = Drill.Status.CANCELLED
        drill.completed_at = timezone.now()
        drill.save(update_fields=["status", "completed_at", "updated_at"])
        audit("drill.cancelled", actor=request.user, target=f"drill:{drill.pk}")
        return Response(DrillSerializer(drill).data)

    @action(detail=True, methods=["post"])
    def sweep(self, request, pk=None):
        """Re-broadcast to non-responders and escalate past the deadline."""
        drill = self.get_object()
        result = sweep_drill(drill)
        return Response(
            {
                "reminders": result.reminders,
                "flagged": result.flagged,
                "escalated": result.escalated,
            }
        )

    @action(detail=True, methods=["get"])
    def monitor(self, request, pk=None):
        """Everything the live dashboard polls: stats plus the roster."""
        drill = self.get_object()
        participants = drill.participants.select_related(
            "employee", "employee__location"
        )
        return Response(
            {
                "drill": DrillSerializer(drill).data,
                "statistics": drill_statistics(drill),
                "participants": ParticipantSerializer(participants, many=True).data,
                "server_time": timezone.now(),
            }
        )

    @action(detail=True, methods=["get"])
    def notifications(self, request, pk=None):
        drill = self.get_object()
        queryset = Notification.objects.filter(
            participant__drill=drill
        ).select_related("participant__employee")
        page = self.paginate_queryset(queryset)
        serializer = NotificationSerializer(page or queryset, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    @action(detail=True, methods=["get"], url_path="report.csv")
    def report_csv(self, request, pk=None):
        """Post-event compliance export for the management audit."""
        drill = self.get_object()
        response = HttpResponse(content_type="text/csv")
        filename = f"drill-{drill.pk}-compliance.csv"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'

        writer = csv.writer(response)
        writer.writerow(
            [
                "Employee ID",
                "Name",
                "Location",
                "Role",
                "Escalation tier",
                "Status",
                "Notified at",
                "Responded at",
                "Response latency (s)",
                "Channel",
                "Reminders sent",
                "Reply text",
            ]
        )
        for participant in drill.participants.select_related(
            "employee", "employee__location"
        ):
            writer.writerow(
                [
                    participant.employee.employee_id,
                    participant.employee.full_name,
                    participant.employee.location.name,
                    participant.employee.get_role_display(),
                    participant.employee.escalation_tier,
                    participant.get_status_display(),
                    participant.notified_at.isoformat() if participant.notified_at else "",
                    participant.responded_at.isoformat() if participant.responded_at else "",
                    participant.response_latency_seconds or "",
                    participant.response_channel,
                    participant.reminders_sent,
                    participant.response_text,
                ]
            )

        audit("drill.exported", actor=request.user, target=f"drill:{drill.pk}")
        return response


class InboundMessageViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = InboundMessageSerializer
    queryset = InboundMessage.objects.select_related("participant__employee")


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AuditLogSerializer
    queryset = AuditLog.objects.select_related("actor")


# --------------------------------------------------------------------------
# Overview + health
# --------------------------------------------------------------------------


class OverviewView(APIView):
    """Landing-page summary for the dashboard."""

    def get(self, request):
        active = list(
            Drill.objects.filter(status=Drill.Status.ACTIVE).prefetch_related("locations")
        )
        return Response(
            {
                "counts": {
                    "employees": Employee.objects.filter(is_active=True).count(),
                    "locations": Location.objects.filter(is_active=True).count(),
                    "drills": Drill.objects.count(),
                    "active_drills": len(active),
                },
                "active_drills": [
                    {
                        "drill": DrillSerializer(drill).data,
                        "statistics": drill_statistics(drill),
                    }
                    for drill in active
                ],
                "recent_events": SeismicEventSerializer(
                    SeismicEvent.objects.all()[:5], many=True
                ).data,
                "recent_activity": AuditLogSerializer(
                    AuditLog.objects.select_related("actor")[:10], many=True
                ).data,
            }
        )


class HealthCheckView(APIView):
    """Pre-drill System Health Verification."""

    def get(self, request):
        return Response(health_check())


@api_view(["GET"])
@permission_classes([AllowAny])
def liveness(request):
    """Unauthenticated probe for Cloud Run / Docker health checks."""
    return Response({"status": "ok", "time": timezone.now()})


# --------------------------------------------------------------------------
# Public employee status page (no authentication - the token is the secret)
# --------------------------------------------------------------------------


class PublicStatusView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, token):
        try:
            participant = resolve_status_token(token)
        except ResolutionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_404_NOT_FOUND)

        drill = participant.drill
        event = drill.seismic_event
        return Response(
            {
                "employee_name": participant.employee.full_name,
                "location": participant.employee.location.name,
                "instruction": participant.employee.location.evacuation_instruction,
                "drill_name": drill.name,
                "drill_status": drill.status,
                "drill_kind": drill.kind,
                "is_open": drill.status == Drill.Status.ACTIVE,
                "status": participant.status,
                "status_display": participant.get_status_display(),
                "responded_at": participant.responded_at,
                "magnitude": event.magnitude if event else None,
                "place": event.place if event else None,
            }
        )

    def post(self, request, token):
        serializer = WebResponseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            participant = handle_web_response(token, serializer.validated_data["status"])
        except ResolutionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "status": participant.status,
                "status_display": participant.get_status_display(),
                "responded_at": participant.responded_at,
            }
        )


class ParticipantOverrideView(APIView):
    """Manual reconciliation: the Safety Officer sets a status by hand.

    Used when someone reports in over the radio or in person, which the
    two-way SMS loop can never see.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        participant = DrillParticipant.objects.select_related("employee").filter(pk=pk).first()
        if participant is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        new_status = request.data.get("status")
        valid = {choice for choice, _ in DrillParticipant.Status.choices}
        if new_status not in valid:
            return Response(
                {"detail": f"Status must be one of: {', '.join(sorted(valid))}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        participant.status = new_status
        participant.response_channel = "manual"
        participant.note = str(request.data.get("note", ""))[:255]
        if new_status in DrillParticipant.RESPONDED_STATUSES and not participant.responded_at:
            participant.responded_at = timezone.now()
        participant.save(
            update_fields=[
                "status",
                "response_channel",
                "note",
                "responded_at",
                "updated_at",
            ]
        )
        audit(
            "participant.override",
            actor=request.user,
            target=f"participant:{participant.pk}",
            status=new_status,
        )
        return Response(ParticipantSerializer(participant).data)
