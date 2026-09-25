"""Result computation and chain of custody:
prepared (Course Coordinator / SDRC / revaluation reviewer) → verified (R&D Cell) → ratified (COE).

Rules that are UNRESOLVED block the computation instead of being guessed:
  result.absence_policy                        (treatment of absences)
  exam.reattempt_carries_continuous_assessment (CA on re-attempts)
Results computed with AMBIGUOUS parameters are provisional and can be
ratified only with an explicit, audited acknowledgement by the COE."""
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from coursework.models import (ActivitySubmission, Assessment, Course, CourseAttempt, CourseResult, MarkEntry,
                               ResultEvent, RevaluationCase, ScholarCourseEnrollment)

from . import grading
from .common import audit_ok, authorize_or_deny, deny
from .config import RuleContext

SUPPORTED_ABSENCE_POLICIES = {"ABSENT_IF_ANY_COMPONENT_ABSENT"}


@dataclass(frozen=True)
class Mark:
    marks: Optional[Decimal]
    is_absent: bool
    cleared: Optional[bool]


def current_result(attempt) -> Optional[CourseResult]:
    return CourseResult.objects.filter(attempt=attempt, is_current=True).first()


def _marks_for(attempt, assessments, ctx) -> dict:
    entries = {m.assessment_id: Mark(m.marks, m.is_absent, m.cleared)
               for m in MarkEntry.objects.filter(attempt=attempt, assessment__in=assessments)}
    if attempt.attempt_no > 1 and any(a.kind == Assessment.Kind.CONTINUOUS for a in assessments):
        carry = ctx("exam.reattempt_carries_continuous_assessment")
        if carry.unresolved:
            raise ValidationError("Whether Continuous Assessment carries into a re-attempt is unresolved "
                                  "(exam.reattempt_carries_continuous_assessment); requires institutional decision")
        if carry.value is True:
            previous = CourseAttempt.objects.filter(scholar=attempt.scholar, course=attempt.course,
                                                    attempt_no=attempt.attempt_no - 1).first()
            for a in assessments:
                if a.kind == Assessment.Kind.CONTINUOUS and a.pk not in entries and previous is not None:
                    prior = MarkEntry.objects.filter(attempt=previous, assessment=a).first()
                    if prior is not None:
                        entries[a.pk] = Mark(prior.marks, prior.is_absent, prior.cleared)
    return entries


def compute(attempt: CourseAttempt, ctx: RuleContext, overrides: Optional[dict] = None) -> dict:
    """Pure computation from raw marks (plus revaluation overrides, if any).
    Missing marks are NEVER defaulted."""
    enrollment = attempt.enrollment
    course = attempt.course
    assessments = list(enrollment.offering.assessments.all())
    if not assessments:
        raise ValidationError("The offering has no assessment scheme")
    scored = [a for a in assessments if a.kind != Assessment.Kind.MANDATORY_MODULE]
    modules = [a for a in assessments if a.kind == Assessment.Kind.MANDATORY_MODULE]
    if sum((a.weight for a in scored), Decimal("0")) != Decimal("100"):
        raise ValidationError("Assessment weights do not total 100")

    if course.category == Course.Category.ACTIVITY:
        required = ctx("activity.required_artefacts").value.get(course.activity_type, [])
        accepted = set(ActivitySubmission.objects.filter(enrollment=enrollment, status="ACCEPTED")
                       .values_list("artefact", flat=True))
        missing_artefacts = [a for a in required if a not in accepted]
        if missing_artefacts:
            raise ValidationError(f"Required activity artefacts not accepted by SDRC: {missing_artefacts}")

    entries = _marks_for(attempt, assessments, ctx)
    for assessment_id, marks in (overrides or {}).items():
        entries[assessment_id] = Mark(Decimal(str(marks)), False, None)
    missing = [a.name for a in assessments if a.pk not in entries]
    if missing:
        raise ValidationError(f"Marks missing for: {missing}")

    scheme = ctx("grading.scheme").value
    absent = [a.name for a in scored if entries[a.pk].is_absent]
    if absent:
        policy = ctx("result.absence_policy")
        if policy.unresolved:
            raise ValidationError(f"Absent in {absent}: the treatment of absences is unresolved "
                                  "(result.absence_policy); requires institutional decision")
        if policy.value not in SUPPORTED_ABSENCE_POLICIES:
            raise ValidationError(f"Unsupported result.absence_policy {policy.value!r}")
        return {"total": None, "grade": "AB", "grade_point": None, "outcome": CourseResult.Outcome.ABSENT,
                "reasons": ["Absent in " + ", ".join(absent)], "module_cleared": None}

    total = sum((entries[a.pk].marks / a.max_marks * a.weight for a in scored), Decimal("0"))
    total = total.quantize(Decimal("0.01"), ROUND_HALF_UP)
    grade, point = grading.grade_for(total, scheme)
    minimum = Decimal(str(ctx("coursework.course_pass_min_marks").value))
    applies_to = ctx("coursework.course_pass_min_applies_to").value
    passed, reasons = True, []
    if applies_to == "COURSE":
        if grading.apply_rounding(total, scheme) < minimum:
            passed = False
            reasons.append(f"Course total {total} below the minimum {minimum}")
    elif applies_to == "ASSESSMENT":
        for a in scored:
            pct = entries[a.pk].marks / a.max_marks * 100
            if pct < minimum:
                passed = False
                reasons.append(f"{a.name}: {pct.quantize(Decimal('0.01'))}% below the minimum {minimum}")
    else:
        raise ValidationError(f"Unknown coursework.course_pass_min_applies_to {applies_to!r}")
    module_cleared = None
    if modules:
        module_cleared = all(entries[m.pk].cleared for m in modules)
        if not module_cleared:
            passed = False
            reasons.append("Mandatory module not cleared: " + ", ".join(m.name for m in modules
                                                                         if not entries[m.pk].cleared))
    return {"total": total, "grade": grade, "grade_point": point,
            "outcome": CourseResult.Outcome.PASS if passed else CourseResult.Outcome.FAIL,
            "reasons": reasons, "module_cleared": module_cleared}


