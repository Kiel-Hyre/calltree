"""API routes. Everything the SPA talks to lives under /api/."""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from api import views, webhooks

router = DefaultRouter()
router.register("locations", views.LocationViewSet, basename="location")
router.register("employees", views.EmployeeViewSet, basename="employee")
router.register("events", views.SeismicEventViewSet, basename="event")
router.register("drills", views.DrillViewSet, basename="drill")
router.register("inbound", views.InboundMessageViewSet, basename="inbound")
router.register("audit", views.AuditLogViewSet, basename="audit")

app_name = "api"

urlpatterns = [
    # Session
    path("auth/session/", views.SessionView.as_view(), name="session"),
    path("auth/login/", views.LoginView.as_view(), name="login"),
    path("auth/logout/", views.LogoutView.as_view(), name="logout"),
    # Operations
    path("overview/", views.OverviewView.as_view(), name="overview"),
    path("health/", views.HealthCheckView.as_view(), name="health"),
    path("healthz/", views.liveness, name="liveness"),
    path("events/simulate/", views.SimulateEventView.as_view(), name="simulate"),
    path(
        "participants/<int:pk>/override/",
        views.ParticipantOverrideView.as_view(),
        name="participant-override",
    ),
    # Public employee status link
    path("status/<str:token>/", views.PublicStatusView.as_view(), name="public-status"),
    # Machine-to-machine
    path("webhooks/usgs/", webhooks.UsgsWebhookView.as_view(), name="webhook-usgs"),
    path("webhooks/m360/", webhooks.M360InboundView.as_view(), name="webhook-m360"),
    path("webhooks/textbee/", webhooks.TextBeeInboundView.as_view(), name="webhook-textbee"),
    path("webhooks/pubsub/", webhooks.PubSubPushView.as_view(), name="webhook-pubsub"),
    path("", include(router.urls)),
]
