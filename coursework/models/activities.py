"""Mandatory activity components (Industrial Training / Field Work,
Conference / Workshop, Research Seminar Presentation). They are evaluated by
SDRC; their required artefacts are configuration (activity.required_artefacts)."""
from django.db import models
from django.db.models import Q
from django.utils import timezone


class ActivitySubmission(models.Model):
    class Artefact(models.TextChoices):
        PLAN = "PLAN", "Training plan"
        LOGBOOK = "LOGBOOK", "Logbook"
        REPORT = "REPORT", "Report"
        POSTER = "POSTER", "Poster"
        REFLECTIVE_NOTE = "REFLECTIVE_NOTE", "Reflective note"
        PARTICIPATION_EVIDENCE = "PARTICIPATION_EVIDENCE", "Participation evidence"
        PRESENTATION = "PRESENTATION", "Seminar presentation"

    class Status(models.TextChoices):
        SUBMITTED = "SUBMITTED", "Submitted"
        ACCEPTED = "ACCEPTED", "Accepted by SDRC"
        RETURNED = "RETURNED", "Returned for revision"

    enrollment = models.ForeignKey("coursework.ScholarCourseEnrollment", on_delete=models.PROTECT,
                                   related_name="activity_submissions")
    artefact = models.CharField(max_length=22, choices=Artefact.choices)
    document = models.FileField(upload_to="coursework/activities/", blank=True)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=9, choices=Status.choices, default=Status.SUBMITTED)
    submitted_by = models.ForeignKey("identity.Person", on_delete=models.PROTECT, related_name="+")
    submitted_at = models.DateTimeField(default=timezone.now)
    reviewed_by = models.ForeignKey("identity.Person", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_remarks = models.TextField(blank=True)

    class Meta:
        ordering = ["enrollment", "submitted_at"]
        constraints = [
            models.UniqueConstraint(fields=["enrollment", "artefact"], name="one_live_artefact_submission",
                                    condition=~Q(status="RETURNED")),
        ]

    @property
    def owner_scholar(self):
        return self.enrollment.scholar
