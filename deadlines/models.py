from django.conf import settings
from django.db import models
from django.utils import timezone


class AcademicEvent(models.Model):
    """Global clock: an organisation-wide calendar event (RPET, exams, ...)."""

    key = models.CharField(max_length=80, unique=True)
    kind = models.CharField(max_length=40)
    title = models.CharField(max_length=255)
    start = models.DateField()
    end = models.DateField()
    confirmed = models.BooleanField(default=False, help_text="R&D office has confirmed the generated date")

    class Meta:
        ordering = ["start"]

    def __str__(self):
        return f"{self.start}: {self.title}"


class Deadline(models.Model):
    """Per-scholar clock: a rolling deadline derived from the scholar's own dates.
    Rows are recomputed by the engine; ``key`` is stable across recomputation."""

    scholar = models.ForeignKey("scholars.Scholar", on_delete=models.CASCADE, related_name="deadlines")
    key = models.CharField(max_length=80)
    kind = models.CharField(max_length=40)
    title = models.CharField(max_length=255)
    due_date = models.DateField()
    met = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["due_date"]
        constraints = [models.UniqueConstraint(fields=["scholar", "key"], name="uniq_scholar_deadline")]

    def __str__(self):
        return f"{self.scholar.prn}: {self.title} ({self.due_date})"

    def state(self, today):
        if self.met:
            return "met"
        return "overdue" if self.due_date < today else "open"


class Notification(models.Model):
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="phd_notifications")
    deadline = models.ForeignKey(Deadline, null=True, blank=True, on_delete=models.CASCADE)
    event = models.ForeignKey(AcademicEvent, null=True, blank=True, on_delete=models.CASCADE)
    tier = models.SmallIntegerField(help_text="Days before due (0 = overdue)")
    message = models.CharField(max_length=500)
    created_at = models.DateTimeField(default=timezone.now)
    emailed_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["recipient", "deadline", "tier"], condition=models.Q(deadline__isnull=False),
                                    name="uniq_deadline_notification"),
            models.UniqueConstraint(fields=["recipient", "event", "tier"], condition=models.Q(event__isnull=False),
                                    name="uniq_event_notification"),
        ]
