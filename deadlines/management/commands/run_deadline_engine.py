from datetime import date

from django.core.management.base import BaseCommand

from deadlines import engine


class Command(BaseCommand):
    help = "Sync the global calendar and every scholar's rolling deadlines, then emit reminders. Run daily."

    def add_arguments(self, parser):
        parser.add_argument("--today", type=date.fromisoformat, default=None, help="Override today (YYYY-MM-DD)")

    def handle(self, *args, today=None, **options):
        result = engine.run(today or date.today())
        self.stdout.write(self.style.SUCCESS(", ".join(f"{k}={v}" for k, v in result.items())))
