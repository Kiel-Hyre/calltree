"""Drain queued notifications and sweep active drills.

This is the worker that keeps the reminder and escalation loops running when
the system is not driven by Cloud Scheduler. It is what docker-compose runs
as the ``worker`` service.

    python manage.py run_dispatcher --loop --interval 20
"""

from __future__ import annotations

import time

from django.core.management.base import BaseCommand

from core.models import Drill, Notification
from dissemination.services import send_now
from engine.services import sweep_drill


class Command(BaseCommand):
    help = "Send queued notifications and chase non-responders on active drills."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true")
        parser.add_argument("--interval", type=int, default=20)
        parser.add_argument(
            "--batch", type=int, default=200, help="Max notifications per pass."
        )
        parser.add_argument(
            "--no-sweep",
            action="store_true",
            help="Only send queued messages; skip reminders and escalation.",
        )

    def handle(self, *args, **options):
        interval = max(5, options["interval"])

        while True:
            try:
                self._pass(options["batch"], sweep=not options["no_sweep"])
            except Exception as exc:  # a worker must not die on one bad pass
                self.stderr.write(self.style.ERROR(f"Dispatcher pass failed: {exc}"))

            if not options["loop"]:
                return
            time.sleep(interval)

    def _pass(self, batch: int, *, sweep: bool) -> None:
        queued_ids = list(
            Notification.objects.filter(status=Notification.Status.QUEUED)
            .order_by("queued_at")
            .values_list("pk", flat=True)[:batch]
        )
        if queued_ids:
            summary = send_now(queued_ids)
            self.stdout.write(
                f"Dispatched {summary.total}: {summary.sent} sent, "
                f"{summary.failed} failed, {summary.skipped} skipped."
            )

        if not sweep:
            return

        for drill in Drill.objects.filter(status=Drill.Status.ACTIVE):
            result = sweep_drill(drill)
            if result.reminders or result.flagged:
                self.stdout.write(
                    f"Drill #{drill.pk}: {result.reminders} reminder(s), "
                    f"{result.flagged} flagged, {result.escalated} escalation message(s)."
                )