def _result_fields(values, ctx, actor, capability):
    return dict(total_marks=values["total"], grade=values["grade"], grade_point=values["grade_point"],
                outcome=values["outcome"], reasons=values["reasons"], rule_versions=ctx.versions,
                is_provisional=ctx.provisional, status=CourseResult.Status.PREPARED, prepared_by=actor,
                prepared_as=capability, prepared_at=timezone.now(), verified_by=None, verified_at=None,
                ratified_by=None, ratified_at=None)


def prepare_result(actor, attempt: CourseAttempt, *, request=None) -> CourseResult:
    course = attempt.course
    action = ("academic.result.prepare.activity" if course.category == Course.Category.ACTIVITY
              else "academic.result.prepare.course")
    decision = authorize_or_deny(actor, action, attempt, request=request)
    if attempt.status != CourseAttempt.Status.SCHEDULED or attempt.enrollment_id is None:
        raise ValidationError("Only an attempt under evaluation can have a result prepared")
    existing = current_result(attempt)
    if existing is not None and existing.status in (CourseResult.Status.VERIFIED, CourseResult.Status.RATIFIED):
        deny(actor, action, f"Result is already {existing.status}; it cannot be re-prepared", existing,
             request=request)
    ctx = RuleContext()
    values = compute(attempt, ctx)
    fields = _result_fields(values, ctx, actor, decision.capability)
    with transaction.atomic():
        if existing is None:
            result = CourseResult.objects.create(attempt=attempt, **fields)
        else:
            for k, v in fields.items():
                setattr(existing, k, v)
            existing.save()
            result = existing
        ResultEvent.objects.create(result=result, action=ResultEvent.Action.PREPARE, actor=actor,
                                   capability=decision.capability)
        audit_ok(actor, action, result, decision, request=request,
                 after={"total": str(values["total"]), "grade": values["grade"], "outcome": values["outcome"],
                        "provisional": ctx.provisional, "rule_versions": ctx.versions})
    return result


def verify_result(actor, result: CourseResult, *, remarks="", request=None) -> CourseResult:
    decision = authorize_or_deny(actor, "academic.result.verify", result, request=request)
    if result.status != CourseResult.Status.PREPARED:
        raise ValidationError(f"Only a PREPARED result can be verified (status {result.status})")
    if result.prepared_by_id == actor.pk:
        deny(actor, "academic.result.verify", "Separation of duties: the preparer cannot verify", result,
             request=request)
    with transaction.atomic():
        result.status, result.verified_by, result.verified_at = CourseResult.Status.VERIFIED, actor, timezone.now()
        result.save(update_fields=["status", "verified_by", "verified_at"])
        ResultEvent.objects.create(result=result, action=ResultEvent.Action.VERIFY, actor=actor,
                                   capability=decision.capability, remarks=remarks)
        audit_ok(actor, "academic.result.verify", result, decision, request=request)
    return result


