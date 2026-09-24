"""Dual-clock deadline engine.

* Global clock  - ``sync_global_calendar(year)`` materialises the recurring
  organisation-wide events from ``phd_rules.calendar_rules``.
* Scholar clock - ``scholar_deadlines(scholar)`` derives every rolling
  deadline from that scholar's own dates (registration, coursework pass,
  synopsis clearance, examiner dispatch ...), honouring granted extensions
  and clamping to the programme ceiling.

``run(today)`` does both and emits tiered reminders (30/7/1 days before and
once when overdue). It is idempotent, so it is safe to run from cron daily.
"""
from dataclasses import dataclass
from datetime import date

from django.contrib.auth import get_user_model
from django.db import transaction

from core.roles import Role
from lifecycle.models import ExaminerReport, ResearchProposal, Viva
from phd_rules import calendar_rules, durations, policy
from phd_rules.dates import add_months
from scholars.models import ExtensionGrant, Fellowship, Phase, Scholar, Status
from supervision.models import SupervisorAssignment
from supervision.services import current_supervisor, tac_complete

from .models import AcademicEvent, Deadline, Notification


@dataclass(frozen=True)
class DeadlineSpec:
    key: str
    kind: str
    title: str
    due: date
    met: bool


# --- Scholar clock -------------------------------------------------------------

def scholar_deadlines(s: Scholar, today: date) -> list[DeadlineSpec]:
    reg = s.registration_date
    if not reg:
        return []
    ceiling = s.programme_ceiling
    clamp = lambda d: durations.clamp_to_ceiling(d, ceiling)  # noqa: E731
    thesis = s.theses.order_by("-submitted_on").first()
    specs = [
        DeadlineSpec("SUPERVISOR", "SUPERVISOR_ALLOCATION", "Supervisor allocated & DC-approved",
                     durations.supervisor_allocation_due(reg), current_supervisor(s) is not None),
        DeadlineSpec("COURSEWORK", "COURSEWORK", "Coursework completed",
                     durations.coursework_due(reg), s.coursework_completed_on is not None),
        DeadlineSpec("MAX_DURATION", "PROGRAMME_CEILING", "Maximum programme duration (thesis must be submitted)",
                     ceiling, thesis is not None),
    ]

    sup = s.supervisor_assignments.filter(kind=SupervisorAssignment.Kind.SUPERVISOR, end_date__isnull=True,
                                          approved_on__isnull=False).first()
    if sup:
        specs.append(DeadlineSpec("TAC", "TAC_FORMATION", "TAC formed & approved",
                                  durations.tac_formation_due(sup.approved_on), tac_complete(s)))

    if s.coursework_completed_on:
        passed = s.proposals.filter(outcome__in=ResearchProposal.PASSING).exists()
        specs.append(DeadlineSpec("PROPOSAL", "RESEARCH_PROPOSAL", "Research proposal recommended",
                                  clamp(durations.proposal_due(s.coursework_completed_on)), passed))

    # 6-monthly progress reports, until thesis submission; look 6 months ahead.
    horizon = min(thesis.submitted_on if thesis else add_months(today, policy.PROGRESS_REPORT_INTERVAL_MONTHS),
                  ceiling)
    submitted = set(s.progress_reports.filter(submitted_on__isnull=False).values_list("period_no", flat=True))
    for n, due in enumerate(durations.progress_report_due_dates(reg, horizon), start=1):
        specs.append(DeadlineSpec(f"PROGRESS:{n}", "PROGRESS_REPORT", f"Six-monthly progress report #{n}",
                                  due, n in submitted))

    specs.append(DeadlineSpec(
        "SYNOPSIS", "SYNOPSIS", "Pre-submission synopsis",
        clamp(durations.synopsis_due(reg, s.mode, s.has_extension(ExtensionGrant.Kind.SYNOPSIS))),
        s.synopses.exists()))

    cleared = s.synopses.filter(tac_cleared_on__isnull=False).order_by("-tac_cleared_on").first()
    if cleared:
        due = durations.thesis_submission_due(cleared.tac_cleared_on, s.has_extension(ExtensionGrant.Kind.THESIS_SUBMISSION))
        specs.append(DeadlineSpec("THESIS", "THESIS_SUBMISSION", "Thesis submission", clamp(due), thesis is not None))

    if thesis:
        reports = list(ExaminerReport.objects.filter(examiner__thesis=thesis, examiner__replaced=False)
                       .select_related("examiner"))
        for r in reports:
            specs.append(DeadlineSpec(f"EXAMINER:{r.pk}", "EXAMINER_REPORT", f"Examiner report from {r.examiner.name}",
                                      durations.examiner_report_due(r.dispatched_on), r.received_on is not None))
        received = [r.received_on for r in reports if r.received_on]
        if reports and len(received) >= policy.EXAMINERS_SELECTED:
            held = Viva.objects.filter(thesis=thesis).exclude(outcome=Viva.Outcome.PENDING).exists()
            specs.append(DeadlineSpec("VIVA", "VIVA", "Dissertation presentation / viva held",
                                      durations.viva_due(max(received)), held))

    if s.fellowship == Fellowship.JRF and s.fellowship_start:
        specs.append(DeadlineSpec("SRF", "SRF_ELIGIBILITY", "Eligible for JRF -> SRF review (DC recommendation)",
                                  durations.srf_eligible_from(s.fellowship_start), False))
    return specs


