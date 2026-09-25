"""Department / School elective lists: catalogue → proposed list → staged approval."""
from django.db import models
from django.db.models import Q
from django.utils import timezone


class ElectiveList(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        SUBMITTED = "SUBMITTED", "Submitted for approval"
        APPROVED = "APPROVED", "Approved"
        RETURNED = "RETURNED", "Returned for revision"

    department = models.ForeignKey("core.Department", null=True, blank=True, on_delete=models.PROTECT)
    school = models.ForeignKey("core.School", null=True, blank=True, on_delete=models.PROTECT)
    semester = models.ForeignKey("coursework.Semester", on_delete=models.PROTECT, related_name="elective_lists")
    status = models.CharField(max_length=9, choices=Status.choices, default=Status.DRAFT)
    current_step = models.PositiveSmallIntegerField(default=0)
    approval_chain = models.JSONField(default=list, help_text="Capabilities snapshot at submission")
    prepared_by = models.ForeignKey("identity.Person", on_delete=models.PROTECT, related_name="+")
    prepared_at = models.DateTimeField(default=timezone.now)
    submitted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(name="elective_list_one_owner",
                                   condition=Q(department__isnull=False, school__isnull=True)
                                   | Q(department__isnull=True, school__isnull=False)),
        ]

    def __str__(self):
        return f"Electives {self.department or self.school} · {self.semester}"


class ElectiveListItem(models.Model):
    elective_list = models.ForeignKey(ElectiveList, on_delete=models.PROTECT, related_name="items")
    course = models.ForeignKey("coursework.Course", on_delete=models.PROTECT, related_name="+")
    intake_capacity = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["elective_list", "course"], name="uniq_elective_list_item")]


class ElectiveListDecision(models.Model):
    elective_list = models.ForeignKey(ElectiveList, on_delete=models.PROTECT, related_name="decisions")
    step = models.PositiveSmallIntegerField()
    capability = models.CharField(max_length=30)
    actor = models.ForeignKey("identity.Person", on_delete=models.PROTECT, related_name="+")
    approved = models.BooleanField()
    remarks = models.TextField(blank=True)
    at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["at", "id"]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise PermissionError("elective list decisions are append-only")
        super().save(*args, **kwargs)