def ratify_result(actor, result: CourseResult, *, remarks="", acknowledge_provisional: bool = False,
                  request=None) -> CourseResult:
    """COE ratification. The result becomes final and the attempt's legacy
    fields are written from it. A provisional result (computed with AMBIGUOUS
    parameters) needs an explicit acknowledgement, which is recorded."""
    from .records import refresh_coursework_completion

    decision = authorize_or_deny(actor, "academic.result.ratify", result, request=request)
    if result.status != CourseResult.Status.VERIFIED:
        raise ValidationError(f"Only a VERIFIED result can be ratified (status {result.status})")
    if actor.pk in (result.prepared_by_id, result.verified_by_id):
        deny(actor, "academic.result.ratify", "Separation of duties: the preparer or verifier cannot ratify",
             result, request=request)
    if result.is_provisional and not acknowledge_provisional:
        raise ValidationError("This result was computed with AMBIGUOUS / UNRESOLVED rule parameters "
                              f"{sorted(k for k, v in result.rule_versions.items() if v['status'] != 'CONFIRMED')}; "
                              "ratification requires explicit acknowledgement")
    note = "Ratified with acknowledged provisional rules" if result.is_provisional else ""
    attempt = result.attempt
    with transaction.atomic():
        original = result.supersedes
        if original is not None:
            original._supersede_ok = True
            original.is_current = False
            original.save(update_fields=["is_current"])
            result.is_current = True
        result.status, result.ratified_by, result.ratified_at = CourseResult.Status.RATIFIED, actor, timezone.now()
        result.save(update_fields=["status", "ratified_by", "ratified_at", "is_current"])
        ResultEvent.objects.create(result=result, action=ResultEvent.Action.RATIFY, actor=actor,
                                   capability=decision.capability, remarks="; ".join(filter(None, [remarks, note])))
        _sync_attempt(attempt, result)
        if original is not None:
            RevaluationCase.objects.filter(revised_result=result).update(status=RevaluationCase.Status.COMPLETED)
        audit_ok(actor, "academic.result.ratify", result, decision, request=request,
                 after={"outcome": result.outcome, "grade": result.grade, "provisional": result.is_provisional,
                        "supersedes": getattr(original, "pk", None)}, reasons=[note] if note else [])
        refresh_coursework_completion(attempt.scholar)
    return result


def _sync_attempt(attempt, result):
    """Write the ratified result into the attempt's legacy fields (read-only mirror)."""
    gp = result.grade_point
    attempt.marks = result.total_marks
    attempt.grade = "" if result.outcome == CourseResult.Outcome.ABSENT else result.grade
    attempt.grade_point = int(gp) if gp is not None and gp == gp.to_integral_value() else None
    attempt.special_grade = "AB" if result.outcome == CourseResult.Outcome.ABSENT else ""
    entries = MarkEntry.objects.filter(attempt=attempt, assessment__kind=Assessment.Kind.MANDATORY_MODULE)
    attempt.ethics_cleared = all(e.cleared for e in entries) if entries.exists() else None
    attempt.status = CourseAttempt.Status.RESULT_RATIFIED
    attempt.save(update_fields=["marks", "grade", "grade_point", "special_grade", "ethics_cleared", "status"])
    enrollment_status = (ScholarCourseEnrollment.Status.COMPLETED if result.outcome == CourseResult.Outcome.PASS
                         else ScholarCourseEnrollment.Status.ENROLLED)
    ScholarCourseEnrollment.objects.filter(pk=attempt.enrollment_id).update(status=enrollment_status)


def return_result(actor, result: CourseResult, *, reason: str, request=None) -> CourseResult:
    decision = authorize_or_deny(actor, "academic.result.return", result, request=request)
    if result.status == CourseResult.Status.RATIFIED:
        deny(actor, "academic.result.return", "A ratified result is final; use a revaluation case", result,
             request=request)
    if result.status not in (CourseResult.Status.PREPARED, CourseResult.Status.VERIFIED):
        raise ValidationError(f"Cannot return a result in status {result.status}")
    if not reason.strip():
        raise ValidationError("A reason is required")
    with transaction.atomic():
        before = result.status
        result.status = CourseResult.Status.RETURNED
        result.save(update_fields=["status"])
        ResultEvent.objects.create(result=result, action=ResultEvent.Action.RETURN, actor=actor,
                                   capability=decision.capability, remarks=reason)
        if result.supersedes_id:
            RevaluationCase.objects.filter(revised_result=result).update(status=RevaluationCase.Status.REJECTED)
        audit_ok(actor, "academic.result.return", result, decision, request=request, before={"status": before},
                 after={"status": result.status}, reasons=[reason])
    return result


def visible_result(actor, attempt: CourseAttempt, *, request=None):
    """A scholar sees a result only once ratified; staff with view access see any stage."""
    decision = authorize_or_deny(actor, "academic.record.view", attempt, request=request)
    result = current_result(attempt)
    if result is None:
        return None
    if decision.capability == "SCHOLAR" and result.status != CourseResult.Status.RATIFIED:
        return None
    return result
