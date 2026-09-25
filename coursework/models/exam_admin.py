"""Hall tickets, question-paper setter appointments, revaluation, transcript issuance."""
from django.db import models
from django.db.models import Q
from django.utils import timezone


class HallTicket(models.Model):
    class Status(models.TextChoices):
        ISSUED = "ISSUED", "Issued"
        REVOKED = "REVOKED", "Revoked"

    registration = models.ForeignKey("coursework.ExamRegistration", on_delete=models.PROTECT,
                                     related_name="hall_tickets")
    number = models.CharField(max_length=40, unique=True, help_text="System identifier; official format unresolved")
    verification_code = models.CharField(max_length=32, unique=True)
    status = models.CharField(max_length=7, choices=Status.choices, default=Status.ISSUED)
    issued_by = models.ForeignKey("identity.Person", on_delete=models.PROTECT, related_name="+")
    issued_at = models.DateTimeField(default=timezone.now)
    revoked_by = models.ForeignKey("identity.Person", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    revoked_at = models.DateTimeField(null=True, blank=True)
    revocation_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["registration"], condition=Q(status="ISSUED"),
                                               name="one_live_hall_ticket")]

    @property
    def owner_scholar(self):
        return self.registration.enrollment.scholar


class QuestionPaperSetterAppointment(models.Model):
    """Appointment of an expert to set the question paper for an exam.
    Records the appointment and receipt only; paper content and confidentiality
    handling are not stored or defined here."""

    class Status(models.TextChoices):
        APPOINTED = "APPOINTED", "Appointed"
        PAPER_RECEIVED = "PAPER_RECEIVED", "Paper received"
        WITHDRAWN = "WITHDRAWN", "Withdrawn"

    exam = models.ForeignKey("coursework.Exam", on_delete=models.PROTECT, related_name="setter_appointments")
    setter = models.ForeignKey("identity.Person", on_delete=models.PROTECT, related_name="+")
    status = models.CharField(max_length=14, choices=Status.choices, default=Status.APPOINTED)
    basis = models.CharField(max_length=255)
    appointed_by = models.ForeignKey("identity.Person", on_delete=models.PROTECT, related_name="+")
    appointed_as = models.CharField(max_length=30)
    appointed_at = models.DateTimeField(default=timezone.now)
    received_by = models.ForeignKey("identity.Person", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    received_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["exam", "setter"], condition=~Q(status="WITHDRAWN"),
                                               name="uniq_active_setter_appointment")]

    @property
    def scope_department(self):
        return self.exam.course.department


class RevaluationCase(models.Model):
    """Post-result revaluation. The original ratified result is never changed;
    a revised result supersedes it only after verification and ratification."""

    class Status(models.TextChoices):
        REQUESTED = "REQUESTED", "Requested"
        REVIEWED_UNCHANGED = "REVIEWED_UNCHANGED", "Reviewed: no change"
        REVISED_PENDING = "REVISED_PENDING", "Revised result awaiting verification / ratification"
        COMPLETED = "COMPLETED", "Revised result ratified"
        REJECTED = "REJECTED", "Revised result not ratified"

    OPEN = ("REQUESTED", "REVISED_PENDING")

    original_result = models.ForeignKey("coursework.CourseResult", on_delete=models.PROTECT,
                                        related_name="revaluation_cases")
    revised_result = models.OneToOneField("coursework.CourseResult", null=True, blank=True, on_delete=models.PROTECT,
                                          related_name="revaluation_origin")
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REQUESTED)
    requested_by = models.ForeignKey("identity.Person", on_delete=models.PROTECT, related_name="+")
    requested_at = models.DateTimeField(default=timezone.now)
    reviewer = models.ForeignKey("identity.Person", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    reviewed_as = models.CharField(max_length=30, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_remarks = models.TextField(blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["original_result"], name="one_open_revaluation",
                                               condition=Q(status__in=["REQUESTED", "REVISED_PENDING"]))]

    @property
    def owner_scholar(self):
        return self.original_result.attempt.scholar


class RevaluationMark(models.Model):
    """Reviewer's revised marks. The original MarkEntry is not modified."""

    case = models.ForeignKey(RevaluationCase, on_delete=models.PROTECT, related_name="marks")
    assessment = models.ForeignKey("coursework.Assessment", on_delete=models.PROTECT, related_name="+")
    original_marks = models.DecimalField(max_digits=6, decimal_places=2, null=True)
    revised_marks = models.DecimalField(max_digits=6, decimal_places=2)
    reason = models.CharField(max_length=255)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["case", "assessment"], name="uniq_revaluation_mark"),
            models.CheckConstraint(name="revaluation_marks_non_negative", condition=Q(revised_marks__gte=0)),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise PermissionError("revaluation marks are append-only")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("revaluation marks are append-only")


class TranscriptIssue(models.Model):
    """Record that a transcript was issued. Stores only a content hash and a
    verification code, never marks or grades (the transcript is always derived)."""

    scholar = models.ForeignKey("scholars.Scholar", on_delete=models.PROTECT, related_name="transcript_issues")
    version = models.PositiveIntegerField()
    content_hash = models.CharField(max_length=64)
    verification_code = models.CharField(max_length=32, unique=True)
    provisional = models.BooleanField()
    issued_by = models.ForeignKey("identity.Person", on_delete=models.PROTECT, related_name="+")
    issued_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["scholar", "version"], name="uniq_transcript_version")]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise PermissionError("transcript issue records are append-only")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("transcript issue records are append-only")

    @property
    def owner_scholar(self):
        return self.scholar
