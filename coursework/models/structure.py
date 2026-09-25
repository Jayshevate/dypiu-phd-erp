"""Institutional academic structure. University, School and Department live
in `core` (shared kernel). The academic calendar is composed of AcademicYear,
Semester, ExamCycle and the global calendar events in `deadlines.AcademicEvent`."""
from django.db import models
from django.db.models import F, Q


class AcademicYear(models.Model):
    code = models.CharField(max_length=20, unique=True, help_text="e.g. 2026-27")
    start_date = models.DateField()
    end_date = models.DateField()

    class Meta:
        ordering = ["start_date"]
        constraints = [models.CheckConstraint(name="academic_year_dates", condition=Q(end_date__gt=F("start_date")))]

    def __str__(self):
        return self.code


class Semester(models.Model):
    """The canonical academic semester. The regulations define two cycles:
    January–June and July–December. Names and exact dates are configuration
    (RD-34). `finance.Semester` (TA) links here."""

    class Term(models.TextChoices):
        JAN_JUN = "JAN_JUN", "January–June"
        JUL_DEC = "JUL_DEC", "July–December"

    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name="semesters")
    term = models.CharField(max_length=7, choices=Term.choices)
    name = models.CharField(max_length=60, help_text="Display name, e.g. 'Winter 2027'")
    start_date = models.DateField()
    end_date = models.DateField()
    elective_registration_deadline = models.DateField(null=True, blank=True)
    registration_opens = models.DateField(null=True, blank=True, help_text="Semester registration window")
    registration_closes = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["start_date"]
        constraints = [
            models.UniqueConstraint(fields=["academic_year", "term"], name="uniq_semester_term"),
            models.CheckConstraint(name="semester_dates", condition=Q(end_date__gt=F("start_date"))),
            models.CheckConstraint(name="semester_registration_window",
                                   condition=Q(registration_opens__isnull=True, registration_closes__isnull=True)
                                   | Q(registration_opens__isnull=False,
                                       registration_closes__gte=F("registration_opens"))),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_term_display()})"


class FeeClearance(models.Model):
    """Fee / dues clearance for a scholar in a semester, recorded by an
    authorised office. Used by semester registration and exam eligibility."""

    scholar = models.ForeignKey("scholars.Scholar", on_delete=models.PROTECT, related_name="fee_clearances")
    semester = models.ForeignKey(Semester, on_delete=models.PROTECT, related_name="fee_clearances")
    cleared = models.BooleanField()
    reference = models.CharField(max_length=120, help_text="Receipt / accounts reference")
    recorded_by = models.ForeignKey("identity.Person", on_delete=models.PROTECT, related_name="+")
    recorded_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["scholar", "semester"], name="uniq_fee_clearance")]

    @property
    def owner_scholar(self):
        return self.scholar


class SemesterRegistration(models.Model):
    class Status(models.TextChoices):
        REGISTERED = "REGISTERED", "Registered"
        CANCELLED = "CANCELLED", "Cancelled"

    scholar = models.ForeignKey("scholars.Scholar", on_delete=models.PROTECT, related_name="semester_registrations")
    semester = models.ForeignKey(Semester, on_delete=models.PROTECT, related_name="registrations")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.REGISTERED)
    fee_clearance = models.ForeignKey(FeeClearance, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    registered_by = models.ForeignKey("identity.Person", on_delete=models.PROTECT, related_name="+")
    registered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["scholar", "semester"], condition=Q(status="REGISTERED"),
                                               name="uniq_active_semester_registration")]

    @property
    def owner_scholar(self):
        return self.scholar
