"""The phase state machine.

Each phase has a *gate*: a function returning the list of unmet conditions
for leaving that phase. ``advance`` moves a scholar forward only when the
gate returns no reasons; every change is logged in ``PhaseTransition``.

Phases are a main line, not a strict sequence of activity: supervisor
allocation typically happens during coursework, so when the coursework gate
opens the Supervisor & TAC gate may already be satisfied and ``advance_all``
will carry the scholar through both in one call.
"""
from dataclasses import dataclass, field
from datetime import date

from django.core.exceptions import ValidationError
from django.db import transaction

from coursework.academic.records import coursework_status
from phd_rules import durations, policy
from scholars.models import ExtensionGrant, Phase, Scholar, Status
from supervision.services import current_supervisor, tac_complete

from .models import DCReview, DegreeAward, ExaminerReport, PhaseTransition, ProgressReport, ResearchProposal, Viva


@dataclass
class GateResult:
    phase: int
    reasons: list = field(default_factory=list)

    @property
    def open(self) -> bool:
        return not self.reasons


def _admission(s: Scholar, today):
    r = []
    if s.application and not s.application.eligible_for_selection:
        r.append("Application has not cleared RPET/exemption and interview (>= 50%)")
    if not s.registration_date:
        r.append("Provisional registration date not recorded")
    if not s.registration_fee_paid:
        r.append("Registration fee not paid")
    if s.category in ("PT_SPONSORED", "PT_SISTER") and not s.sponsorship_letter:
        r.append("Sponsorship / NOC letter required for this part-time category")
    return r


def _coursework(s, today):
    """Authoritative: the Academic record derived from ratified results."""
    status = coursework_status(s)
    return [] if status["complete"] else status["reasons"]


def _supervisor_tac(s, today):
    r = []
    if current_supervisor(s) is None:
        r.append("No DC-approved supervisor")
    if not tac_complete(s):
        r.append("TAC not formed/approved (supervisor + 2 interdisciplinary members)")
    return r


def _proposal(s, today):
    latest = s.proposals.order_by("-attempt_no").first()
    if latest is None:
        return ["Research proposal not submitted"]
    if latest.outcome not in ResearchProposal.PASSING:
        return [f"Latest proposal outcome: {latest.get_outcome_display()}"]
    return []


def _progress(s, today):
    r = []
    if s.dc_reviews.filter(decision=DCReview.Decision.PENDING).exists():
        r.append("A DC review is pending")
    last = s.progress_reports.exclude(outcome=ProgressReport.Outcome.PENDING).order_by("-period_no").first()
    if last is None or last.outcome != ProgressReport.Outcome.SATISFACTORY:
        r.append("Latest TAC-reviewed progress report must be satisfactory")
    if not s.synopses.exists():
        r.append("Pre-submission synopsis not submitted")
    return r


def _synopsis(s, today):
    syn = s.synopses.filter(tac_cleared_on__isnull=False).order_by("-tac_cleared_on").first()
    return [] if syn else ["TAC has not cleared the pre-submission synopsis"]


def _thesis_submission(s, today):
    thesis = s.theses.order_by("-submitted_on").first()
    if thesis is None:
        return ["Thesis not submitted"]
    r = []
    if thesis.plagiarism_percent > policy.MAX_PLAGIARISM_PERCENT:
        r.append(f"Plagiarism {thesis.plagiarism_percent}% exceeds {policy.MAX_PLAGIARISM_PERCENT}%")
    if not thesis.fees_paid:
        r.append("Thesis fees not paid")
    if thesis.submitted_on < durations.earliest_thesis_submission(s.registration_date):
        r.append(f"Minimum duration of {policy.MIN_DURATION_YEARS} years not completed")
    if s.programme_ceiling and thesis.submitted_on > s.programme_ceiling:
        r.append("Submitted after the maximum programme duration")
    syn = s.synopses.filter(tac_cleared_on__isnull=False).order_by("-tac_cleared_on").first()
    if syn:
        due = durations.thesis_submission_due(syn.tac_cleared_on, s.has_extension(ExtensionGrant.Kind.THESIS_SUBMISSION))
        if thesis.submitted_on > due:
            r.append(f"Submitted after the thesis deadline {due}")
    return r


