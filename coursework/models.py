from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from phd_rules import grading


class Course(models.Model):
    class Category(models.TextChoices):
        MANDATORY = "MANDATORY", "Mandatory"
        ELECTIVE = "ELECTIVE", "Elective"
        ACTIVITY = "ACTIVITY", "Activity"

    code = models.CharField(max_length=20, unique=True)
    title = models.CharField(max_length=200)
    credits = models.PositiveSmallIntegerField()
    category = models.CharField(max_length=10, choices=Category.choices)
    required_for_all = models.BooleanField(default=True, help_text="Part of the common core for every entry category")
    has_ethics_submodule = models.BooleanField(default=False, help_text="Ethics sub-module must be cleared independently")
    coordinator = models.ForeignKey("core.Faculty", null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} {self.title}"


class AssessmentComponent(models.Model):
    """Rubric line for a course (e.g. SIS7005's 5 components, SIS7007's 3 criteria).
    Weights of a course's components must sum to 100. ``is_gate`` components
    (e.g. SIS7006 reflective note) must reach ``gate_min_percent`` or the attempt fails."""

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="components")
    name = models.CharField(max_length=200)
    weight = models.DecimalField(max_digits=5, decimal_places=2)
    is_gate = models.BooleanField(default=False)
    gate_min_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))

    def __str__(self):
        return f"{self.course.code}: {self.name} ({self.weight}%)"


class CourseAttempt(models.Model):
    class Special(models.TextChoices):
        NONE = "", "-"
        INCOMPLETE = "I", "Incomplete"
        REREGISTER = "RW", "Re-register"
        ABSENT = "AB", "Absent"

    scholar = models.ForeignKey("scholars.Scholar", on_delete=models.CASCADE, related_name="course_attempts")
    course = models.ForeignKey(Course, on_delete=models.PROTECT)
    attempt_no = models.PositiveSmallIntegerField()
    exam_date = models.DateField()
    marks = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
                                validators=[MinValueValidator(0), MaxValueValidator(100)])
    special_grade = models.CharField(max_length=2, choices=Special.choices, blank=True, default="")
    ethics_cleared = models.BooleanField(null=True, blank=True)
    grade = models.CharField(max_length=2, blank=True)
    grade_point = models.PositiveSmallIntegerField(null=True, blank=True)
    override = models.ForeignKey("core.ApprovalRequest", null=True, blank=True, on_delete=models.SET_NULL,
                                 help_text="VC override for a 3rd attempt / out-of-window attempt")

    class Meta:
        ordering = ["scholar", "course", "attempt_no"]
        constraints = [models.UniqueConstraint(fields=["scholar", "course", "attempt_no"], name="uniq_attempt")]

    def __str__(self):
        return f"{self.scholar.prn} {self.course.code} #{self.attempt_no}: {self.grade or '-'}"

    @property
    def passed(self) -> bool:
        return grading.is_pass(self.grade_point) and self.ethics_cleared is not False


class ComponentScore(models.Model):
    attempt = models.ForeignKey(CourseAttempt, on_delete=models.CASCADE, related_name="component_scores")
    component = models.ForeignKey(AssessmentComponent, on_delete=models.PROTECT)
    percent = models.DecimalField(max_digits=5, decimal_places=2, validators=[MinValueValidator(0), MaxValueValidator(100)])

    class Meta:
        constraints = [models.UniqueConstraint(fields=["attempt", "component"], name="uniq_component_score")]
