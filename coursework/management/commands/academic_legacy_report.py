"""Read-only report of data created by retired academic paths. Nothing is
modified: these records must be re-entered through the authoritative
Academic services (or reviewed) by the PhD Cell / COE."""
from django.core.management.base import BaseCommand

from core.models import ApprovalRequest
from coursework.models import CourseAttempt


class Command(BaseCommand):
    help = "List legacy attempt records and superseded third-attempt approvals (read-only)."

    def handle(self, *args, **options):
        legacy = CourseAttempt.objects.filter(status=CourseAttempt.Status.LEGACY).select_related("scholar", "course")
        overrides = ApprovalRequest.objects.filter(chain__code="SUPERSEDED_COURSEWORK_THIRD_ATTEMPT")
        self.stdout.write(f"Legacy attempt records (not counted by the authoritative record): {legacy.count()}")
        for a in legacy:
            self.stdout.write(f"  {a.scholar.prn} {a.course.code} attempt {a.attempt_no} "
                              f"grade={a.grade or '-'} override={a.override_id or '-'}")
        self.stdout.write(f"Superseded DC→VC third-attempt approval requests: {overrides.count()}")
        for r in overrides:
            self.stdout.write(f"  #{r.pk} {r.summary} status={r.status}")
        if legacy.exists() or overrides.exists():
            self.stdout.write("Action: re-enter through coursework.academic services / ThirdAttemptCase.")
