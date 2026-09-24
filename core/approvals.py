"""Approval-chain service.

Usage::

    req = start_approval("SYNOPSIS_EXTENSION", scholar=s, target=grant, user=u)
    decide(req, dc_user, approved=True)

When the final step approves (or any step rejects), callbacks registered with
``on_decision(code)`` run inside the same transaction so the domain object's
state and the approval record never disagree.
"""
from collections import defaultdict
from typing import Callable

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .models import ApprovalChain, ApprovalDecision, ApprovalRequest, ApprovalStatus
from .roles import Role, user_has_role

_callbacks: dict[str, list[Callable]] = defaultdict(list)


def on_decision(code: str):
    """Register ``fn(request, approved: bool)`` for when a chain closes."""

    def register(fn):
        _callbacks[code].append(fn)
        return fn

    return register


def resolve_chain(code: str, scholar=None) -> ApprovalChain:
    category = getattr(scholar, "category", "") or ""
    chain = (
        ApprovalChain.objects.filter(code=code, scholar_category=category).first()
        or ApprovalChain.objects.filter(code=code, scholar_category="").first()
    )
    if chain is None:
        raise ValidationError(f"No approval chain configured for {code!r}")
    if not chain.steps.exists():
        raise ValidationError(f"Approval chain {code!r} has no steps")
    return chain


def start_approval(code: str, *, summary: str, scholar=None, target=None, user=None) -> ApprovalRequest:
    chain = resolve_chain(code, scholar)
    req = ApprovalRequest(chain=chain, scholar=scholar, summary=summary, requested_by=user)
    if target is not None:
        req.content_type = ContentType.objects.get_for_model(target)
        req.object_id = target.pk
    req.save()
    return req


def _is_own_scholar(user, role: str, scholar) -> bool:
    faculty = getattr(user, "faculty", None)
    if scholar is None or faculty is None:
        return False
    if role in (Role.SUPERVISOR, Role.CO_SUPERVISOR):
        return scholar.supervisor_assignments.filter(faculty=faculty, end_date__isnull=True).exists()
    if role == Role.TAC_MEMBER:
        return scholar.tac_memberships.filter(faculty=faculty, end_date__isnull=True).exists()
    return True


def can_decide(req: ApprovalRequest, user) -> bool:
    step = req.pending_step
    if step is None or not user_has_role(user, step.role):
        return False
    if step.own_scholar_only and not user.is_superuser:
        return _is_own_scholar(user, step.role, req.scholar)
    return True


@transaction.atomic
def decide(req: ApprovalRequest, user, approved: bool, remarks: str = "") -> ApprovalRequest:
    req = ApprovalRequest.objects.select_for_update().get(pk=req.pk)
    step = req.pending_step
    if step is None:
        raise ValidationError("This request is already closed")
    if not can_decide(req, user):
        raise PermissionDenied(f"{user} cannot decide step {step}")

    ApprovalDecision.objects.create(request=req, step=step, decided_by=user, approved=approved, remarks=remarks)
    last_order = req.chain.steps.order_by("-order").values_list("order", flat=True).first()

    if not approved:
        req.status = ApprovalStatus.REJECTED
    elif step.order == last_order:
        req.status = ApprovalStatus.APPROVED
    else:
        req.current_step = req.chain.steps.filter(order__gt=step.order).order_by("order").first().order

    if req.status != ApprovalStatus.PENDING:
        req.closed_at = timezone.now()
    req.save()

    if req.status != ApprovalStatus.PENDING:
        for fn in _callbacks.get(req.chain.code, []):
            fn(req, req.status == ApprovalStatus.APPROVED)
    return req
