"""Assessment → Marks → Grade → Result, with chain of custody."""
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Assessment(models.Model):
    class Kind(models.TextChoices):
        CONTINUOUS = "CONTINUOUS", "Continuous Assessment"
        END_TERM = "END_TERM", "End-term evaluation"
        ACTIVITY = "ACTIVITY", "Activity evaluation (SDRC)"
        MANDATORY_MODULE = "MANDATORY_MODULE", "Mandatory module (cleared / not cleared)"

    offering = models.ForeignKey("coursework.CourseOffering", on_delete=models.PROTECT, related_name="assessments")
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    weight = models.DecimalField(max_digits=5, decimal_places=2,
                                 help_text="Percent of the course total; 0 for mandatory modules")
    max_marks = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True,
                                    help_text="Empty for mandatory modules (cleared / not cleared)")
    sequence = models.PositiveSmallIntegerField(default=1)
    reweighting_reference = models.CharField(max_length=255, blank=True,
                                             help_text="Approval reference when an elective is re-weighted")

    class Meta:
        ordering = ["offering", "sequence"]
        constraints = [
            models.UniqueConstraint(fields=["offering", "name"], name="uniq_assessment_name"),
            models.CheckConstraint(
                name="assessment_weight_and_max",
                condition=(Q(kind="MANDATORY_MODULE", weight=0, max_marks__isnull=True))
                | (~Q(kind="MANDATORY_MODULE") & Q(weight__gt=0, weight__lte=100, max_marks__gt=0)),
            ),
        ]

    def __str__(self):
        return f"{self.offering}: {self.name}"

    @property
    def scope_department(self):
        return self.offering.course.department

    @property
    def teaching_offering(self):
        return self.offering


class MarkEntry(models.Model):
    """Raw marks for one assessment in one attempt. Missing marks are simply
    absent rows; they are never defaulted."""

    assessment = models.ForeignKey(Assessment, on_delete=models.PROTECT, related_name="marks")
    attempt = models.ForeignKey("coursework.CourseAttempt", on_delete=models.PROTECT, related_name="mark_entries")
    marks = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    is_absent = models.BooleanField(default=False)
    cleared = models.BooleanField(null=True, blank=True, help_text="Mandatory modules only")
    entered_by = models.ForeignKey("identity.Person", on_delete=models.PROTECT, related_name="+")
    entered_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["assessment", "attempt"], name="uniq_mark_entry"),
            models.CheckConstraint(
                name="mark_entry_shape",
                condition=Q(is_absent=True, marks__isnull=True, cleared__isnull=True)
                | Q(is_absent=False, marks__isnull=False, marks__gte=0, cleared__isnull=True)
                | Q(is_absent=False, marks__isnull=True, cleared__isnull=False),
            ),
        ]

    @property
    def owner_scholar(self):
        return self.attempt.scholar

    @property
    def teaching_section(self):
        return self.attempt.teaching_section

    @property
    def teaching_offering(self):
        return self.assessment.offering


class MarkEntryHistory(models.Model):
    """Append-only record of every change to a mark entry."""

    mark = models.ForeignKey(MarkEntry, on_delete=models.PROTECT, related_name="history")
    old_marks = models.DecimalField(max_digits=6, decimal_places=2, null=True)
    old_is_absent = models.BooleanField()
    old_cleared = models.BooleanField(null=True)
    new_marks = models.DecimalField(max_digits=6, decimal_places=2, null=True)
    new_is_absent = models.BooleanField()
    new_cleared = models.BooleanField(null=True)
    reason = models.CharField(max_length=255)
    changed_by = models.ForeignKey("identity.Person", on_delete=models.PROTECT, related_name="+")
    changed_at = models.DateTimeField(default=timezone.now)

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise PermissionError("mark history is append-only")
        super().save(*args, **kwargs)


