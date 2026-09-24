from django.core.exceptions import ValidationError
from django.db import transaction

from core.approvals import on_decision, start_approval
from phd_rules import policy

from .models import ExtensionGrant, LeaveRecord, Scholar


@transaction.atomic
def request_extension(scholar: Scholar, kind: str, reason: str = "", user=None) -> ExtensionGrant:
    if scholar.has_extension(kind):
        raise ValidationError(f"{ExtensionGrant.Kind(kind).label} already granted")
    if kind == ExtensionGrant.Kind.RELAXATION and not scholar.relaxation_eligible:
        raise ValidationError("Relaxation applies only to female scholars or PwD > 40%")
    grant = ExtensionGrant.objects.create(scholar=scholar, kind=kind, reason=reason)
    grant.approval = start_approval(
        ExtensionGrant.CHAIN_FOR_KIND[kind], summary=f"{scholar.prn}: {grant.get_kind_display()}",
        scholar=scholar, target=grant, user=user,
    )
    grant.save(update_fields=["approval"])
    return grant


def _close_extension(req, approved):
    state = ExtensionGrant.State.GRANTED if approved else ExtensionGrant.State.REFUSED
    ExtensionGrant.objects.filter(approval=req).update(status=state)


for _code in ExtensionGrant.CHAIN_FOR_KIND.values():
    on_decision(_code)(_close_extension)


def validate_leave(leave: LeaveRecord) -> None:
    """Enforce leave caps at write time (approved + pending leaves count)."""
    if leave.end_date < leave.start_date:
        raise ValidationError("Leave ends before it starts")
    others = leave.scholar.leaves.exclude(pk=leave.pk).filter(type=leave.type)
    if leave.type == LeaveRecord.Type.ANNUAL:
        if leave.start_date.year != leave.end_date.year:
            raise ValidationError("Split annual leave that spans two calendar years")
        used = sum(l.days for l in others.filter(start_date__year=leave.start_date.year))
        if used + leave.days > policy.ANNUAL_LEAVE_DAYS:
            raise ValidationError(
                f"Annual leave cap {policy.ANNUAL_LEAVE_DAYS} days exceeded ({used} already used/requested)"
            )
    if leave.type == LeaveRecord.Type.MATERNITY:
        used = sum(l.days for l in others)
        if used + leave.days > policy.MATERNITY_LEAVE_DAYS:
            raise ValidationError(f"Maternity leave cap {policy.MATERNITY_LEAVE_DAYS} days exceeded ({used} used)")


@transaction.atomic
def apply_for_leave(scholar, type, start_date, end_date, reason="", user=None) -> LeaveRecord:
    leave = LeaveRecord(scholar=scholar, type=type, start_date=start_date, end_date=end_date, reason=reason)
    validate_leave(leave)
    leave.save()
    chain = "LEAVE" if type == LeaveRecord.Type.ANNUAL else "MATERNITY_LEAVE"
    leave.approval = start_approval(chain, summary=f"{scholar.prn}: {leave.get_type_display()} ({leave.days} days)",
                                    scholar=scholar, target=leave, user=user)
    leave.save(update_fields=["approval"])
    return leave


@on_decision("LEAVE")
@on_decision("MATERNITY_LEAVE")
def _close_leave(req, approved):
    LeaveRecord.objects.filter(approval=req).update(approved=approved)