def _evaluation(s, today):
    thesis = s.theses.order_by("-submitted_on").first()
    reports = ExaminerReport.objects.filter(examiner__thesis=thesis, examiner__selected=True,
                                            examiner__replaced=False, received_on__isnull=False)
    if reports.count() < policy.EXAMINERS_SELECTED:
        return [f"{reports.count()} of {policy.EXAMINERS_SELECTED} examiner reports received"]
    commend = reports.filter(recommendation=ExaminerReport.Recommendation.COMMEND).count()
    if commend < policy.EXAMINERS_COMMEND_REQUIRED:
        return [f"Only {commend} examiner(s) commended; {policy.EXAMINERS_COMMEND_REQUIRED} required"]
    return []


def _viva(s, today):
    ok = Viva.objects.filter(thesis__scholar=s, outcome=Viva.Outcome.SATISFACTORY).exists()
    return [] if ok else ["DPEP has not recorded a satisfactory viva"]


def _degree(s, today):
    award = DegreeAward.objects.filter(scholar=s).first()
    if award is None or award.approved_on is None:
        return ["Degree award approval chain not complete"]
    if not award.issued_on:
        return ["Certificate not issued"]
    return []


GATES = {
    Phase.ADMISSION: _admission,
    Phase.COURSEWORK: _coursework,
    Phase.SUPERVISOR_TAC: _supervisor_tac,
    Phase.RESEARCH_PROPOSAL: _proposal,
    Phase.PROGRESS: _progress,
    Phase.SYNOPSIS: _synopsis,
    Phase.THESIS_SUBMISSION: _thesis_submission,
    Phase.THESIS_EVALUATION: _evaluation,
    Phase.VIVA: _viva,
    Phase.DEGREE_AWARD: _degree,
}


class TransitionBlocked(ValidationError):
    pass


def evaluate_gate(scholar: Scholar, today: date = None) -> GateResult:
    today = today or date.today()
    if scholar.status != Status.ACTIVE:
        return GateResult(scholar.phase, [f"Scholar status is {scholar.get_status_display()}"])
    return GateResult(scholar.phase, GATES[Phase(scholar.phase)](scholar, today))


def _log(scholar, from_phase, from_status, actor, note):
    PhaseTransition.objects.create(scholar=scholar, from_phase=from_phase, to_phase=scholar.phase,
                                   from_status=from_status, to_status=scholar.status, actor=actor, note=note)


@transaction.atomic
def advance(scholar: Scholar, actor=None, today: date = None) -> Scholar:
    scholar = Scholar.objects.select_for_update().get(pk=scholar.pk)
    gate = evaluate_gate(scholar, today)
    if not gate.open:
        raise TransitionBlocked(gate.reasons)
    before = (scholar.phase, scholar.status)
    if scholar.phase == Phase.DEGREE_AWARD:
        scholar.status = Status.AWARDED
    else:
        scholar.phase += 1
    scholar.save(update_fields=["phase", "status"])
    _log(scholar, *before, actor, "gate passed")
    return scholar


def advance_all(scholar: Scholar, actor=None, today: date = None) -> Scholar:
    """Advance through as many consecutive open gates as possible."""
    while scholar.status == Status.ACTIVE and evaluate_gate(scholar, today).open:
        scholar = advance(scholar, actor, today)
    return scholar


@transaction.atomic
def terminate(scholar: Scholar, status: str, note: str, actor=None) -> Scholar:
    if status not in (Status.CANCELLED, Status.WITHDRAWN):
        raise ValueError("terminate() only cancels or withdraws")
    before = (scholar.phase, scholar.status)
    scholar.status = status
    scholar.save(update_fields=["status"])
    _log(scholar, *before, actor, note)
    return scholar
