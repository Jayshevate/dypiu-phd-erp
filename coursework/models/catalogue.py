"""Course master and the per-attempt record.

`Course` and `CourseAttempt` pre-date Step 5A and are extended in place
(no duplicate entities). `CourseAttempt` is the single ExamAttempt record:
new attempts are created through examination/activity services and carry
their enrollment and registration. Rows with status LEGACY were graded by the
retired pre-Step-5A path; they are kept for the record but are NOT counted by
the authoritative academic record (see `manage.py academic_legacy_report`)."""
from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q


class Course(models.Model):
    class Category(models.TextChoices):
        MANDATORY = "MANDATORY", "Mandatory taught course"
        ELECTIVE = "ELECTIVE", "Elective"
        ACTIVITY = "ACTIVITY", "Mandatory activity component"

    class ActivityType(models.TextChoices):
        NONE = "", "-"
        INDUSTRIAL_TRAINING = "INDUSTRIAL_TRAINING", "Industrial Training / Field Work"
        CONFERENCE_WORKSHOP = "CONFERENCE_WORKSHOP", "Conference / Workshop participation"
        RESEARCH_SEMINAR = "RESEARCH_SEMINAR", "Research Seminar Presentation"

    class EvaluationBody(models.TextChoices):
        COURSE_COORDINATOR = "COURSE_COORDINATOR", "Course Coordinator"
        SDRC = "SDRC", "SDRC (activity-based evaluation)"

    code = models.CharField(max_length=20, unique=True)
    title = models.CharField(max_length=200)
    credits = models.PositiveSmallIntegerField()
    category = models.CharField(max_length=10, choices=Category.choices)
    activity_type = models.CharField(max_length=20, choices=ActivityType.choices, blank=True, default="")
    evaluation_body = models.CharField(max_length=20, choices=EvaluationBody.choices,
                                       default=EvaluationBody.COURSE_COORDINATOR)
    applicable_categories = models.JSONField(default=list, blank=True,
                                             help_text='Scholar coursework categories, e.g. ["I","II","III"]; '
                                                       "empty = all")
    department = models.ForeignKey("core.Department", null=True, blank=True, on_delete=models.PROTECT,
                                   help_text="Owning department; empty = institution-level (e.g. CISR courses)")
    required_for_all = models.BooleanField(default=True, help_text="Part of the common core for every entry category")
    has_ethics_submodule = models.BooleanField(default=False, help_text="Ethics sub-module must be cleared independently")
    is_active = models.BooleanField(default=True)
    coordinator = models.ForeignKey("core.Faculty", null=True, blank=True, on_delete=models.SET_NULL,
                                    help_text="LEGACY: use FacultySubjectAssignment(role=COURSE_COORDINATOR)")

    class Meta:
        ordering = ["code"]
        constraints = [
            models.CheckConstraint(name="course_credits_positive", condition=Q(credits__gt=0)),
            # Activities are evaluated by SDRC and have an activity type; taught courses
            # and electives are evaluated by the Course Coordinator.
            models.CheckConstraint(
                name="course_activity_consistency",
                condition=(Q(category="ACTIVITY", evaluation_body="SDRC") & ~Q(activity_type=""))
                | (~Q(category="ACTIVITY") & Q(evaluation_body="COURSE_COORDINATOR", activity_type="")),
            ),
        ]

    def __str__(self):
        return f"{self.code} {self.title}"

    @property
    def scope_department(self):
        return self.department

    def applies_to(self, coursework_category: str) -> bool:
        return not self.applicable_categories or coursework_category in self.applicable_categories


class AssessmentComponent(models.Model):
    """LEGACY rubric line per course, used by the legacy record_result path.
    New evaluations use coursework.Assessment (per offering)."""

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="components")
    name = models.CharField(max_length=200)
    weight = models.DecimalField(max_digits=5, decimal_places=2)
    is_gate = models.BooleanField(default=False)
    gate_min_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))

    def __str__(self):
        return f"{self.course.code}: {self.name} ({self.weight}%)"


class CourseAttempt(models.Model):
    """ExamAttempt: one attempt of a scholar at a course (exam or activity evaluation)."""

    class Special(models.TextChoices):
        NONE = "", "-"
        INCOMPLETE = "I", "Incomplete"
        REREGISTER = "RW", "Re-register"
        ABSENT = "AB", "Absent"

    class Status(models.TextChoices):
        LEGACY = "LEGACY", "Legacy record"
        SCHEDULED = "SCHEDULED", "Scheduled / under evaluation"
        RESULT_RATIFIED = "RESULT_RATIFIED", "Result ratified"
        CANCELLED = "CANCELLED", "Cancelled"

    scholar = models.ForeignKey("scholars.Scholar", on_delete=models.CASCADE, related_name="course_attempts")
    course = models.ForeignKey(Course, on_delete=models.PROTECT)
    enrollment = models.ForeignKey("coursework.ScholarCourseEnrollment", null=True, blank=True,
                                   on_delete=models.PROTECT, related_name="attempts")
    attempt_no = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.LEGACY)
    exam_date = models.DateField()
    # The fields below are written ONLY from a ratified CourseResult (or by the legacy path).
    marks = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
                                validators=[MinValueValidator(0), MaxValueValidator(100)])
    special_grade = models.CharField(max_length=2, choices=Special.choices, blank=True, default="")
    ethics_cleared = models.BooleanField(null=True, blank=True)
    grade = models.CharField(max_length=2, blank=True)
    grade_point = models.PositiveSmallIntegerField(null=True, blank=True)
    override = models.ForeignKey("core.ApprovalRequest", null=True, blank=True, on_delete=models.SET_NULL,
                                 help_text="LEGACY: VC override for a 3rd attempt (see ThirdAttemptCase)")

    class Meta:
        ordering = ["scholar", "course", "attempt_no"]
        constraints = [
            models.UniqueConstraint(fields=["scholar", "course", "attempt_no"], name="uniq_attempt"),
            # Two regular attempts plus one approved third (final) attempt.
            models.CheckConstraint(name="attempt_no_range", condition=Q(attempt_no__gte=1, attempt_no__lte=3)),
            models.CheckConstraint(name="attempt_marks_range",
                                   condition=Q(marks__isnull=True) | Q(marks__gte=0, marks__lte=100)),
        ]

    def __str__(self):
        return f"{self.scholar.prn} {self.course.code} #{self.attempt_no}: {self.grade or '-'}"

    # --- authorization adapters (see identity.authz) ---
    @property
    def owner_scholar(self):
        return self.scholar

    @property
    def teaching_section(self):
        return self.enrollment.section if self.enrollment_id else None

    @property
    def teaching_offering(self):
        return self.enrollment.offering if self.enrollment_id else None


ExamAttempt = CourseAttempt


class ComponentScore(models.Model):
    """LEGACY rubric score (legacy record_result path)."""

    attempt = models.ForeignKey(CourseAttempt, on_delete=models.CASCADE, related_name="component_scores")
    component = models.ForeignKey(AssessmentComponent, on_delete=models.PROTECT)
    percent = models.DecimalField(max_digits=5, decimal_places=2, validators=[MinValueValidator(0), MaxValueValidator(100)])

    class Meta:
        constraints = [models.UniqueConstraint(fields=["attempt", "component"], name="uniq_component_score")]
