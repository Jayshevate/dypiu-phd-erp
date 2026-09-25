"""Scholar academic record: profile, coursework status, standing, transcript.
All DERIVED from ratified results; nothing here is independently editable."""
from decimal import ROUND_HALF_UP, Decimal

from django.utils import timezone

from coursework.models import Course, CourseResult, ThirdAttemptCase

from . import grading
from .common import authorize_or_deny, coursework_category
from .config import RuleContext
from .examination import attempt_history, attempt_window_end, counted_failures


def _ratified(scholar):
    return (CourseResult.objects.filter(attempt__scholar=scholar, status=CourseResult.Status.RATIFIED, is_current=True)
            .select_related("attempt__course", "attempt__enrollment__offering__semester")
            .order_by("attempt__course__code", "attempt__attempt_no"))


def academic_profile(scholar, ctx=None) -> dict:
    ctx = ctx or RuleContext()
    category = coursework_category(scholar, ctx)
    return {
        "scholar_id": scholar.pk, "prn": scholar.prn, "name": scholar.name,
        "department": str(scholar.department), "entry_qualification": scholar.entry_qualification,
        "coursework_category": category, "study_mode": scholar.mode,
        "registration_date": scholar.registration_date, "status": scholar.status,
        "required_credits": ctx("coursework.required_credits").value[category],
        "electives_required": ctx("coursework.electives_required").value[category],
    }


def coursework_status(scholar, ctx=None) -> dict:
    ctx = ctx or RuleContext()
    profile = academic_profile(scholar, ctx)
    category = profile["coursework_category"]
    scheme = ctx("grading.scheme").value
    min_grade = ctx("coursework.continuation_min_grade").value
    min_gpa = Decimal(str(ctx("coursework.min_gpa").value))

    latest = {}
    for r in _ratified(scholar):
        latest[r.attempt.course_id] = r
    passed = {cid: r for cid, r in latest.items() if r.outcome == CourseResult.Outcome.PASS}
    reasons = []

    required = [c for c in Course.objects.filter(is_active=True, required_for_all=True)
                .exclude(category=Course.Category.ELECTIVE) if c.applies_to(category)]
    for c in required:
        if c.pk not in passed:
            reasons.append(f"{c.code} not passed")
    electives_passed = [r for r in passed.values() if r.attempt.course.category == Course.Category.ELECTIVE]
    if len(electives_passed) < profile["electives_required"]:
        reasons.append(f"{len(electives_passed)} of {profile['electives_required']} electives passed")
    credits = sum(r.attempt.course.credits for r in passed.values())
    if credits < profile["required_credits"]:
        reasons.append(f"Credits earned {credits} < required {profile['required_credits']}")
    graded = [r for r in latest.values() if r.grade_point is not None]
    total_credits = sum(r.attempt.course.credits for r in graded)
    gpa = (sum(r.grade_point * r.attempt.course.credits for r in graded) / total_credits
           ).quantize(Decimal("0.01"), ROUND_HALF_UP) if total_credits else None
    if gpa is None:
        reasons.append(f"No ratified graded result yet (GPA {min_gpa} required)")
    elif gpa < min_gpa:
        reasons.append(f"GPA {gpa} below {min_gpa}")
    for r in passed.values():
        if not grading.grade_at_least(r.grade, min_grade, scheme):
            reasons.append(f"{r.attempt.course.code}: grade {r.grade} below the continuation minimum {min_grade}")
    legacy = legacy_attempts(scholar).count()
    if legacy:
        reasons.append(f"{legacy} legacy attempt record(s) must be re-entered through the Academic services")
    return {"complete": not reasons, "reasons": reasons, "credits_earned": credits, "gpa": gpa,
            "required_credits": profile["required_credits"], "electives_passed": len(electives_passed),
            "provisional": ctx.provisional or any(r.is_provisional for r in latest.values()),
            "rule_versions": ctx.versions}


class Standing:
    COURSEWORK_COMPLETE = "COURSEWORK_COMPLETE"
    IN_PROGRESS = "IN_PROGRESS"
    THIRD_ATTEMPT_REQUIRED = "THIRD_ATTEMPT_REQUIRED"
    THIRD_ATTEMPT_APPROVED = "THIRD_ATTEMPT_APPROVED"
    ATTEMPTS_EXHAUSTED = "ATTEMPTS_EXHAUSTED"
    DECISION_REQUIRED = "DECISION_REQUIRED"


def academic_standing(scholar, ctx=None) -> dict:
    """Derived standing plus per-course flags. No standing is stored."""
    ctx = ctx or RuleContext()
    status = coursework_status(scholar, ctx)
    if status["complete"]:
        return {"standing": Standing.COURSEWORK_COMPLETE, "courses": {}, "reasons": []}
    max_regular = ctx("exam.max_regular_attempts").value
    max_total = ctx("exam.max_attempts_with_approval").value
    courses, overall = {}, Standing.IN_PROGRESS
    rank = [Standing.IN_PROGRESS, Standing.THIRD_ATTEMPT_APPROVED, Standing.THIRD_ATTEMPT_REQUIRED,
            Standing.DECISION_REQUIRED, Standing.ATTEMPTS_EXHAUSTED]
    course_ids = {e.offering.course_id for e in scholar.course_enrollments.exclude(status="WITHDRAWN")}
    for course in Course.objects.filter(pk__in=course_ids):
        history = attempt_history(scholar, course)
        if history["passed"]:
            continue
        failures, blockers = counted_failures(history, ctx)
        window_end = attempt_window_end(scholar, course, history, ctx)
        expired = window_end is not None and timezone.localdate() > window_end
        approved = ThirdAttemptCase.objects.filter(scholar=scholar, enrollment__offering__course=course,
                                                   status=ThirdAttemptCase.Status.VC_APPROVED).exists()
        if blockers:
            s = Standing.DECISION_REQUIRED
        elif failures >= max_total:
            s = Standing.ATTEMPTS_EXHAUSTED
        elif (failures >= max_regular or expired) and approved:
            s = Standing.THIRD_ATTEMPT_APPROVED
        elif failures >= max_regular or expired:
            s = Standing.THIRD_ATTEMPT_REQUIRED
        else:
            s = Standing.IN_PROGRESS
        courses[course.code] = {"standing": s, "counted_failures": failures, "window_end": window_end}
        if rank.index(s) > rank.index(overall):
            overall = s
    return {"standing": overall, "courses": courses, "reasons": status["reasons"]}


