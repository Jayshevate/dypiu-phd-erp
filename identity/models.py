import hashlib
import json

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone

from .capabilities import ALLOWED_SCOPES, DERIVED_CAPABILITIES, Capability, ScopeType


class Person(models.Model):
    """The one identity of a human. A person may hold several profiles at once
    (e.g. DYPIU faculty registered as a Part-Time Internal scholar)."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                related_name="person", help_text="Login account (IdP subject)")
    full_name = models.CharField(max_length=200)
    email = models.EmailField()
    is_active = models.BooleanField(default=True)
    # Profiles. Linked from Person so existing modules stay unchanged.
    faculty_profile = models.OneToOneField("core.Faculty", null=True, blank=True, on_delete=models.PROTECT,
                                           related_name="person")
    scholar_profile = models.OneToOneField("scholars.Scholar", null=True, blank=True, on_delete=models.PROTECT,
                                           related_name="person")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(Lower("email"), name="uniq_person_email_ci")]
        ordering = ["full_name"]

    def __str__(self):
        return f"{self.full_name} <{self.email}>"


class CapabilityAssignment(models.Model):
    """An explicit, time-bounded, scoped grant. Created and revoked only through
    identity.services (the admin is read-only for this model)."""

    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="capability_assignments")
    capability = models.CharField(max_length=30, choices=Capability.choices)
    scope_type = models.CharField(max_length=15, choices=ScopeType.choices)
    school = models.ForeignKey("core.School", null=True, blank=True, on_delete=models.PROTECT)
    department = models.ForeignKey("core.Department", null=True, blank=True, on_delete=models.PROTECT)
    scholar = models.ForeignKey("scholars.Scholar", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="+")
    valid_from = models.DateField()
    valid_to = models.DateField(null=True, blank=True)
    basis = models.CharField(max_length=255, help_text="Why this was granted (order no., approval ref)")
    granted_by = models.ForeignKey(Person, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                                   help_text="Null only for the system bootstrap")
    granted_at = models.DateTimeField(default=timezone.now)
    revoked_by = models.ForeignKey(Person, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    revoked_at = models.DateTimeField(null=True, blank=True)
    revocation_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                name="capability_scope_fields_consistent",
                condition=(
                    Q(scope_type="INSTITUTION", school__isnull=True, department__isnull=True, scholar__isnull=True)
                    | Q(scope_type="SCHOOL", school__isnull=False, department__isnull=True, scholar__isnull=True)
                    | Q(scope_type="DEPARTMENT", school__isnull=True, department__isnull=False, scholar__isnull=True)
                    | Q(scope_type="SCHOLAR", school__isnull=True, department__isnull=True, scholar__isnull=False)
                ),
            ),
            models.CheckConstraint(
                name="capability_not_derived",
                condition=~Q(capability__in=[c.value for c in DERIVED_CAPABILITIES]),
            ),
            models.CheckConstraint(name="capability_valid_range",
                                   condition=Q(valid_to__isnull=True) | Q(valid_to__gte=models.F("valid_from"))),
        ] + [
            # No duplicate active grant of the same capability on the same scope.
            models.UniqueConstraint(
                fields=["person", "capability", *target],
                condition=Q(revoked_at__isnull=True, scope_type=scope),
                name=f"uniq_active_grant_{scope.lower()}",
            )
            for scope, target in (("INSTITUTION", []), ("SCHOOL", ["school"]),
                                  ("DEPARTMENT", ["department"]), ("SCHOLAR", ["scholar"]))
        ]
        ordering = ["person", "capability"]

    def __str__(self):
        return f"{self.person.full_name}: {self.capability} @ {self.scope_label}"

    @property
    def scope_label(self):
        target = self.school or self.department or self.scholar
        return f"{self.scope_type}" + (f":{target}" if target else "")

    def clean(self):
        if self.capability in DERIVED_CAPABILITIES:
            raise ValidationError(f"{self.capability} is derived from profiles/memberships and cannot be granted")
        if self.scope_type not in ALLOWED_SCOPES.get(self.capability, set()):
            raise ValidationError(f"{self.capability} cannot be scoped to {self.scope_type}")
        if self.capability == Capability.SUPERVISOR and not self.person.faculty_profile_id:
            raise ValidationError("SUPERVISOR is a capability of Faculty: the person has no faculty profile")

    def is_effective(self, on=None) -> bool:
        on = on or timezone.localdate()
        return (self.revoked_at is None and self.valid_from <= on
                and (self.valid_to is None or self.valid_to >= on) and self.person.is_active)


class AuditQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise PermissionError("audit events are append-only")

    def delete(self):
        raise PermissionError("audit events are append-only")


class AuditEvent(models.Model):
    """Append-only, hash-chained record of privileged actions and denials."""

    actor = models.ForeignKey(Person, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    actor_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="+")
    capability_used = models.CharField(max_length=30, blank=True)
    action = models.CharField(max_length=100)
    resource_type = models.CharField(max_length=100, blank=True)
    resource_id = models.CharField(max_length=64, blank=True)
    allowed = models.BooleanField()
    reasons = models.JSONField(default=list, blank=True)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    request_id = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    prev_hash = models.CharField(max_length=64)
    hash = models.CharField(max_length=64, unique=True)

    objects = AuditQuerySet.as_manager()

    class Meta:
        ordering = ["id"]

    def canonical(self) -> str:
        return json.dumps({
            "actor": self.actor_id, "actor_user": self.actor_user_id, "capability_used": self.capability_used,
            "action": self.action, "resource_type": self.resource_type, "resource_id": self.resource_id,
            "allowed": self.allowed, "reasons": self.reasons, "before": self.before, "after": self.after,
            "ip_address": self.ip_address, "request_id": self.request_id,
            "created_at": self.created_at.isoformat(), "prev_hash": self.prev_hash,
        }, sort_keys=True, default=str)

    def compute_hash(self) -> str:
        return hashlib.sha256(self.canonical().encode()).hexdigest()

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise PermissionError("audit events are append-only")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("audit events are append-only")
