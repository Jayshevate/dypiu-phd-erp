"""Course → CourseOffering → Section → FacultySubjectAssignment;
Scholar → ScholarCourseEnrollment → CourseOffering/Section."""
from django.db import models
from django.db.models import F, Q
from django.utils import timezone


class CourseOffering(models.Model):
    class Status(models.TextChoices):
        PLANNED = "PLANNED", "Planned"
        OPEN = "OPEN", "Open for enrollment"
        CLOSED = "CLOSED", "Closed"

    course = models.ForeignKey("coursework.Course", on_delete=models.PROTECT, related_name="offerings")
    semester = models.ForeignKey("coursework.Semester", on_delete=models.PROTECT, related_name="offerings")
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.PLANNED)
    capacity = models.PositiveSmallIntegerField(null=True, blank=True)
    created_by = models.ForeignKey("identity.Person", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["semester__start_date", "course__code"]
        constraints = [models.UniqueConstraint(fields=["course", "semester"], name="uniq_offering_course_semester")]

    def __str__(self):
        return f"{self.course.code} · {self.semester}"

    @property
    def scope_department(self):
        return self.course.department

    @property
    def teaching_offering(self):
        return self

    @property
    def teaching_section(self):
        return None


class Section(models.Model):
    offering = models.ForeignKey(CourseOffering, on_delete=models.PROTECT, related_name="sections")
    code = models.CharField(max_length=10)
    capacity = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["offering", "code"]
        constraints = [models.UniqueConstraint(fields=["offering", "code"], name="uniq_section_code")]

    def __str__(self):
        return f"{self.offering} / {self.code}"

    @property
    def scope_department(self):
        return self.offering.course.department

    @property
    def teaching_offering(self):
        return self.offering

    @property
    def teaching_section(self):
        return self


class FacultySubjectAssignment(models.Model):
    """Faculty ↔ offering (as Course Coordinator) or ↔ section (as instructor).
    Time-bounded; ended by revocation, never deleted."""

    class Role(models.TextChoices):
        INSTRUCTOR = "INSTRUCTOR", "Instructor (section)"
        COURSE_COORDINATOR = "COURSE_COORDINATOR", "Course Coordinator (offering)"

    faculty = models.ForeignKey("core.Faculty", on_delete=models.PROTECT, related_name="subject_assignments")
    offering = models.ForeignKey(CourseOffering, on_delete=models.PROTECT, related_name="faculty_assignments")
    section = models.ForeignKey(Section, null=True, blank=True, on_delete=models.PROTECT,
                                related_name="faculty_assignments")
    role = models.CharField(max_length=20, choices=Role.choices)
    valid_from = models.DateField()
    valid_to = models.DateField(null=True, blank=True)
    basis = models.CharField(max_length=255)
    assigned_by = models.ForeignKey("identity.Person", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    assigned_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey("identity.Person", null=True, blank=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        constraints = [
            models.CheckConstraint(
                name="assignment_role_section",
                condition=Q(role="INSTRUCTOR", section__isnull=False) | Q(role="COURSE_COORDINATOR", section__isnull=True),
            ),
            models.CheckConstraint(name="assignment_valid_range",
                                   condition=Q(valid_to__isnull=True) | Q(valid_to__gte=F("valid_from"))),
            models.UniqueConstraint(fields=["faculty", "section"], name="uniq_active_instructor",
                                    condition=Q(role="INSTRUCTOR", revoked_at__isnull=True)),
            models.UniqueConstraint(fields=["offering"], name="one_active_coordinator_per_offering",
                                    condition=Q(role="COURSE_COORDINATOR", revoked_at__isnull=True)),
        ]

    def __str__(self):
        return f"{self.faculty} {self.get_role_display()} {self.section or self.offering}"

    def is_active_on(self, day=None) -> bool:
        day = day or timezone.localdate()
        return self.revoked_at is None and self.valid_from <= day and (self.valid_to is None or self.valid_to >= day)

    @property
    def scope_department(self):
        return self.offering.course.department


class ScholarCourseEnrollment(models.Model):
    class Status(models.TextChoices):
        ENROLLED = "ENROLLED", "Enrolled"
        WITHDRAWN = "WITHDRAWN", "Withdrawn"
        COMPLETED = "COMPLETED", "Completed (result ratified: pass)"

    scholar = models.ForeignKey("scholars.Scholar", on_delete=models.PROTECT, related_name="course_enrollments")
    offering = models.ForeignKey(CourseOffering, on_delete=models.PROTECT, related_name="enrollments")
    section = models.ForeignKey(Section, null=True, blank=True, on_delete=models.PROTECT, related_name="enrollments")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ENROLLED)
    elective_proposal = models.ForeignKey("coursework.ElectiveProposal", null=True, blank=True,
                                          on_delete=models.PROTECT, related_name="enrollments")
    enrolled_by = models.ForeignKey("identity.Person", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    enrolled_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["scholar", "offering"]
        constraints = [models.UniqueConstraint(fields=["scholar", "offering"], name="uniq_enrollment")]

    def __str__(self):
        return f"{self.scholar.prn} → {self.offering}"

    @property
    def course(self):
        return self.offering.course

    @property
    def owner_scholar(self):
        return self.scholar

    @property
    def teaching_section(self):
        return self.section

    @property
    def teaching_offering(self):
        return self.offering
