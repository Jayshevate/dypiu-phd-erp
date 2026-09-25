"""Elective → ElectiveProposal → Approval → Offering → Enrollment.
The approving authority (elective.approval_authority) and whether a supervisor
recommendation is required (elective.supervisor_recommendation_required) are
UNRESOLVED (RD-38): decisions are blocked until DYPIU configures them."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from coursework.models import Course, ElectiveList, ElectiveListItem, ElectiveProposal, ElectiveProposalEvent
from identity.authz import Policy, Rule

from .common import audit_ok, authorize_or_deny, authorize_policy_or_deny, coursework_category, deny
from .config import RuleContext


def _event(proposal, action, actor, decision, remarks=""):
    ElectiveProposalEvent.objects.create(proposal=proposal, action=action, actor=actor,
                                         capability=decision.capability, remarks=remarks)


def propose(actor, scholar, *, semester, justification, course=None, proposed_code="", proposed_title="",
            credits=None, research_relevance="", request=None) -> ElectiveProposal:
    decision = authorize_or_deny(actor, "academic.elective.propose", scholar, request=request)
    ctx = RuleContext()
    if course is not None:
        if course.category != Course.Category.ELECTIVE or not course.is_active:
            raise ValidationError(f"{course.code} is not an active elective")
        if not course.applies_to(coursework_category(scholar, ctx)):
            raise ValidationError(f"{course.code} is not open to the scholar's coursework category")
        if ctx("elective.proposal_requires_approved_list").value and not ElectiveListItem.objects.filter(
                course=course, elective_list__semester=semester,
                elective_list__status=ElectiveList.Status.APPROVED).exists():
            raise ValidationError(f"{course.code} is not on an approved elective list for {semester}")
        credits = course.credits
    elif not proposed_title.strip() or not credits:
        raise ValidationError("Propose a catalogue elective, or give a title and credits for a new course")
    if not justification.strip():
        raise ValidationError("A justification is required")
    proposal = ElectiveProposal(scholar=scholar, semester=semester, course=course, proposed_code=proposed_code,
                                proposed_title=proposed_title, credits=credits, justification=justification,
                                research_relevance=research_relevance, submitted_by=actor)
    proposal.full_clean()
    with transaction.atomic():
        proposal.save()
        _event(proposal, "SUBMITTED", actor, decision, justification)
        audit_ok(actor, "academic.elective.propose", proposal, decision, request=request,
                 after={"course": getattr(course, "code", proposed_code), "credits": credits})
    return proposal


def supervisor_recommend(actor, proposal, *, recommend: bool, remarks="", request=None) -> ElectiveProposal:
    decision = authorize_or_deny(actor, "academic.elective.recommend", proposal, request=request)
    if proposal.status != ElectiveProposal.Status.SUBMITTED:
        raise ValidationError(f"Proposal is {proposal.status}")
    with transaction.atomic():
        proposal.status = (ElectiveProposal.Status.SUPERVISOR_RECOMMENDED if recommend
                           else ElectiveProposal.Status.SUPERVISOR_NOT_RECOMMENDED)
        proposal.supervisor_by, proposal.supervisor_at, proposal.supervisor_remarks = actor, timezone.now(), remarks
        proposal.save(update_fields=["status", "supervisor_by", "supervisor_at", "supervisor_remarks"])
        _event(proposal, proposal.status, actor, decision, remarks)
        audit_ok(actor, "academic.elective.recommend", proposal, decision, request=request,
                 after={"status": proposal.status})
    return proposal


def decide(actor, proposal, *, approve: bool, remarks="", request=None) -> ElectiveProposal:
    ctx = RuleContext()
    authority = ctx("elective.approval_authority")
    if authority.unresolved:
        raise ValidationError("The elective approving authority is unresolved (elective.approval_authority, "
                              "RD-38); requires institutional decision")
    policy = Policy(rules=tuple(Rule(c) for c in authority.value), privileged=True)
    decision = authorize_policy_or_deny(actor, "academic.elective.decide", policy, proposal, request=request)
    needs_rec = ctx("elective.supervisor_recommendation_required")
    if needs_rec.unresolved:
        raise ValidationError("Whether a supervisor recommendation is required is unresolved "
                              "(elective.supervisor_recommendation_required); requires institutional decision")
    open_states = [ElectiveProposal.Status.SUBMITTED, ElectiveProposal.Status.SUPERVISOR_RECOMMENDED,
                   ElectiveProposal.Status.SUPERVISOR_NOT_RECOMMENDED]
    if needs_rec.value is True:
        open_states = [ElectiveProposal.Status.SUPERVISOR_RECOMMENDED, ElectiveProposal.Status.SUPERVISOR_NOT_RECOMMENDED]
    if proposal.status not in open_states:
        raise ValidationError(f"Proposal is {proposal.status}; it cannot be decided now")
    if proposal.submitted_by_id == actor.pk or proposal.supervisor_by_id == actor.pk:
        deny(actor, "academic.elective.decide", "Separation of duties: the proposer or recommender cannot decide",
             proposal, request=request)
    notes = []
    with transaction.atomic():
        proposal.status = ElectiveProposal.Status.APPROVED if approve else ElectiveProposal.Status.REJECTED
        proposal.decided_by, proposal.decided_as = actor, decision.capability
        proposal.decided_at, proposal.decision_remarks = timezone.now(), remarks
        proposal.save(update_fields=["status", "decided_by", "decided_as", "decided_at", "decision_remarks"])
        _event(proposal, proposal.status, actor, decision, remarks)
        audit_ok(actor, "academic.elective.decide", proposal, decision, request=request,
                 after={"status": proposal.status, "rule_versions": ctx.versions}, reasons=notes)
    return proposal
