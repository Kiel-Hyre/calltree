"""Root URL configuration.

/api/    -> Django REST Framework
/admin/  -> Django admin (directory upkeep, audit trail)
/*       -> the compiled Vue 3 SPA, which owns client-side routing
"""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path

from calltree.spa import SpaView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("api.urls")),
    # Anything else is an SPA route. The regex deliberately excludes the
    # static prefix so a missing asset 404s instead of returning index.html.
    re_path(
        rf"^(?!api/|admin/|{settings.STATIC_URL.lstrip('/')}).*$",
        SpaView.as_view(),
        name="spa",
    ),
]
