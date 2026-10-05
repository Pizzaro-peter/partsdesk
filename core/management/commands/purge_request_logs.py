"""Remove aged request-metadata logs after an explicit retention decision."""
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import models, transaction
from django.utils import timezone

from core.models import RequestLog


class Command(BaseCommand):
    help = "Preview or delete request metadata older than the chosen retention period."

    def add_arguments(self, parser):
        parser.add_argument("--days", required=True, type=int)
        parser.add_argument("--delete", action="store_true", help="Perform the deletion; otherwise only preview.")

    def handle(self, **options):
        days = options["days"]
        if days < 1:
            raise CommandError("--days must be a positive integer.")

        cutoff = timezone.now() - timedelta(days=days)
        records = RequestLog.objects.filter(created_at__lt=cutoff)
        count = records.count()
        if not options["delete"]:
            self.stdout.write(f"Dry run: {count} request log(s) are older than {days} days. Re-run with --delete to remove them.")
            return

        with transaction.atomic():
            deleted, _ = models.QuerySet.delete(records)
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} request log(s) older than {days} days. Audit and business records were not changed."))
