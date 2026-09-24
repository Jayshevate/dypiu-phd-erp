from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


class PhaseTransition(models.Model):
    """Append-only audit log of every phase/status change."""

    scholar = models.ForeignKey("scholars.Scholar", on_delete=models.CASCADE, related_name="transitions")
    from_phase = models.PositiveSmallIntegerField()
    to_phase = models.PositiveSmallIntegerField()
    from_status = models.CharField(max_length=10)
    to_status = models.CharField(max_length=10)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    note = models.TextField(blank=True)
    at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["at"]


class ResearchProposal(models.Model):
    class Outcome(models.TextChoices):
        PENDING = "PENDING", "Pending evaluation"
        RECOMMENDED = "RECOMMENDED", "Recommended"
        MINOR_REVISION = "MINOR_REVISION", "Recommended with minor modifications"
        RESUBMIT = "RESUBMIT", "Resubmit after major revision"
        NOT_RECOMMENDED = "NOT_RECOMMENDED", "Not recommended"

    PASSING = (Outcome.RECOMMENDED, Outcome.MINOR_REVISION)
    FAILING = (Outcome.RESUBMIT, Outcome.NOT_RECOMMENDED)

    scholar = models.ForeignKey("scholars.Scholar", on_delete=models.CASCADE, related_name="proposals")
    attempt_no = models.PositiveSmallIntegerField(default=1)
    title = models.CharField(max_length=500)
    document = models.FileField(upload_to="proposals/", blank=True)
    submitted_on = models.DateField()
    committee = models.ForeignKey("core.Committee", null=True, blank=True, on_delete=models.PROTECT)
    seminar_date = models.DateField(null=True, blank=True)
    outcome = models.CharField(max_length=20, choices=Outcome.choices, default=Outcome.PENDING)
    remarks = models.TextField(blank=True)

    class Meta:
        ordering = ["scholar", "attempt_no"]
        constraints = [models.UniqueConstraint(fields=["scholar", "attempt_no"], name="uniq_proposal_attempt")]


class ProgressReport(models.Model):
    class Outcome(models.TextChoices):
        PENDING = "PENDING", "Awaiting TAC review"
        SATISFACTORY = "SATISFACTORY", "Satisfactory"
        UNSATISFACTORY = "UNSATISFACTORY", "Unsatisfactory (warning)"

    scholar = models.ForeignKey("scholars.Scholar", on_delete=models.CASCADE, related_name="progress_reports")
    period_no = models.PositiveSmallIntegerField(help_text="1 = first six months after registration")
    due_date = models.DateField()
    submitted_on = models.DateField(null=True, blank=True)
    document = models.FileField(upload_to="progress/", blank=True)
    tac_meeting_date = models.DateField(null=True, blank=True)
    tac_minutes = models.TextField(blank=True)
    outcome = models.CharField(max_length=15, choices=Outcome.choices, default=Outcome.PENDING)
    dc_review = models.ForeignKey("DCReview", null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="warnings", help_text="The DC review that dealt with this warning")

    class Meta:
        ordering = ["scholar", "period_no"]
        constraints = [models.UniqueConstraint(fields=["scholar", "period_no"], name="uniq_progress_period")]


class DCReview(models.Model):
    class Decision(models.TextChoices):
        PENDING = "PENDING", "Pending"
        CONTINUE = "CONTINUE", "Continue with conditions"
        CANCEL = "CANCEL", "Cancel registration"

    scholar = models.ForeignKey("scholars.Scholar", on_delete=models.CASCADE, related_name="dc_reviews")
    reason = models.CharField(max_length=255)
    opened_on = models.DateField()
    decision = models.CharField(max_length=10, choices=Decision.choices, default=Decision.PENDING)
    decided_on = models.DateField(null=True, blank=True)
    remarks = models.TextField(blank=True)


