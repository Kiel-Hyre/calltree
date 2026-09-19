"""Poll the USGS GeoJSON feed and run the Trigger Filter on new events.

Run once from Cloud Scheduler, or with --loop as a long-lived worker:

    python manage.py poll_usgs
    python manage.py poll_usgs --loop --interval 60
"""

from __future__ import annotations

import time

from django.conf import settings
from django.core.management.base import BaseCommand

from core.models import SeismicEvent
from engine.services import evaluate_seismic_event
from ingestion.usgs import IngestionError, fetch_feed, store_event


class Command(BaseCommand):
    help = "Ingest earthquakes from the USGS Earthquake Notification Service feed."

    def add_arguments(self, parser):
        parser.add_argument("--url", default=None, help="Override USGS_FEED_URL.")
        parser.add_argument(
            "--loop", action="store_true", help="Keep polling instead of exiting."
        )
        parser.add_argument(
            "--interval",
            type=int,
            default=settings.USGS_POLL_INTERVAL_SECONDS,
            help="Seconds between polls when --loop is set.",
        )
        parser.add_argument(
            "--auto-trigger",
            action="store_true",
            help="Activate a call tree when the Trigger Filter matches, "
            "overriding AUTO_TRIGGER_ENABLED.",
        )

    def handle(self, *args, **options):
        auto = options["auto_trigger"] or settings.AUTO_TRIGGER_ENABLED
        interval = max(10, options["interval"])

        while True:
            try:
                self._poll_once(options["url"], auto)
            except IngestionError as exc:
                self.stderr.write(self.style.ERROR(str(exc)))
            except Exception as exc:  # keep a long-running poller alive
                self.stderr.write(self.style.ERROR(f"Unexpected polling error: {exc}"))

            if not options["loop"]:
                return
            time.sleep(interval)

    def _poll_once(self, url, auto_trigger: bool) -> None:
        parsed_events = fetch_feed(url)
        new_count = 0

        for parsed in parsed_events:
            event, created = store_event(parsed, source=SeismicEvent.Source.USGS_FEED)
            if not created and event.evaluated_at:
                continue
            new_count += 1
            matches, drill = evaluate_seismic_event(event, auto_trigger=auto_trigger)
            hits = [m for m in matches if m.triggered]
            if hits:
                self.stdout.write(
                    self.style.WARNING(
                        f"M{event.magnitude} {event.place} triggered "
                        f"{len(hits)} location(s)"
                        + (f" -> drill #{drill.pk}" if drill else " (auto-trigger off)")
                    )
                )

        self.stdout.write(
            f"Read {len(parsed_events)} event(s) from the feed; {new_count} newly evaluated."
        )
