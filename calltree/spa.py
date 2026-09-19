"""Serve the compiled Vue 3 single-page application."""

from __future__ import annotations

from django.conf import settings
from django.http import HttpResponse
from django.views import View

_MISSING_BUILD = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Front end not built</title>
<style>
  body{font-family:ui-sans-serif,system-ui,sans-serif;max-width:40rem;margin:4rem auto;
       padding:0 1.5rem;line-height:1.6;color:#0f172a;background:#f8fafc}
  code{background:#e2e8f0;padding:.15rem .4rem;border-radius:.25rem}
  h1{font-size:1.35rem}
</style></head><body>
<h1>The Vue front end has not been built yet</h1>
<p>The API is running. To build the dashboard:</p>
<pre><code>cd frontend
npm install
npm run build</code></pre>
<p>For hot reload during development run <code>npm run dev</code> and use the
Vite server on port 5173, which proxies <code>/api</code> to Django.</p>
<p>The REST API is available at <code>/api/</code> and the Django admin at
<code>/admin/</code>.</p>
</body></html>"""


class SpaView(View):
    """Return dist/index.html for every non-API route.

    The file is read per request rather than cached at import time so that a
    rebuild during development is picked up without restarting Django.
    """

    def get(self, request, *args, **kwargs):
        index = settings.SPA_INDEX_FILE
        if not index.is_file():
            return HttpResponse(_MISSING_BUILD, status=501)

        response = HttpResponse(index.read_text(encoding="utf-8"))
        # The shell is tiny and its asset links are content-hashed; never let
        # a proxy pin an old shell that points at deleted bundles.
        response["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response
