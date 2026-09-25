"""ThirdAttemptCase: submitted → Dean R&D review → VC decision.
The chain comes from third_attempt.review_chain (CONFIRMED: Dean R&D, then VC)."""
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from coursework.models import CourseResult, ThirdAttemptCase, ThirdAttemptCaseEvent
from identity.authz import Policy, Rule

from .common import audit_ok, authorize_or_deny, authorize_policy_or_deny, deny
from .config import RuleContext
from .examination import attempt_history, attempt_window_end, counted_failures


def _step_policy(step: int) -> tuple[str, Policy]:
    chain = RuleContext()("third_attempt.review_chain").value
    capability = chain[step]
    action = "academic.third_attempt.review" if step == 0 else "academic.third_attempt.decide"
    return action, Policy(rules=(Rule(capability),), privileged=True)


def submit_case(actor, enrollment, *, reason: str, supporting_document=None, request=None) -> ThirdAttemptCase:
    decision = authorize_or_deny(actor, "academic.third_attempt.submit", enrollment, request=request)
    scholar, course = enrollment.scholar, enrollment.offering.course
    ctx = RuleContext()
    history = attempt_history(scholar, course)
    failures, blockers = counted_failures(history, ctx)
    problems = list(blockers)
    if history["passed"]:
        problems.append("Course already passed")
    if history["pending"]:
        problems.append("A previous attempt has no ratified result yet")
    window_end = attempt_window_end(scholar, course, history, ctx)
    exhausted = failures >= ctx("exam.max_regular_attempts").value
    expired = window_end is not None and timezone.localdate() > window_end
    if not (exhausted or expired):
        problems.append("Regular attempts are not exhausted and the attempt window has not expired")
    if not reason.strip():
        problems.append("A reason is required")
    if problems:
        raise ValidationError(problems)
    results = {r.attempt_id: r for r in CourseResult.objects.filter(attempt__in=history["attempts"], is_current=True)}
    snapshot = [{"attempt_no": a.attempt_no, "exam_date": str(a.exam_date),
                 "outcome": getattr(results.get(a.pk), "outcome", None),
                 "grade": getattr(results.get(a.pk), "grade", None)} for a in history["attempts"]]
    try:
        with transaction.atomic():
            case = ThirdAttemptCase.objects.create(
                scholar=scholar, enrollment=enrollment, attempts_used=len(history["attempts"]),
                previous_attempts=snapshot, reason=reason, submitted_by=actor,
                supporting_document=supporting_document or "")
            ThirdAttemptCaseEvent.objects.create(case=case, action="SUBMITTED", actor=actor,
                                                 capability=decision.capability, remarks=reason)
            audit_ok(actor, "academic.third_attempt.submit", case, decision, request=request,
                     after={"attempts_used": case.attempts_used})
    except IntegrityError:
        raise ValidationError("An open third-attempt case already exists for this course")
    return case


def dean_review(actor, case: ThirdAttemptCase, *, recommend: bool, remarks: str, request=None) -> ThirdAttemptCase:
    action, policy = _step_policy(0)
    decision = authorize_policy_or_deny(actor, action, policy, case, request=request)
    if case.status != ThirdAttemptCase.Status.SUBMITTED:
        raise ValidationError(f"Case is {case.status}; only a submitted case can be reviewed")
    if case.submitted_by_id == actor.pk:
        deny(actor, action, "Separation of duties: the submitter cannot review the case", case, request=request)
    with transaction.atomic():
        case.status = (ThirdAttemptCase.Status.DEAN_RECOMMENDED if recommend
                       else ThirdAttemptCase.Status.DEAN_NOT_RECOMMENDED)
        case.dean_by, case.dean_at, case.dean_remarks = actor, timezone.now(), remarks
        case.save(update_fields=["status", "dean_by", "dean_at", "dean_remarks"])
        ThirdAttemptCaseEvent.objects.create(case=case, action=case.status, actor=actor,
                                             capability=decision.capability, remarks=remarks)
        audit_ok(actor, action, case, decision, request=request, after={"status": case.status})
    return case


def vc_decide(actor, case: ThirdAttemptCase, *, approve: bool, remarks: str, mentor=None,
              request=None) -> ThirdAttemptCase:
    action, policy = _step_policy(1)
    decision = authorize_policy_or_deny(actor, action, policy, case, request=request)
    if case.status not in (ThirdAttemptCase.Status.DEAN_RECOMMENDED, ThirdAttemptCase.Status.DEAN_NOT_RECOMMENDED):
        deny(actor, action, "The VC decides only after the Dean R&D review", case, request=request)
    if actor.pk in (case.submitted_by_id, case.dean_by_id):
        deny(actor, action, "Separation of duties: the submitter or reviewer cannot decide", case,
             request=request)
    mentor_rule = RuleContext()("third_attempt.mentor_required")
    if approve and mentor_rule.unresolved:
        raise ValidationError("Whether a faculty mentor must be assigned is unresolved "
                              "(third_attempt.mentor_required); approval requires institutional decision")
    if approve and mentor_rule.value is True and mentor is None:
        raise ValidationError("A faculty mentor must be assigned when approving (third_attempt.mentor_required)")
    with transaction.atomic():
        case.status = ThirdAttemptCase.Status.VC_APPROVED if approve else ThirdAttemptCase.Status.VC_REJECTED
        case.vc_by, case.vc_at, case.vc_remarks, case.mentor = actor, timezone.now(), remarks, mentor
        case.save(update_fields=["status", "vc_by", "vc_at", "vc_remarks", "mentor"])
        ThirdAttemptCaseEvent.objects.create(case=case, action=case.status, actor=actor,
                                             capability=decision.capability, remarks=remarks)
        audit_ok(actor, action, case, decision, request=request, after={"status": case.status})
    return case
