"""Post-result revaluation. No revaluation provision exists in the supplied
sources, so the whole path is governed by UNRESOLVED parameters
(revaluation.enabled, .request_window_days, .reviewer) and is blocked until
DYPIU configures them. The original ratified result and its raw marks are
never modified: revised marks live in RevaluationMark and a revised result
supersedes the original only after R&D Cell verification and COE ratification."""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from coursework.models import CourseResult, MarkEntry, ResultEvent, RevaluationCase, RevaluationMark
from identity.authz import Policy, Rule

from .common import audit_ok, authorize_or_deny, authorize_policy_or_deny, deny
from .config import RuleContext
from .results import _result_fields, compute


def _require_configured(ctx):
    enabled = ctx("revaluation.enabled")
    window = ctx("revaluation.request_window_days")
    reviewer = ctx("revaluation.reviewer")
    if enabled.unresolved or window.unresolved or reviewer.unresolved:
        raise ValidationError("Revaluation is not defined by the supplied sources (revaluation.enabled / "
                              "request_window_days / reviewer are unresolved); requires institutional decision")
    if not enabled.value:
        raise ValidationError("Revaluation is disabled (revaluation.enabled)")
    return window.value, reviewer.value


def request_revaluation(actor, result: CourseResult, *, reason: str, request=None) -> RevaluationCase:
    decision = authorize_or_deny(actor, ["academic.revaluation.request.self", "academic.revaluation.request.manage"],
                                 result, request=request)
    ctx = RuleContext()
    window_days, _ = _require_configured(ctx)
    if result.status != CourseResult.Status.RATIFIED or not result.is_current:
        raise ValidationError("Only the current ratified result can be revalued")
    if (timezone.now() - result.ratified_at).days > int(window_days):
        raise ValidationError(f"The revaluation window of {window_days} days has passed")
    if not reason.strip():
        raise ValidationError("A reason is required")
    try:
        with transaction.atomic():
            case = RevaluationCase.objects.create(original_result=result, reason=reason, requested_by=actor)
            audit_ok(actor, "academic.revaluation.request", case, decision, request=request,
                     after={"result_id": result.pk, "rule_versions": ctx.versions})
    except IntegrityError:
        raise ValidationError("A revaluation of this result is already open")
    return case


def review_revaluation(actor, case: RevaluationCase, *, revised_marks: dict, remarks: str,
                       request=None) -> RevaluationCase:
    """revised_marks: {Assessment: Decimal}. Creates a revised result (not
    current) when the outcome changes; otherwise closes the case unchanged."""
    ctx = RuleContext()
    _, reviewers = _require_configured(ctx)
    decision = authorize_policy_or_deny(actor, "academic.revaluation.review",
                                        Policy(rules=tuple(Rule(c) for c in reviewers), privileged=True),
                                        case, request=request)
    if case.status != RevaluationCase.Status.REQUESTED:
        raise ValidationError(f"Case is {case.status}")
    original = case.original_result
    if actor.pk in (case.requested_by_id, original.prepared_by_id, original.verified_by_id, original.ratified_by_id):
        deny(actor, "academic.revaluation.review", "Separation of duties: the requester or anyone in the original "
             "result's chain cannot review", case, request=request)
    attempt = original.attempt
    offering_assessments = {a.pk: a for a in attempt.enrollment.offering.assessments.all()}
    for assessment, marks in revised_marks.items():
        if assessment.pk not in offering_assessments or assessment.max_marks is None:
            raise ValidationError(f"{assessment} cannot be revalued for this result")
        if not (0 <= Decimal(str(marks)) <= assessment.max_marks):
            raise ValidationError(f"Marks for {assessment.name} must be between 0 and {assessment.max_marks}")
    if not remarks.strip():
        raise ValidationError("Review remarks are required")
    values = compute(attempt, ctx, overrides={a.pk: m for a, m in revised_marks.items()})
    unchanged = (values["total"], values["grade"], values["outcome"]) == (
        original.total_marks, original.grade, original.outcome)
    with transaction.atomic():
        for assessment, marks in revised_marks.items():
            prior = MarkEntry.objects.filter(attempt=attempt, assessment=assessment).first()
            RevaluationMark.objects.create(case=case, assessment=assessment, original_marks=getattr(prior, "marks", None),
                                           revised_marks=Decimal(str(marks)), reason=remarks)
        case.reviewer, case.reviewed_as, case.reviewed_at, case.review_remarks = (
            actor, decision.capability, timezone.now(), remarks)
        if unchanged:
            case.status = RevaluationCase.Status.REVIEWED_UNCHANGED
        else:
            revised = CourseResult.objects.create(attempt=attempt, is_current=False, supersedes=original,
                                                  **_result_fields(values, ctx, actor, decision.capability))
            ResultEvent.objects.create(result=revised, action=ResultEvent.Action.PREPARE, actor=actor,
                                       capability=decision.capability, remarks=f"Revaluation case {case.pk}")
            case.revised_result, case.status = revised, RevaluationCase.Status.REVISED_PENDING
        case.save()
        audit_ok(actor, "academic.revaluation.review", case, decision, request=request,
                 after={"status": case.status, "total": str(values["total"]), "grade": values["grade"],
                        "outcome": values["outcome"]})
    return case
