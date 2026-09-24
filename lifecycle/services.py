"""Milestone workflows that sit inside the lifecycle phases."""
from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.db import transaction

from core.approvals import on_decision, start_approval
from core.models import ApprovalStatus
from phd_rules import durations, policy
from phd_rules.calendar_rules import viva_notice_dates
from scholars.models import Status

from . import engine
from .models import DCReview, DegreeAward, ExaminerNomination, ExaminerReport, ProgressReport, ResearchProposal, Thesis, Viva


# --- Research proposal ------------------------------------------------------

@transaction.atomic
def record_proposal_outcome(proposal: ResearchProposal, outcome: str, remarks="", actor=None):
    proposal.outcome = outcome
    proposal.remarks = remarks
    proposal.save(update_fields=["outcome", "remarks"])
    failures = proposal.scholar.proposals.filter(outcome__in=ResearchProposal.FAILING).count()
    if failures >= policy.PROPOSAL_MAX_FAILED_ATTEMPTS:
        engine.terminate(proposal.scholar, Status.CANCELLED,
                         f"Research proposal not recommended {failures} times", actor)
    return proposal


# --- Progress monitoring ----------------------------------------------------

def open_warnings(scholar):
    return scholar.progress_reports.filter(outcome=ProgressReport.Outcome.UNSATISFACTORY, dc_review__isnull=True)


@transaction.atomic
def record_tac_review(report: ProgressReport, outcome: str, meeting_date: date, minutes=""):
    report.outcome = outcome
    report.tac_meeting_date = meeting_date
    report.tac_minutes = minutes
    report.save()
    warnings = list(open_warnings(report.scholar))
    if len(warnings) >= policy.WARNINGS_BEFORE_DC_REVIEW:
        review = DCReview.objects.create(scholar=report.scholar, opened_on=meeting_date,
                                         reason=f"{len(warnings)} unsatisfactory TAC progress reviews")
        ProgressReport.objects.filter(pk__in=[w.pk for w in warnings]).update(dc_review=review)
        return review
    return None


@transaction.atomic
def decide_dc_review(review: DCReview, decision: str, on: date, remarks="", actor=None):
    review.decision, review.decided_on, review.remarks = decision, on, remarks
    review.save()
    if decision == DCReview.Decision.CANCEL:
        engine.terminate(review.scholar, Status.CANCELLED, f"DC review: {remarks}", actor)


# --- Thesis evaluation --------------------------------------------------------

@transaction.atomic
def submit_examiner_panel(thesis: Thesis, nominees: list[dict], user=None):
    if len(nominees) < policy.EXAMINER_PANEL_MIN_NOMINEES:
        raise ValidationError(f"Panel needs at least {policy.EXAMINER_PANEL_MIN_NOMINEES} nominees")
    ExaminerNomination.objects.bulk_create(ExaminerNomination(thesis=thesis, **n) for n in nominees)
    thesis.panel_approval = start_approval("EXAMINER_SELECTION", summary=f"{thesis.scholar.prn}: examiner panel",
                                           scholar=thesis.scholar, target=thesis, user=user)
    thesis.save(update_fields=["panel_approval"])
    return thesis.panel_approval


@transaction.atomic
def select_examiners(thesis: Thesis, chosen: list[ExaminerNomination], dispatched_on: date):
    if not thesis.panel_approval or thesis.panel_approval.status != ApprovalStatus.APPROVED:
        raise ValidationError("Examiner panel has not been approved")
    if len(chosen) != policy.EXAMINERS_SELECTED or any(n.thesis_id != thesis.pk for n in chosen):
        raise ValidationError(f"Select exactly {policy.EXAMINERS_SELECTED} examiners from this thesis's panel")
    for n in chosen:
        n.selected = True
        n.save(update_fields=["selected"])
        ExaminerReport.objects.create(examiner=n, dispatched_on=dispatched_on)


@transaction.atomic
def replace_examiner(report: ExaminerReport, replacement: ExaminerNomination, dispatched_on: date):
    """Escalation path when an examiner has not reported after reminder + grace."""
    if replacement.thesis_id != report.examiner.thesis_id or replacement.selected:
        raise ValidationError("Replacement must be an unselected nominee from the same panel")
    report.examiner.replaced = True
    report.examiner.save(update_fields=["replaced"])
    replacement.selected = True
    replacement.save(update_fields=["selected"])
    return ExaminerReport.objects.create(examiner=replacement, dispatched_on=dispatched_on)


def examiner_escalations(today: date):
    """(to_remind, to_replace) querysets of outstanding reports."""
    pending = ExaminerReport.objects.filter(received_on__isnull=True, examiner__replaced=False)
    remind = pending.filter(reminded_on__isnull=True,
                            dispatched_on__lte=today - timedelta(days=policy.EXAMINER_REMINDER_DAYS))
    replace = pending.filter(reminded_on__lte=today - timedelta(days=policy.EXAMINER_ESCALATION_GRACE_DAYS))
    return remind, replace


# --- Viva ------------------------------------------------------------------------

def validate_viva(viva: Viva):
    problems = []
    notify_deadline, invite_deadline = viva_notice_dates(viva.scheduled_on)
    if viva.candidate_notified_on and viva.candidate_notified_on > notify_deadline:
        problems.append(f"Candidate must be notified by {notify_deadline}")
    if viva.invitation_published_on and viva.invitation_published_on > invite_deadline:
        problems.append(f"Open invitation must be published by {invite_deadline}")
    last = (ExaminerReport.objects.filter(examiner__thesis=viva.thesis, received_on__isnull=False)
            .order_by("-received_on").values_list("received_on", flat=True).first())
    if last and viva.scheduled_on > durations.viva_due(last):
        problems.append(f"Viva must be held by {durations.viva_due(last)} (2 months after last report)")
    if problems:
        raise ValidationError(problems)


# --- Degree award -----------------------------------------------------------

@transaction.atomic
def start_degree_award(scholar, user=None) -> DegreeAward:
    award, _ = DegreeAward.objects.get_or_create(scholar=scholar)
    if award.approval is None:
        award.approval = start_approval("DEGREE_AWARD", summary=f"{scholar.prn}: award of PhD",
                                        scholar=scholar, target=award, user=user)
        award.save(update_fields=["approval"])
    return award


@on_decision("DEGREE_AWARD")
def _close_degree(req, approved):
    if approved:
        DegreeAward.objects.filter(approval=req).update(approved_on=date.today())