@transaction.atomic
def sync_scholar(s: Scholar, today: date) -> list[Deadline]:
    specs = scholar_deadlines(s, today)
    keys = {sp.key for sp in specs}
    s.deadlines.exclude(key__in=keys).filter(met=False).delete()
    rows = []
    for sp in specs:
        row, _ = Deadline.objects.update_or_create(
            scholar=s, key=sp.key, defaults=dict(kind=sp.kind, title=sp.title, due_date=sp.due, met=sp.met))
        rows.append(row)
    return rows


# --- Global clock -------------------------------------------------------------

def sync_global_calendar(year: int) -> int:
    """Create missing events for ``year``. Existing rows are never overwritten,
    because the R&D office may have moved a date."""
    created = 0
    for e in calendar_rules.events_for_year(year):
        _, is_new = AcademicEvent.objects.get_or_create(
            key=e.key, defaults=dict(kind=e.kind, title=e.title, start=e.start, end=e.end))
        created += is_new
    return created


# --- Notifications ------------------------------------------------------------

def tier_for(due: date, today: date):
    """The reminder tier that applies today, or None. Overdue -> 0."""
    if due < today:
        return 0
    days = (due - today).days
    applicable = [lead for lead in policy.REMINDER_LEAD_DAYS if days <= lead]
    return min(applicable) if applicable else None


def _users_in(role):
    return list(get_user_model().objects.filter(groups__name=role, is_active=True))


def _scholar_audience(s: Scholar, overdue: bool):
    users = [s.user] if s.user else []
    sup = current_supervisor(s, approved_only=False)
    if sup and sup.user:
        users.append(sup.user)
    if overdue:
        users += _users_in(Role.RD_OFFICE)
    return {u.pk: u for u in users if u}.values()


EVENT_AUDIENCE = {
    "VACANCY_NOTIFICATION": lambda: _users_in(Role.RD_OFFICE),
    "RPET": lambda: _users_in(Role.RD_OFFICE),
    "COURSEWORK_EXAM": lambda: _coursework_scholars(),
    "EXAM_REGISTRATION_DEADLINE": lambda: _coursework_scholars(),
    "ELECTIVE_REGISTRATION_DEADLINE": lambda: _coursework_scholars(),
    "SEMESTER_START": lambda: [],
    "TAC_REPORTS_TO_DC": lambda: _users_in(Role.SUPERVISOR) + _users_in(Role.DC),
}


def _coursework_scholars():
    return [s.user for s in Scholar.objects.filter(status=Status.ACTIVE, phase__lte=Phase.COURSEWORK,
                                                   user__isnull=False).select_related("user")]


def _notify(recipient, tier, message, deadline=None, event=None) -> bool:
    _, created = Notification.objects.get_or_create(
        recipient=recipient, deadline=deadline, event=event, tier=tier, defaults={"message": message})
    return created


def dispatch(today: date) -> int:
    sent = 0
    for d in Deadline.objects.filter(met=False, scholar__status=Status.ACTIVE).select_related("scholar__user"):
        tier = tier_for(d.due_date, today)
        if tier is None:
            continue
        when = "is OVERDUE" if tier == 0 else f"is due on {d.due_date:%d %b %Y}"
        for user in _scholar_audience(d.scholar, overdue=tier == 0):
            sent += _notify(user, tier, f"{d.scholar.prn} {d.scholar.name}: {d.title} {when}", deadline=d)
    for e in AcademicEvent.objects.filter(start__gte=today):
        tier = tier_for(e.start, today)
        if tier is None:
            continue
        for user in EVENT_AUDIENCE.get(e.kind, lambda: [])():
            sent += _notify(user, tier, f"{e.title}: {e.start:%d %b %Y}", event=e)
    return sent


def run(today: date) -> dict:
    events = sum(sync_global_calendar(y) for y in (today.year, today.year + 1))
    scholars = 0
    for s in Scholar.objects.filter(status=Status.ACTIVE):
        sync_scholar(s, today)
        scholars += 1
    return {"events_created": events, "scholars_synced": scholars, "notifications": dispatch(today)}
