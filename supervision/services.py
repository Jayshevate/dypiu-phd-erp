"""Supervisor allocation and TAC formation with write-time rule enforcement.

Capacity checks lock the faculty row (SELECT ... FOR UPDATE on PostgreSQL) so
two concurrent allocations cannot both take the last seat."""
from datetime import date

from django.core.exceptions import ValidationError
from django.db import transaction

from core.approvals import on_decision, start_approval
from core.models import Faculty
from phd_rules import policy
from phd_rules.dates import add_years

from .models import SupervisorAssignment, TACMembership


def open_supervisions(faculty: Faculty):
    qs = SupervisorAssignment.objects.filter(faculty=faculty, end_date__isnull=True)
    if not policy.COUNT_CO_SUPERVISION_TOWARDS_CAP:
        qs = qs.filter(kind=SupervisorAssignment.Kind.SUPERVISOR)
    return qs


def supervisor_load(faculty: Faculty) -> int:
    """Approved and pending assignments both count, to prevent over-allocation."""
    return open_supervisions(faculty).count()


def check_supervisor_eligibility(faculty: Faculty, scholar, kind: str, on: date) -> list[str]:
    problems = []
    if kind == SupervisorAssignment.Kind.SUPERVISOR and faculty.is_external:
        problems.append("External faculty may only be co-supervisors")
    if not faculty.is_external:
        if faculty.supervision_capacity == 0:
            problems.append(f"{faculty.get_designation_display()} is not eligible to supervise")
        elif supervisor_load(faculty) >= faculty.supervision_capacity:
            problems.append(f"{faculty} is at capacity ({faculty.supervision_capacity} scholars)")
        if faculty.superannuation_date and faculty.superannuation_date < add_years(on, policy.MIN_SERVICE_YEARS_REMAINING):
            problems.append(f"{faculty} has fewer than {policy.MIN_SERVICE_YEARS_REMAINING} years of service remaining")
    if scholar.tac_memberships.filter(faculty=faculty, end_date__isnull=True, kind=TACMembership.Kind.MEMBER).exists():
        problems.append(f"{faculty} already sits on this scholar's TAC")
    return problems


@transaction.atomic
def propose_supervisor(scholar, faculty: Faculty, kind=SupervisorAssignment.Kind.SUPERVISOR, on: date = None, user=None):
    on = on or date.today()
    faculty = Faculty.objects.select_for_update().get(pk=faculty.pk)
    problems = check_supervisor_eligibility(faculty, scholar, kind, on)
    if kind == SupervisorAssignment.Kind.SUPERVISOR and scholar.supervisor_assignments.filter(
        kind=kind, end_date__isnull=True
    ).exists():
        problems.append("Scholar already has a supervisor; use the change-of-supervisor workflow")
    if problems:
        raise ValidationError(problems)
    assignment = SupervisorAssignment.objects.create(scholar=scholar, faculty=faculty, kind=kind, start_date=on)
    assignment.approval = start_approval(
        "SUPERVISOR_ALLOCATION", summary=f"{scholar.prn}: {assignment.get_kind_display()} {faculty}",
        scholar=scholar, target=assignment, user=user,
    )
    assignment.save(update_fields=["approval"])
    return assignment


@on_decision("SUPERVISOR_ALLOCATION")
def _close_allocation(req, approved):
    field = "approved_on" if approved else "end_date"
    SupervisorAssignment.objects.filter(approval=req).update(**{field: date.today()})


def current_supervisor(scholar, approved_only=True):
    qs = scholar.supervisor_assignments.filter(kind=SupervisorAssignment.Kind.SUPERVISOR, end_date__isnull=True)
    if approved_only:
        qs = qs.filter(approved_on__isnull=False)
    a = qs.select_related("faculty").first()
    return a.faculty if a else None


def check_tac_members(scholar, members: list[Faculty]) -> list[str]:
    problems = []
    supervisor = current_supervisor(scholar)
    if supervisor is None:
        return ["A DC-approved supervisor is required before forming the TAC"]
    if len(members) != policy.TAC_MEMBER_COUNT or len(set(m.pk for m in members)) != len(members):
        problems.append(f"TAC needs exactly {policy.TAC_MEMBER_COUNT} distinct members besides the supervisor")
    co_supervisor_ids = set(scholar.supervisor_assignments.filter(end_date__isnull=True).values_list("faculty_id", flat=True))
    for m in members:
        if m.pk in co_supervisor_ids:
            problems.append(f"{m} is a supervisor/co-supervisor of this scholar and cannot sit on the TAC")
        if policy.TAC_REQUIRE_INTERDISCIPLINARY and m.department_id and m.department_id == scholar.department_id:
            problems.append(f"{m} is from the scholar's own department; TAC members must be interdisciplinary")
        if policy.TAC_MEMBER_UNIQUE_PER_SUPERVISOR:
            clash = TACMembership.objects.filter(
                faculty=m, kind=TACMembership.Kind.MEMBER, end_date__isnull=True,
                scholar__supervisor_assignments__faculty=supervisor,
                scholar__supervisor_assignments__kind="SUPERVISOR",
                scholar__supervisor_assignments__end_date__isnull=True,
            ).exclude(scholar=scholar).select_related("scholar").first()
            if clash:
                problems.append(f"{m} already sits on the TAC of {clash.scholar.prn}, another scholar of {supervisor}")
    return problems


@transaction.atomic
def propose_tac(scholar, members: list[Faculty], on: date = None, user=None):
    on = on or date.today()
    if scholar.tac_memberships.filter(end_date__isnull=True).exists():
        raise ValidationError("Scholar already has a TAC; dissolve it first")
    problems = check_tac_members(scholar, members)
    if problems:
        raise ValidationError(problems)
    req = start_approval("TAC_FORMATION", summary=f"{scholar.prn}: TAC formation", scholar=scholar, user=user)
    seats = [TACMembership(scholar=scholar, faculty=current_supervisor(scholar), kind=TACMembership.Kind.SUPERVISOR,
                           start_date=on, approval=req)]
    seats += [TACMembership(scholar=scholar, faculty=m, kind=TACMembership.Kind.MEMBER, start_date=on, approval=req)
              for m in members]
    TACMembership.objects.bulk_create(seats)
    return req


@on_decision("TAC_FORMATION")
def _close_tac(req, approved):
    seats = TACMembership.objects.filter(approval=req)
    if approved:
        seats.update(approved_on=date.today())
    else:
        seats.update(end_date=date.today())


def tac_complete(scholar) -> bool:
    seats = scholar.tac_memberships.filter(end_date__isnull=True, approved_on__isnull=False)
    return (seats.filter(kind=TACMembership.Kind.SUPERVISOR).exists()
            and seats.filter(kind=TACMembership.Kind.MEMBER).count() == policy.TAC_MEMBER_COUNT)