def legacy_attempts(scholar):
    """Attempts graded through the retired legacy path. They are NOT counted
    by the authoritative record until re-entered through the Academic services."""
    from coursework.models import CourseAttempt
    return CourseAttempt.objects.filter(scholar=scholar, status=CourseAttempt.Status.LEGACY)


TRANSCRIPT_FORMAT_VERSION = "1"


def transcript_data(scholar, ctx=None) -> dict:
    """Canonical transcript content derived from RATIFIED current results.
    Deterministic (no timestamps), so it can be hashed and later verified."""
    ctx = ctx or RuleContext()
    rows = []
    for r in _ratified(scholar):
        a = r.attempt
        semester = a.enrollment.offering.semester if a.enrollment_id else None
        rows.append({
            "semester": str(semester) if semester else "", "course_code": a.course.code,
            "course_title": a.course.title, "credits": a.course.credits, "attempt_no": a.attempt_no,
            "marks": str(r.total_marks) if r.total_marks is not None else None, "grade": r.grade,
            "grade_point": str(r.grade_point) if r.grade_point is not None else None, "result": r.outcome,
            "credits_earned": a.course.credits if r.outcome == CourseResult.Outcome.PASS else 0,
            "ratified_on": r.ratified_at.date().isoformat(), "result_id": r.pk, "provisional": r.is_provisional,
            "revalued": r.supersedes_id is not None,
        })
    status = coursework_status(scholar, ctx)
    profile = academic_profile(scholar, ctx)
    profile["registration_date"] = profile["registration_date"].isoformat() if profile["registration_date"] else None
    return {
        "format_version": TRANSCRIPT_FORMAT_VERSION,
        "profile": profile,
        "rows": rows,
        "credits_earned": status["credits_earned"],
        "gpa": str(status["gpa"]) if status["gpa"] is not None else None,
        "coursework_complete": status["complete"],
        "provisional": status["provisional"],
        "legacy_records_excluded": legacy_attempts(scholar).count(),
    }


def content_hash(data: dict) -> str:
    import hashlib
    import json
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def transcript(actor, scholar, *, request=None) -> dict:
    """Transcript view assembled from ratified results only; ready for official rendering."""
    authorize_or_deny(actor, "academic.transcript.view", scholar, request=request)
    data = transcript_data(scholar)
    semesters = {}
    for row in data["rows"]:
        semesters.setdefault(row["semester"], []).append(row)
    return {**data, "semesters": semesters, "content_hash": content_hash(data), "generated_at": timezone.now(),
            "source": "Derived from ratified CourseResult records; not independently stored"}


def issue_transcript(actor, scholar, *, request=None):
    """Record an official issue: version, date, content hash and verification
    code only. No marks or grades are stored; no signature or seal is generated."""
    import secrets

    from django.db import transaction
    from django.db.models import Max

    from coursework.models import TranscriptIssue

    from .common import audit_ok
    decision = authorize_or_deny(actor, "academic.transcript.issue", scholar, request=request)
    data = transcript_data(scholar)
    if not data["rows"]:
        from django.core.exceptions import ValidationError
        raise ValidationError("No ratified results: nothing to issue")
    with transaction.atomic():
        version = (TranscriptIssue.objects.select_for_update().filter(scholar=scholar)
                   .aggregate(v=Max("version"))["v"] or 0) + 1
        issue = TranscriptIssue.objects.create(scholar=scholar, version=version, content_hash=content_hash(data),
                                               verification_code=secrets.token_hex(10), provisional=data["provisional"],
                                               issued_by=actor)
        audit_ok(actor, "academic.transcript.issue", issue, decision, request=request,
                 after={"version": version, "content_hash": issue.content_hash, "provisional": issue.provisional})
    return issue


def verify_transcript(verification_code: str) -> dict:
    """Public verification: is the issued transcript still what the records say?"""
    from coursework.models import TranscriptIssue
    issue = TranscriptIssue.objects.filter(verification_code=verification_code).select_related("scholar").first()
    if issue is None:
        return {"valid": False, "reason": "Unknown verification code"}
    current = content_hash(transcript_data(issue.scholar))
    latest = issue.scholar.transcript_issues.order_by("-version").first()
    return {"valid": True, "prn": issue.scholar.prn, "version": issue.version, "issued_at": issue.issued_at,
            "provisional": issue.provisional,
            "matches_current_records": current == issue.content_hash,
            "superseded_by_version": latest.version if latest.version != issue.version else None}


def refresh_coursework_completion(scholar):
    """Mirror completion into the legacy Scholar.coursework_completed_on field (read by the lifecycle gate)."""
    status = coursework_status(scholar)
    if status["complete"] and scholar.coursework_completed_on is None:
        scholar.coursework_completed_on = timezone.localdate()
        scholar.save(update_fields=["coursework_completed_on"])
    return status
