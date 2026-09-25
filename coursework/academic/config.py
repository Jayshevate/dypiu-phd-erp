"""Versioned academic regulatory parameters."""
from dataclasses import dataclass
from typing import Any

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from coursework.models import AcademicRuleParameter, ParameterChangeRequest, RuleStatus

from .common import audit_ok, authorize_or_deny, deny


@dataclass(frozen=True)
class Param:
    key: str
    value: Any
    status: str
    version: int
    reference: str

    @property
    def confirmed(self) -> bool:
        return self.status == RuleStatus.CONFIRMED

    @property
    def unresolved(self) -> bool:
        return self.status == RuleStatus.UNRESOLVED or self.value is None


def get(key: str, on=None) -> Param:
    on = on or timezone.localdate()
    row = (AcademicRuleParameter.objects.filter(key=key, effective_from__lte=on)
           .order_by("-effective_from", "-version").first())
    if row is None:
        raise ValidationError(f"Academic rule parameter {key!r} is not configured")
    return Param(row.key, row.value, row.status, row.version, row.reference)


class RuleContext:
    """Collects the parameters an evaluation used, for traceability."""

    def __init__(self, on=None):
        self.on = on or timezone.localdate()
        self.used: dict[str, Param] = {}

    def __call__(self, key: str) -> Param:
        if key not in self.used:
            self.used[key] = get(key, self.on)
        return self.used[key]

    @property
    def versions(self) -> dict:
        return {k: {"version": p.version, "status": p.status} for k, p in sorted(self.used.items())}

    @property
    def provisional(self) -> bool:
        return any(p.status in (RuleStatus.AMBIGUOUS, RuleStatus.UNRESOLVED) for p in self.used.values())


def propose_change(actor, key: str, *, value, status: str, source: str, reason: str, effective_from=None,
                   reference: str = "", request=None) -> ParameterChangeRequest:
    """Maker step. Nothing changes until a different authorised person approves."""
    decision = authorize_or_deny(actor, "academic.config.change", None, request=request)
    if status not in RuleStatus.values:
        raise ValidationError(f"Unknown status {status!r}")
    if status == RuleStatus.UNRESOLVED and value is not None:
        raise ValidationError("An UNRESOLVED parameter cannot carry a value")
    if status != RuleStatus.UNRESOLVED and value is None:
        raise ValidationError("Only an UNRESOLVED parameter may have no value")
    if not reason.strip() or not source.strip():
        raise ValidationError("source and reason are required")
    if not AcademicRuleParameter.objects.filter(key=key).exists():
        raise ValidationError(f"Unknown parameter {key!r}; new parameters are added by migration")
    change = ParameterChangeRequest(key=key, value=value, status=status, source=source, reference=reference,
                                    effective_from=effective_from or timezone.localdate(), reason=reason,
                                    proposed_by=actor)
    try:
        with transaction.atomic():
            change.save()
            audit_ok(actor, "academic.config.propose", change, decision, request=request,
                     after={"key": key, "value": value, "status": status, "effective_from": str(change.effective_from)})
    except IntegrityError:
        raise ValidationError(f"A change to {key!r} is already pending approval")
    return change


def decide_change(actor, change: ParameterChangeRequest, *, approve: bool, remarks: str = "",
                  request=None) -> ParameterChangeRequest:
    """Checker step. Approval creates a new immutable parameter version."""
    decision = authorize_or_deny(actor, "academic.config.approve", None, request=request)
    if change.state != ParameterChangeRequest.State.PENDING:
        raise ValidationError(f"Change is already {change.state}")
    if change.proposed_by_id == actor.pk:
        deny(actor, "academic.config.approve", "Separation of duties: the proposer cannot approve", change,
             request=request)
    with transaction.atomic():
        change = ParameterChangeRequest.objects.select_for_update().get(pk=change.pk)
        before = get(change.key)
        change.state = (ParameterChangeRequest.State.APPROVED if approve
                        else ParameterChangeRequest.State.REJECTED)
        change.decided_by, change.decided_at, change.decision_remarks = actor, timezone.now(), remarks
        change.save()
        after = {"state": change.state}
        if approve:
            latest = AcademicRuleParameter.objects.filter(key=change.key).aggregate(v=Max("version"))["v"]
            row = AcademicRuleParameter.objects.create(
                key=change.key, version=latest + 1, value=change.value, status=change.status, source=change.source,
                reference=change.reference, effective_from=change.effective_from, change_reason=change.reason,
                created_by=change.proposed_by, approved_by=actor, change_request=change)
            after.update(version=row.version, value=change.value, status=change.status)
        audit_ok(actor, "academic.config.approve", change, decision, request=request,
                 before={"version": before.version, "value": before.value, "status": before.status}, after=after)
    return change