class Synopsis(models.Model):
    scholar = models.ForeignKey("scholars.Scholar", on_delete=models.CASCADE, related_name="synopses")
    submitted_on = models.DateField()
    document = models.FileField(upload_to="synopsis/", blank=True)
    open_seminar_date = models.DateField(null=True, blank=True)
    tac_cleared_on = models.DateField(null=True, blank=True)
    remarks = models.TextField(blank=True)

    class Meta:
        verbose_name_plural = "synopses"
        ordering = ["scholar", "submitted_on"]


class Thesis(models.Model):
    scholar = models.ForeignKey("scholars.Scholar", on_delete=models.CASCADE, related_name="theses")
    title = models.CharField(max_length=500)
    submitted_on = models.DateField()
    document = models.FileField(upload_to="thesis/", blank=True)
    plagiarism_percent = models.DecimalField(max_digits=5, decimal_places=2,
                                             validators=[MinValueValidator(0), MaxValueValidator(100)])
    plagiarism_report = models.FileField(upload_to="thesis/plagiarism/", blank=True)
    fees_paid = models.BooleanField(default=False)
    panel_approval = models.ForeignKey("core.ApprovalRequest", null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        verbose_name_plural = "theses"
        ordering = ["scholar", "submitted_on"]

    def __str__(self):
        return f"{self.scholar.prn}: {self.title}"


class ExaminerNomination(models.Model):
    thesis = models.ForeignKey(Thesis, on_delete=models.CASCADE, related_name="nominations")
    name = models.CharField(max_length=200)
    affiliation = models.CharField(max_length=255)
    email = models.EmailField()
    is_foreign = models.BooleanField(default=False)
    selected = models.BooleanField(default=False, help_text="One of the 3 chosen by the VC")
    replaced = models.BooleanField(default=False, help_text="Replaced after failing to report")

    def __str__(self):
        return f"{self.name} ({self.affiliation})"


class ExaminerReport(models.Model):
    class Recommendation(models.TextChoices):
        COMMEND = "COMMEND", "Commended for award"
        REVISE = "REVISE", "Revise and resubmit"
        NOT_COMMEND = "NOT_COMMEND", "Not commended"

    examiner = models.OneToOneField(ExaminerNomination, on_delete=models.CASCADE, related_name="report")
    dispatched_on = models.DateField()
    reminded_on = models.DateField(null=True, blank=True)
    received_on = models.DateField(null=True, blank=True)
    recommendation = models.CharField(max_length=12, choices=Recommendation.choices, blank=True)
    document = models.FileField(upload_to="thesis/reports/", blank=True)


class Viva(models.Model):
    class Outcome(models.TextChoices):
        PENDING = "PENDING", "Pending"
        SATISFACTORY = "SATISFACTORY", "Satisfactory"
        NOT_SATISFACTORY = "NOT_SATISFACTORY", "Not satisfactory"

    thesis = models.ForeignKey(Thesis, on_delete=models.CASCADE, related_name="vivas")
    dpep = models.ForeignKey("core.Committee", on_delete=models.PROTECT, limit_choices_to={"type": "DPEP"})
    scheduled_on = models.DateField()
    venue = models.CharField(max_length=255, blank=True)
    candidate_notified_on = models.DateField(null=True, blank=True)
    invitation_published_on = models.DateField(null=True, blank=True)
    outcome = models.CharField(max_length=20, choices=Outcome.choices, default=Outcome.PENDING)
    report = models.FileField(upload_to="viva/", blank=True)

    class Meta:
        verbose_name_plural = "vivas"


class DegreeAward(models.Model):
    scholar = models.OneToOneField("scholars.Scholar", on_delete=models.CASCADE, related_name="degree_award")
    approval = models.OneToOneField("core.ApprovalRequest", null=True, blank=True, on_delete=models.SET_NULL)
    approved_on = models.DateField(null=True, blank=True)
    certificate_no = models.CharField(max_length=50, blank=True)
    issued_on = models.DateField(null=True, blank=True)
    academic_council_notified_on = models.DateField(null=True, blank=True)
