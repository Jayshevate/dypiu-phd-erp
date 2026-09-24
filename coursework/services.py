from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from core.models import ApprovalStatus
from phd_rules import durations, grading, policy

from .models import Course, CourseAttempt


def _attempt_needs_override(scholar, course, exam_date: date, attempt_no: int) -> str:
    if attempt_no > policy.COURSEWORK_MAX_ATTEMPTS:
        return f"attempt {attempt_no} exceeds {policy.COURSEWORK_MAX_ATTEMPTS}"
    first = scholar.course_attempts.order_by("exam_date").values_list("exam_date", flat=True).first()
    if first and exam_date > durations.coursework_attempt_window_end(first):
        return f"outside the {policy.COURSEWORK_ATTEMPT_WINDOW_YEARS}-year coursework window"
    return ""


@transaction.atomic
def register_attempt(scholar, course: Course, exam_date: date, override=None) -> CourseAttempt:
    previous = scholar.course_attempts.filter(course=course)
    if any(a.passed for a in previous):
        raise ValidationError(f"{course.code} already passed")
    attempt_no = previous.count() + 1
    reason = _attempt_needs_override(scholar, course, exam_date, attempt_no)
    if reason:
        if override is None or override.chain.code != "COURSEWORK_THIRD_ATTEMPT" or override.status != ApprovalStatus.APPROVED:
            raise ValidationError(f"{course.code}: {reason}; an approved COURSEWORK_THIRD_ATTEMPT override is required")
    return CourseAttempt.objects.create(scholar=scholar, course=course, attempt_no=attempt_no,
                                        exam_date=exam_date, override=override if reason else None)


def marks_from_components(attempt: CourseAttempt):
    """Weighted rubric total, or None if the course has no rubric. Returns
    (marks, failed_gate_name_or_None)."""
    components = list(attempt.course.components.all())
    if not components:
        return None, None
    if sum(c.weight for c in components) != 100:
        raise ValidationError(f"{attempt.course.code} rubric weights must sum to 100")
    scores = {s.component_id: s.percent for s in attempt.component_scores.all()}
    missing = [c.name for c in components if c.pk not in scores]
    if missing:
        raise ValidationError(f"Missing rubric scores: {', '.join(missing)}")
    total = sum(scores[c.pk] * c.weight / 100 for c in components)
    failed_gate = next((c.name for c in components if c.is_gate and scores[c.pk] < c.gate_min_percent), None)
    return Decimal(total).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), failed_gate


@transaction.atomic
def record_result(attempt: CourseAttempt, marks=None, special_grade="", ethics_cleared=None) -> CourseAttempt:
    """Grade an attempt from explicit marks, rubric components, or a special grade."""
    if attempt.course.has_ethics_submodule and ethics_cleared is None and not special_grade:
        raise ValidationError(f"{attempt.course.code}: ethics sub-module result is required")
    attempt.special_grade = special_grade
    attempt.ethics_cleared = ethics_cleared
    if special_grade:
        attempt.marks, attempt.grade, attempt.grade_point = None, special_grade, None
    else:
        failed_gate = None
        if marks is None:
            marks, failed_gate = marks_from_components(attempt)
            if marks is None:
                raise ValidationError("Provide marks or rubric component scores")
        attempt.marks = Decimal(str(marks))
        attempt.grade, attempt.grade_point = grading.FAIL if failed_gate else grading.grade_for_marks(marks)
    attempt.save()
    update_coursework_status(attempt.scholar, as_of=attempt.exam_date)
    return attempt


def best_results(scholar) -> list[grading.CourseResult]:
    """Latest attempt per course is the one that counts."""
    latest = {}
    for a in scholar.course_attempts.select_related("course").order_by("course_id", "attempt_no"):
        latest[a.course_id] = a
    return [grading.CourseResult(a.course.code, a.course.credits, a.grade_point, a.ethics_cleared)
            for a in latest.values() if a.grade]


def evaluate(scholar) -> grading.CourseworkVerdict:
    mandatory = Course.objects.filter(required_for_all=True).values_list("code", flat=True)
    return grading.evaluate_coursework(best_results(scholar), scholar.entry_qualification, mandatory)


def update_coursework_status(scholar, as_of: date):
    verdict = evaluate(scholar)
    if verdict.complete and not scholar.coursework_completed_on:
        scholar.coursework_completed_on = as_of
        scholar.save(update_fields=["coursework_completed_on"])
    return verdict
