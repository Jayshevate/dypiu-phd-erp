from django.db import models
from django.utils import timezone


class RuleStatus(models.TextChoices):
    CONFIRMED = "CONFIRMED", "Confirmed by the supplied source"
    CONFIGURABLE = "CONFIGURABLE", "Configurable (value known, pending institutional confirmation)"
    AMBIGUOUS = "AMBIGUOUS", "Ambiguous (sources conflict or wording unclear)"
    UNRESOLVED = "UNRESOLVED", "Unresolved (no value; requires institutional decision)"


class ParameterQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise PermissionError("rule parameters are versioned: create a new version instead")

    def delete(self):
        raise PermissionError("rule parameters are versioned and cannot be deleted")


class AcademicRuleParameter(models.Model):
    """One immutable version of an academic regulatory parameter. The value in
    force on a date is the latest version whose effective_from <= that date.
    Every version records its classification, source and who created it; the
    table itself is the audit history."""

    key = models.CharField(max_length=100)
    version = models.PositiveIntegerField()
    value = models.JSONField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=RuleStatus.choices)
    source = models.CharField(max_length=255, help_text="Document / dataset the value comes from")
    reference = models.CharField(max_length=100, blank=True, help_text="Decision-sheet IDs, e.g. RD-01, RD-02")
    effective_from = models.DateField()
    change_reason = models.CharField(max_length=255)
    created_by = models.ForeignKey("identity.Person", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="+", help_text="Proposer; null only for the initial seed")
    approved_by = models.ForeignKey("identity.Person", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="+", help_text="Approver; null only for the initial seed")
    change_request = models.OneToOneField("coursework.ParameterChangeRequest", null=True, blank=True,
                                          on_delete=models.PROTECT, related_name="resulting_version")
    created_at = models.DateTimeField(default=timezone.now)

    objects = ParameterQuerySet.as_manager()

    class Meta:
        ordering = ["key", "version"]
        constraints = [models.UniqueConstraint(fields=["key", "version"], name="uniq_rule_param_version")]

    def __str__(self):
        return f"{self.key} v{self.version} [{self.status}]"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise PermissionError("rule parameters are versioned: create a new version instead")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("rule parameters are versioned and cannot be deleted")


class ParameterChangeRequest(models.Model):
    """Maker-checker: a proposed parameter change becomes a new version only
    after approval by a different person holding the approving capability."""

    class State(models.TextChoices):
        PENDING = "PENDING", "Pending approval"
        APPROVED = "APPROVED", "Approved (version created)"
        REJECTED = "REJECTED", "Rejected"

    key = models.CharField(max_length=100)
    value = models.JSONField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=RuleStatus.choices)
    source = models.CharField(max_length=255)
    reference = models.CharField(max_length=100, blank=True)
    effective_from = models.DateField()
    reason = models.CharField(max_length=255)
    state = models.CharField(max_length=8, choices=State.choices, default=State.PENDING)
    proposed_by = models.ForeignKey("identity.Person", on_delete=models.PROTECT, related_name="+")
    proposed_at = models.DateTimeField(default=timezone.now)
    decided_by = models.ForeignKey("identity.Person", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_remarks = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-proposed_at"]
        constraints = [
            models.UniqueConstraint(fields=["key"], condition=models.Q(state="PENDING"),
                                    name="one_pending_change_per_key"),
            models.CheckConstraint(name="change_decision_consistency",
                                   condition=models.Q(state="PENDING", decided_by__isnull=True)
                                   | (~models.Q(state="PENDING") & models.Q(decided_by__isnull=False))),
        ]

    def __str__(self):
        return f"{self.key} → {self.state}"
