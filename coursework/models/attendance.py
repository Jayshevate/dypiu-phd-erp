from django.db import models
from django.db.models import F, Q
from django.utils import timezone


class AttendanceSession(models.Model):
    section = models.ForeignKey("coursework.Section", on_delete=models.PROTECT, related_name="attendance_sessions")
    faculty = models.ForeignKey("core.Faculty", on_delete=models.PROTECT, related_name="+")
    date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    topic = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey("identity.Person", on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["date", "start_time"]
        constraints = [
            models.UniqueConstraint(fields=["section", "date", "start_time"], name="uniq_attendance_session"),
            models.CheckConstraint(name="attendance_session_times", condition=Q(end_time__gt=F("start_time"))),
        ]

    def __str__(self):
        return f"{self.section} {self.date} {self.start_time}"

    @property
    def scope_department(self):
        return self.section.offering.course.department

    @property
    def teaching_section(self):
        return self.section

    @property
    def teaching_offering(self):
        return self.section.offering


class AttendanceRecord(models.Model):
    class Status(models.TextChoices):
        PRESENT = "PRESENT", "Present"
        ABSENT = "ABSENT", "Absent"

    class Mode(models.TextChoices):
        PHYSICAL = "PHYSICAL", "Physical"
        ONLINE = "ONLINE", "Online"

    session = models.ForeignKey(AttendanceSession, on_delete=models.PROTECT, related_name="records")
    enrollment = models.ForeignKey("coursework.ScholarCourseEnrollment", on_delete=models.PROTECT,
                                   related_name="attendance_records")
    status = models.CharField(max_length=7, choices=Status.choices)
    mode = models.CharField(max_length=8, choices=Mode.choices, blank=True, default="")
    recorded_by = models.ForeignKey("identity.Person", on_delete=models.PROTECT, related_name="+")
    recorded_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["session", "enrollment"], name="uniq_attendance_record"),
            models.CheckConstraint(name="attendance_mode_consistency",
                                   condition=(Q(status="PRESENT") & ~Q(mode="")) | Q(status="ABSENT", mode="")),
        ]

    @property
    def owner_scholar(self):
        return self.enrollment.scholar

    @property
    def teaching_section(self):
        return self.session.section

    @property
    def teaching_offering(self):
        return self.session.section.offering


class AttendanceCorrection(models.Model):
    """Append-only history of changes to an attendance record."""

    record = models.ForeignKey(AttendanceRecord, on_delete=models.PROTECT, related_name="corrections")
    old_status = models.CharField(max_length=7)
    old_mode = models.CharField(max_length=8, blank=True)
    new_status = models.CharField(max_length=7)
    new_mode = models.CharField(max_length=8, blank=True)
    reason = models.CharField(max_length=255)
    corrected_by = models.ForeignKey("identity.Person", on_delete=models.PROTECT, related_name="+")
    corrected_at = models.DateTimeField(default=timezone.now)

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise PermissionError("attendance corrections are append-only")
        super().save(*args, **kwargs)


class OrientationSeminar(models.Model):
    """Research Orientation Seminar (held at the beginning of each semester).
    Attendance only; it carries no credits and no marks."""

    semester = models.ForeignKey("coursework.Semester", on_delete=models.PROTECT, related_name="orientation_seminars")
    held_on = models.DateField()
    title = models.CharField(max_length=255)
    organised_by = models.ForeignKey("core.Committee", null=True, blank=True, on_delete=models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["semester", "held_on", "title"], name="uniq_ros")]

    @property
    def scope_department(self):
        return None


class OrientationAttendance(models.Model):
    seminar = models.ForeignKey(OrientationSeminar, on_delete=models.PROTECT, related_name="attendance")
    scholar = models.ForeignKey("scholars.Scholar", on_delete=models.PROTECT, related_name="+")
    attended = models.BooleanField()
    recorded_by = models.ForeignKey("identity.Person", on_delete=models.PROTECT, related_name="+")
    recorded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["seminar", "scholar"], name="uniq_ros_attendance")]

    @property
    def owner_scholar(self):
        return self.scholar
