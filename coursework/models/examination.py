"""ExamCycle → Exam → ExamEligibility → ExamRegistration → ExamAttempt, plus ThirdAttemptCase."""
from django.db import models
from django.db.models import F, Q
from django.utils import timezone


class ExamCycle(models.Model):
    """One coursework examination cycle per semester (January–June, July–December).
    All dates are configuration (RD-35)."""

    semester = models.OneToOneField("coursework.Semester", on_delete=models.PROTECT, related_name="exam_cycle")
    name = models.CharField(max_length=80)
    registration_opens = models.DateField()
    registration_closes = models.DateField()
    exam_start = models.DateField()
    exam_end = models.DateField()

    class Meta:
        ordering = ["exam_start"]
        constraints = [
            models.CheckConstraint(
                name="exam_cycle_dates",
                condition=Q(registration_closes__gte=F("registration_opens"), exam_start__gte=F("registration_closes"),
                            exam_end__gte=F("exam_start")),
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def scope_department(self):
        return None


class Exam(models.Model):
    """The examination of a course in a cycle. Registrations may come from the
    current offering or (for re-attempts) an earlier one."""

    cycle = models.ForeignKey(ExamCycle, on_delete=models.PROTECT, related_name="exams")
    course = models.ForeignKey("coursework.Course", on_delete=models.PROTECT, related_name="exams")
    scheduled_on = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    venue = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["scheduled_on", "start_time"]
        constraints = [
            models.UniqueConstraint(fields=["cycle", "course"], name="uniq_exam_cycle_course"),
            models.CheckConstraint(name="exam_times", condition=Q(end_time__gt=F("start_time"))),
        ]

    def __str__(self):
        return f"{self.course.code} · {self.cycle}"

    @property
    def scope_department(self):
        return self.course.department


class ExamEligibility(models.Model):
    """A persisted, server-computed eligibility evaluation (kept for audit and appeals)."""

    exam = models.ForeignKey(Exam, on_delete=models.PROTECT, related_name="eligibility_checks")
    enrollment = models.ForeignKey("coursework.ScholarCourseEnrollment", on_delete=models.PROTECT, related_name="+")
    eligible = models.BooleanField()
    attempt_no = models.PositiveSmallIntegerField(null=True)
    reasons = models.JSONField(default=list)
    warnings = models.JSONField(default=list)
    rule_versions = models.JSONField(default=dict)
    evaluated_by = models.ForeignKey("identity.Person", null=True, on_delete=models.PROTECT, related_name="+")
    evaluated_at = models.DateTimeField(default=timezone.now)

    @property
    def owner_scholar(self):
        return self.enrollment.scholar


class ExamRegistration(models.Model):
    class Status(models.TextChoices):
        REGISTERED = "REGISTERED", "Registered"
        CANCELLED = "CANCELLED", "Cancelled"

    exam = models.ForeignKey(Exam, on_delete=models.PROTECT, related_name="registrations")
    enrollment = models.ForeignKey("coursework.ScholarCourseEnrollment", on_delete=models.PROTECT,
                                   related_name="exam_registrations")
    attempt = models.OneToOneField("coursework.CourseAttempt", on_delete=models.PROTECT, related_name="registration")
    attempt_no = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.REGISTERED)
    eligibility = models.OneToOneField(ExamEligibility, on_delete=models.PROTECT, related_name="registration")
    third_attempt_case = models.OneToOneField("coursework.ThirdAttemptCase", null=True, blank=True,
                                              on_delete=models.PROTECT, related_name="registration")
    registered_by = models.ForeignKey("identity.Person", on_delete=models.PROTECT, related_name="+")
    registered_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["exam", "enrollment"], name="uniq_active_exam_registration",
                                    condition=Q(status="REGISTERED")),
            models.CheckConstraint(name="registration_attempt_range",
                                   condition=Q(attempt_no__gte=1, attempt_no__lte=3)),
            models.CheckConstraint(name="third_attempt_needs_case",
                                   condition=Q(attempt_no__lt=3) | Q(third_attempt_case__isnull=False)),
        ]

    @property
    def owner_scholar(self):
        return self.enrollment.scholar


class ThirdAttemptCase(models.Model):
    """Third (final) attempt: Dean R&D review, then VC approval."""

    class Status(models.TextChoices):
        SUBMITTED = "SUBMITTED", "Submitted"
        DEAN_RECOMMENDED = "DEAN_RECOMMENDED", "Recommended by Dean R&D"
        DEAN_NOT_RECOMMENDED = "DEAN_NOT_RECOMMENDED", "Not recommended by Dean R&D"
        VC_APPROVED = "VC_APPROVED", "Approved by VC"
        VC_REJECTED = "VC_REJECTED", "Rejected by VC"
        CONSUMED = "CONSUMED", "Used for the third attempt"

    OPEN = (Status.SUBMITTED, Status.DEAN_RECOMMENDED, Status.DEAN_NOT_RECOMMENDED, Status.VC_APPROVED)

    scholar = models.ForeignKey("scholars.Scholar", on_delete=models.PROTECT, related_name="third_attempt_cases")
    enrollment = models.ForeignKey("coursework.ScholarCourseEnrollment", on_delete=models.PROTECT,
                                   related_name="third_attempt_cases")
    attempts_used = models.PositiveSmallIntegerField()
    previous_attempts = models.JSONField(default=list, help_text="Snapshot of prior attempts and results")
    reason = models.TextField()
    supporting_document = models.FileField(upload_to="coursework/third_attempt/", blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SUBMITTED)
    submitted_by = models.ForeignKey("identity.Person", on_delete=models.PROTECT, related_name="+")
    submitted_at = models.DateTimeField(default=timezone.now)
    dean_by = models.ForeignKey("identity.Person", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    dean_at = models.DateTimeField(null=True, blank=True)
    dean_remarks = models.TextField(blank=True)
    vc_by = models.ForeignKey("identity.Person", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    vc_at = models.DateTimeField(null=True, blank=True)
    vc_remarks = models.TextField(blank=True)
    mentor = models.ForeignKey("core.Faculty", null=True, blank=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["enrollment"], name="one_open_third_attempt_case",
                                    condition=Q(status__in=["SUBMITTED", "DEAN_RECOMMENDED",
                                                            "DEAN_NOT_RECOMMENDED", "VC_APPROVED"])),
            models.CheckConstraint(name="third_attempt_vc_after_dean",
                                   condition=Q(vc_at__isnull=True) | Q(dean_at__isnull=False)),
        ]

    @property
    def owner_scholar(self):
        return self.scholar


class ThirdAttemptCaseEvent(models.Model):
    case = models.ForeignKey(ThirdAttemptCase, on_delete=models.PROTECT, related_name="events")
    action = models.CharField(max_length=30)
    actor = models.ForeignKey("identity.Person", on_delete=models.PROTECT, related_name="+")
    capability = models.CharField(max_length=30)
    remarks = models.TextField(blank=True)
    at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["at", "id"]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise PermissionError("case history is append-only")
        super().save(*args, **kwargs)
