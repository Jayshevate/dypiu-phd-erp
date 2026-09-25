"""Elective catalogue → department/school proposed list → staged approval
(elective.list_approval_chain; AMBIGUOUS: 'approved jointly by DC and Dean of
Research', order not stated) → scholar proposal → enrollment."""
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from coursework.models import Course, ElectiveList, ElectiveListDecision, ElectiveListItem
from identity.authz import Policy, ResourceScope, Rule

from .common import audit_ok, authorize_or_deny, authorize_policy_or_deny, deny
from .config import RuleContext


def _scope(elective_list: ElectiveList) -> ResourceScope:
    if elective_list.department_id:
        return ResourceScope(school_id=elective_list.department.school_id, department_id=elective_list.department_id)
    return ResourceScope(school_id=elective_list.school_id)


def prepare_list(actor, *, semester, items, department=None, school=None, request=None) -> ElectiveList:
    """items: [(course, intake_capacity or None)]"""
    if (department is None) == (school is None):
        raise ValidationError("A list belongs to exactly one department or one school")
    target = (ResourceScope(school_id=department.school_id, department_id=department.pk) if department
              else ResourceScope(school_id=school.pk))
    decision = authorize_or_deny(actor, "academic.elective_list.prepare", target, request=request)
    if not items:
        raise ValidationError("The list is empty")
    for course, _ in items:
        if course.category != Course.Category.ELECTIVE or not course.is_active:
            raise ValidationError(f"{course.code} is not an active elective in the catalogue")
    try:
        with transaction.atomic():
            lst = ElectiveList.objects.create(department=department, school=school, semester=semester,
                                              prepared_by=actor)
            for course, capacity in items:
                ElectiveListItem.objects.create(elective_list=lst, course=course, intake_capacity=capacity)
            audit_ok(actor, "academic.elective_list.prepare", lst, decision, request=request,
                     after={"courses": [c.code for c, _ in items]})
    except IntegrityError:
        raise ValidationError("Duplicate course in the list")
    return lst


def submit_list(actor, elective_list: ElectiveList, *, request=None) -> ElectiveList:
    decision = authorize_or_deny(actor, "academic.elective_list.prepare", _scope(elective_list), request=request)
    if elective_list.status not in (ElectiveList.Status.DRAFT, ElectiveList.Status.RETURNED):
        raise ValidationError(f"List is {elective_list.status}")
    chain = RuleContext()("elective.list_approval_chain")
    if chain.unresolved or not chain.value:
        raise ValidationError("The elective list approval chain is unresolved (elective.list_approval_chain)")
    with transaction.atomic():
        elective_list.status, elective_list.current_step = ElectiveList.Status.SUBMITTED, 0
        elective_list.approval_chain, elective_list.submitted_at = list(chain.value), timezone.now()
        elective_list.save(update_fields=["status", "current_step", "approval_chain", "submitted_at"])
        audit_ok(actor, "academic.elective_list.submit", elective_list, decision, request=request,
                 after={"approval_chain": chain.value, "chain_status": chain.status})
    return elective_list


def decide_list(actor, elective_list: ElectiveList, *, approve: bool, remarks: str = "", request=None) -> ElectiveList:
    if elective_list.status != ElectiveList.Status.SUBMITTED:
        raise ValidationError(f"List is {elective_list.status}")
    step = elective_list.current_step
    capability = elective_list.approval_chain[step]
    action = "academic.elective_list.decide"
    decision = authorize_policy_or_deny(actor, action, Policy(rules=(Rule(capability),), privileged=True),
                                        _scope(elective_list), request=request)
    if actor.pk == elective_list.prepared_by_id or elective_list.decisions.filter(actor=actor).exists():
        deny(actor, action, "Separation of duties: the preparer or an earlier approver cannot decide this step",
             elective_list, request=request)
    if not approve and not remarks.strip():
        raise ValidationError("Remarks are required when returning a list")
    with transaction.atomic():
        ElectiveListDecision.objects.create(elective_list=elective_list, step=step, capability=capability,
                                            actor=actor, approved=approve, remarks=remarks)
        if not approve:
            elective_list.status = ElectiveList.Status.RETURNED
        elif step + 1 >= len(elective_list.approval_chain):
            elective_list.status = ElectiveList.Status.APPROVED
        else:
            elective_list.current_step = step + 1
        elective_list.save(update_fields=["status", "current_step"])
        audit_ok(actor, action, elective_list, decision, request=request,
                 after={"step": step, "approved": approve, "status": elective_list.status})
    return elective_list
