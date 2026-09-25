from django.db import models


class Semester(models.Model):
    code = models.CharField(max_length=20, unique=True, help_text="e.g. 2026-ODD")
    start_date = models.DateField()
    end_date = models.DateField()
    fee_deadline = models.DateField()
    academic_semester = models.OneToOneField("coursework.Semester", null=True, blank=True, on_delete=models.PROTECT,
                                             related_name="ta_semester",
                                             help_text="Canonical academic semester this TA semester belongs to")

    class Meta:
        ordering = ["start_date"]

    def __str__(self):
        return self.code


class TARegistration(models.Model):
    class State(models.TextChoices):
        REGISTERED = "REGISTERED", "Registered"
        LISTED = "LISTED", "Shared with Schools"
        INDUCTED = "INDUCTED", "Inducted"
        COMPLETED = "COMPLETED", "Completed"

    scholar = models.ForeignKey("scholars.Scholar", on_delete=models.CASCADE, related_name="ta_registrations")
    semester = models.ForeignKey(Semester, on_delete=models.PROTECT)
    form_submitted_on = models.DateField()
    fee_receipt = models.FileField(upload_to="ta/receipts/", blank=True)
    expertise = models.CharField(max_length=255, blank=True)
    state = models.CharField(max_length=12, choices=State.choices, default=State.REGISTERED)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["scholar", "semester"], name="uniq_ta_semester")]

    def __str__(self):
        return f"{self.scholar.prn} TA {self.semester}"


class TAAssignment(models.Model):
    registration = models.ForeignKey(TARegistration, on_delete=models.CASCADE, related_name="assignments")
    school = models.ForeignKey("core.School", on_delete=models.PROTECT)
    course_name = models.CharField(max_length=200)
    faculty = models.ForeignKey("core.Faculty", on_delete=models.PROTECT)
    hours_per_week = models.PositiveSmallIntegerField()
    contact_hours_per_week = models.PositiveSmallIntegerField()


class TAFeedback(models.Model):
    class Source(models.TextChoices):
        FACULTY = "FACULTY", "Faculty"
        DIRECTOR = "DIRECTOR", "School Director"
        STUDENTS = "STUDENTS", "Students"

    registration = models.ForeignKey(TARegistration, on_delete=models.CASCADE, related_name="feedback")
    source = models.CharField(max_length=10, choices=Source.choices)
    rating = models.PositiveSmallIntegerField(help_text="1-5")
    satisfactory = models.BooleanField()
    comments = models.TextField(blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["registration", "source"], name="uniq_ta_feedback_source")]


class GrantClaim(models.Model):
    class State(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    scholar = models.ForeignKey("scholars.Scholar", on_delete=models.CASCADE, related_name="grant_claims")
    event_name = models.CharField(max_length=255)
    event_date = models.DateField()
    amount_claimed = models.DecimalField(max_digits=10, decimal_places=2)
    amount_approved = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    receipts = models.FileField(upload_to="grants/", blank=True)
    state = models.CharField(max_length=10, choices=State.choices, default=State.PENDING)
    approval = models.OneToOneField("core.ApprovalRequest", null=True, blank=True, on_delete=models.SET_NULL)
    submitted_on = models.DateField(auto_now_add=True)
