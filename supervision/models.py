from django.db import models
from django.db.models import Q


class SupervisorAssignment(models.Model):
    class Kind(models.TextChoices):
        SUPERVISOR = "SUPERVISOR", "Supervisor"
        CO_SUPERVISOR = "CO_SUPERVISOR", "Co-Supervisor"

    scholar = models.ForeignKey("scholars.Scholar", on_delete=models.CASCADE, related_name="supervisor_assignments")
    faculty = models.ForeignKey("core.Faculty", on_delete=models.PROTECT, related_name="supervisions")
    kind = models.CharField(max_length=15, choices=Kind.choices)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True, help_text="Set when the assignment ends or is replaced")
    approved_on = models.DateField(null=True, blank=True, help_text="DC approval date; null while pending")
    approval = models.OneToOneField("core.ApprovalRequest", null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["scholar"], condition=Q(kind="SUPERVISOR", end_date__isnull=True),
                                    name="one_open_supervisor_per_scholar"),
            models.UniqueConstraint(fields=["scholar", "faculty"], condition=Q(end_date__isnull=True),
                                    name="no_duplicate_open_supervision"),
        ]

    def __str__(self):
        return f"{self.faculty} -> {self.scholar.prn} ({self.get_kind_display()})"


class TACMembership(models.Model):
    class Kind(models.TextChoices):
        SUPERVISOR = "SUPERVISOR", "Supervisor (ex officio)"
        MEMBER = "MEMBER", "Interdisciplinary member"

    scholar = models.ForeignKey("scholars.Scholar", on_delete=models.CASCADE, related_name="tac_memberships")
    faculty = models.ForeignKey("core.Faculty", on_delete=models.PROTECT, related_name="tac_memberships")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    approved_on = models.DateField(null=True, blank=True)
    approval = models.ForeignKey("core.ApprovalRequest", null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["scholar", "faculty"], condition=Q(end_date__isnull=True),
                                    name="no_duplicate_open_tac_seat"),
        ]

    def __str__(self):
        return f"TAC {self.scholar.prn}: {self.faculty} ({self.get_kind_display()})"
