"""WSGI entry point (gunicorn / Cloud Run)."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "calltree.settings")

application = get_wsgi_application()