class CourseResult(models.Model):
    """Computed by the server from raw marks. Chain of custody:
    prepared (Course Coordinator / SDRC) → verified (R&D Cell) → ratified (COE).
    A ratified result is immutable."""

    class Status(models.TextChoices):
        PREPARED = "PREPARED", "Prepared"
        VERIFIED = "VERIFIED", "Verified by R&D Cell"
        RATIFIED = "RATIFIED", "Ratified by COE"
        RETURNED = "RETURNED", "Returned for correction"

    class Outcome(models.TextChoices):
        PASS = "PASS", "Pass"
        FAIL = "FAIL", "Fail"
        ABSENT = "ABSENT", "Absent"

    attempt = models.ForeignKey("coursework.CourseAttempt", on_delete=models.PROTECT, related_name="results")
    is_current = models.BooleanField(default=True, help_text="The result in force for the attempt")
    supersedes = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="revisions", help_text="Original result (revaluation)")
    total_marks = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    grade = models.CharField(max_length=3, blank=True)
    grade_point = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    outcome = models.CharField(max_length=6, choices=Outcome.choices)
    reasons = models.JSONField(default=list, blank=True, help_text="Why the outcome is what it is")
    rule_versions = models.JSONField(default=dict, help_text="{parameter key: version} used")
    is_provisional = models.BooleanField(help_text="Computed with AMBIGUOUS / UNRESOLVED parameters")
    status = models.CharField(max_length=9, choices=Status.choices, default=Status.PREPARED)
    prepared_by = models.ForeignKey("identity.Person", on_delete=models.PROTECT, related_name="+")
    prepared_as = models.CharField(max_length=30)
    prepared_at = models.DateTimeField(default=timezone.now)
    verified_by = models.ForeignKey("identity.Person", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    verified_at = models.DateTimeField(null=True, blank=True)
    ratified_by = models.ForeignKey("identity.Person", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    ratified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(name="result_total_range",
                                   condition=Q(total_marks__isnull=True) | Q(total_marks__gte=0, total_marks__lte=100)),
            models.CheckConstraint(name="result_custody_order",
                                   condition=Q(ratified_at__isnull=True) | Q(verified_at__isnull=False)),
            models.CheckConstraint(
                name="result_status_consistency",
                condition=Q(status="RATIFIED", verified_at__isnull=False, ratified_at__isnull=False)
                | Q(status="VERIFIED", verified_at__isnull=False, ratified_at__isnull=True)
                | Q(status__in=["PREPARED", "RETURNED"], ratified_at__isnull=True),
            ),
            models.UniqueConstraint(fields=["attempt"], condition=Q(is_current=True),
                                    name="one_current_result_per_attempt"),
        ]

    _supersede_ok = False

    def save(self, *args, **kwargs):
        """A ratified result is immutable. The only permitted change is being
        superseded by a ratified revaluation result (is_current -> False)."""
        if self.pk is not None:
            old = type(self).objects.filter(pk=self.pk).values().first()
            if old and old["status"] == self.Status.RATIFIED:
                changed = {k for k, v in old.items() if getattr(self, k) != v}
                if not (self._supersede_ok and changed <= {"is_current"}):
                    raise PermissionError("A ratified result is immutable")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status == self.Status.RATIFIED:
            raise PermissionError("A ratified result cannot be deleted")
        super().delete(*args, **kwargs)

    @property
    def owner_scholar(self):
        return self.attempt.scholar

    @property
    def teaching_section(self):
        return self.attempt.teaching_section

    @property
    def teaching_offering(self):
        return self.attempt.teaching_offering


class ResultEvent(models.Model):
    """Append-only custody log for a result."""

    class Action(models.TextChoices):
        PREPARE = "PREPARE", "Prepared"
        VERIFY = "VERIFY", "Verified"
        RATIFY = "RATIFY", "Ratified"
        RETURN = "RETURN", "Returned"

    result = models.ForeignKey(CourseResult, on_delete=models.PROTECT, related_name="events")
    action = models.CharField(max_length=7, choices=Action.choices)
    actor = models.ForeignKey("identity.Person", on_delete=models.PROTECT, related_name="+")
    capability = models.CharField(max_length=30)
    remarks = models.CharField(max_length=255, blank=True)
    at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["at", "id"]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise PermissionError("result events are append-only")
        super().save(*args, **kwargs)
