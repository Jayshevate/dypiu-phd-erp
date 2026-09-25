"""ExamCycle → Exam → ExamEligibility → ExamRegistration → ExamAttempt."""
from datetime import date

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from coursework.models import (Course, CourseAttempt, CourseResult, Exam, ExamCycle, ExamEligibility,
                               ExamRegistration, FeeClearance, ScholarCourseEnrollment, SemesterRegistration,
                               ThirdAttemptCase, ThirdAttemptCaseEvent)
from phd_rules.dates import add_years
from scholars.models import Status as ScholarStatus

from .attendance import attendance_summary
from .common import audit_ok, authorize_or_deny, deny
from .config import RuleContext


# --- Cycles & exams ------------------------------------------------------------------

def create_exam_cycle(actor, *, semester, name, registration_opens, registration_closes, exam_start, exam_end,
                      request=None) -> ExamCycle:
    decision = authorize_or_deny(actor, "academic.exam.manage", None, request=request)
    if semester.term not in RuleContext()("exam.cycles").value:
        raise ValidationError(f"{semester.term} is not a configured examination cycle")
    cycle = ExamCycle(semester=semester, name=name, registration_opens=registration_opens,
                      registration_closes=registration_closes, exam_start=exam_start, exam_end=exam_end)
    cycle.full_clean()
    try:
        with transaction.atomic():
            cycle.save()
            audit_ok(actor, "academic.exam.cycle.create", cycle, decision, request=request,
                     after={"semester_id": semester.pk, "exam_start": str(exam_start), "exam_end": str(exam_end)})
    except IntegrityError:
        raise ValidationError("An examination cycle already exists for this semester")
    return cycle


def create_exam(actor, *, cycle, course, scheduled_on, start_time, end_time, venue="", request=None) -> Exam:
    decision = authorize_or_deny(actor, "academic.exam.manage", course, request=request)
    if course.category == Course.Category.ACTIVITY:
        raise ValidationError("Activity components are evaluated by SDRC, not by a written examination")
    if not (cycle.exam_start <= scheduled_on <= cycle.exam_end):
        raise ValidationError("The exam date must fall within the cycle's examination period")
    exam = Exam(cycle=cycle, course=course, scheduled_on=scheduled_on, start_time=start_time, end_time=end_time,
                venue=venue)
    exam.full_clean()
    try:
        with transaction.atomic():
            exam.save()
            audit_ok(actor, "academic.exam.create", exam, decision, request=request,
                     after={"cycle_id": cycle.pk, "course": course.code, "date": str(scheduled_on)})
    except IntegrityError:
        raise ValidationError("This course already has an exam in this cycle")
    return exam


# --- Attempt history --------------------------------------------------------------------

def attempt_history(scholar, course) -> dict:
    attempts = list(CourseAttempt.objects.filter(scholar=scholar, course=course)
                    .exclude(status=CourseAttempt.Status.CANCELLED).order_by("attempt_no"))
    results = {r.attempt_id: r for r in CourseResult.objects.filter(attempt__in=attempts, is_current=True)}
    ratified = [(a, results.get(a.pk)) for a in attempts
                if a.pk in results and results[a.pk].status == CourseResult.Status.RATIFIED]
    return {
        "attempts": attempts,
        "next_no": len(attempts) + 1,
        "pending": [a for a in attempts if a.status == CourseAttempt.Status.SCHEDULED],
        "passed": any(r.outcome == CourseResult.Outcome.PASS for _, r in ratified),
        "failed": [a for a, r in ratified if r.outcome == CourseResult.Outcome.FAIL],
        "absent": [a for a, r in ratified if r.outcome == CourseResult.Outcome.ABSENT],
    }


def counted_failures(history, ctx) -> tuple[int, list[str]]:
    """Failed attempts counted towards the limit. Absent attempts count only if
    exam.absent_counts_as_attempt says so; while UNRESOLVED they block."""
    blockers = []
    count = len(history["failed"])
    if history["absent"]:
        rule = ctx("exam.absent_counts_as_attempt")
        if rule.value is None:
            blockers.append("Treatment of an absent attempt is unresolved (exam.absent_counts_as_attempt); "
                            "requires institutional decision")
        elif rule.value:
            count += len(history["absent"])
    return count, blockers


def attempt_window_end(scholar, course, history, ctx) -> date | None:
    start_rule = ctx("exam.attempt_window_start").value
    years = ctx("exam.attempt_window_years").value
    if start_rule == "ENROLMENT":
        start = scholar.registration_date or scholar.admission_date
    elif start_rule == "FIRST_ATTEMPT":
        start = history["attempts"][0].exam_date if history["attempts"] else None
    else:
        raise ValidationError(f"Unknown exam.attempt_window_start {start_rule!r}")
    return add_years(start, years) if start else None


def approved_case(enrollment):
    return ThirdAttemptCase.objects.filter(enrollment=enrollment, status=ThirdAttemptCase.Status.VC_APPROVED).first()


# --- Eligibility ----------------------------------------------------------------------

def evaluate_eligibility(enrollment, exam, *, on: date | None = None, evaluated_by=None,
                         persist: bool = True) -> ExamEligibility:
    """Server-side eligibility. Persisted for audit and appeals; `persist=False`
    returns an unsaved preview (read-only views must not create records)."""
    on = on or timezone.localdate()
    ctx = RuleContext(on)
    scholar = enrollment.scholar
    course = enrollment.offering.course
    reasons, warnings = [], []

    if scholar.status != ScholarStatus.ACTIVE:
        reasons.append("Scholar is not active")
    if enrollment.status != ScholarCourseEnrollment.Status.ENROLLED:
        reasons.append(f"Enrollment status is {enrollment.status}")
    if exam.course_id != course.pk:
        reasons.append("The exam is for a different course")
    cycle = exam.cycle
    if not (cycle.registration_opens <= on <= cycle.registration_closes):
        reasons.append(f"Registration window is {cycle.registration_opens} to {cycle.registration_closes}")

    history = attempt_history(scholar, course)
    attempt_no = history["next_no"]
    if history["passed"]:
        reasons.append(f"{course.code} has already been passed")
    if history["pending"]:
        reasons.append("A previous attempt has no ratified result yet")
    failures, blockers = counted_failures(history, ctx)
    reasons.extend(blockers)

    max_regular = ctx("exam.max_regular_attempts").value
    max_total = ctx("exam.max_attempts_with_approval").value
    window_end = attempt_window_end(scholar, course, history, ctx)
    outside_window = window_end is not None and exam.scheduled_on > window_end
    needs_case = failures >= max_regular or outside_window
    case = approved_case(enrollment) if needs_case else None
    if attempt_no > max_total:
        reasons.append(f"Maximum of {max_total} attempts reached")
    elif needs_case and case is None:
        why = (f"{failures} counted attempt(s) used of {max_regular}" if failures >= max_regular
               else f"outside the attempt window ending {window_end}")
        reasons.append(f"Further attempt requires an approved third-attempt case ({why})")

    if course.category != Course.Category.ACTIVITY:
        reasons.extend(_attendance_reasons(enrollment, scholar, ctx, warnings))
    exam_semester = cycle.semester
    if ctx("exam.requires_fee_clearance").value and not FeeClearance.objects.filter(
            scholar=scholar, semester=exam_semester, cleared=True).exists():
        reasons.append(f"Fee / dues clearance not recorded for {exam_semester}")
    if ctx("semester_registration.required").value and not SemesterRegistration.objects.filter(
            scholar=scholar, semester=exam_semester, status=SemesterRegistration.Status.REGISTERED).exists():
        reasons.append(f"Scholar is not registered for {exam_semester}")

    eligibility = ExamEligibility(
        exam=exam, enrollment=enrollment, eligible=not reasons, attempt_no=attempt_no if not reasons else None,
        reasons=reasons, warnings=warnings, rule_versions=ctx.versions, evaluated_by=evaluated_by)
    if persist:
        eligibility.save()
    return eligibility


def _attendance_reasons(enrollment, scholar, ctx, warnings) -> list[str]:
    basis = ctx("attendance.basis").value
    if basis != "PER_COURSE":
        return [f"Attendance basis {basis!r} is not implemented (attendance.basis)"]
    threshold = ctx("attendance.min_percent").value.get(scholar.mode)
    if threshold is None:
        return [f"The attendance requirement for {scholar.mode} scholars is unresolved "
                "(attendance.min_percent); requires institutional decision"]
    summary = attendance_summary(enrollment, ctx)
    if summary["sessions_held"] == 0:
        warnings.append("No attendance sessions recorded for this enrollment")
        return []
    if summary["percent"] < threshold:
        return [f"Physical attendance {summary['percent']}% below the required {threshold}%"]
    return []


def register_for_exam(actor, enrollment, exam, *, request=None) -> ExamRegistration:
    decision = authorize_or_deny(actor, ["academic.exam.register.self", "academic.exam.register.manage"],
                                 enrollment, request=request)
    eligibility = evaluate_eligibility(enrollment, exam, evaluated_by=actor)
    if not eligibility.eligible:
        deny(actor, "academic.exam.register", "Not eligible: " + "; ".join(eligibility.reasons), enrollment,
             request=request)
    # Eligibility passed, so if this attempt needs a case, an approved one exists.
    use_case = approved_case(enrollment) if _needs_case(enrollment, exam) else None
    try:
        with transaction.atomic():
            attempt = CourseAttempt.objects.create(
                scholar=enrollment.scholar, course=enrollment.offering.course, enrollment=enrollment,
                attempt_no=eligibility.attempt_no, status=CourseAttempt.Status.SCHEDULED, exam_date=exam.scheduled_on)
            registration = ExamRegistration.objects.create(
                exam=exam, enrollment=enrollment, attempt=attempt, attempt_no=eligibility.attempt_no,
                eligibility=eligibility, third_attempt_case=use_case, registered_by=actor)
            if use_case is not None:
                use_case.status = ThirdAttemptCase.Status.CONSUMED
                use_case.save(update_fields=["status"])
                ThirdAttemptCaseEvent.objects.create(case=use_case, action="CONSUMED", actor=actor,
                                                     capability=decision.capability,
                                                     remarks=f"Registration #{registration.pk}")
            audit_ok(actor, "academic.exam.register", registration, decision, request=request,
                     after={"exam_id": exam.pk, "attempt_no": eligibility.attempt_no,
                            "warnings": eligibility.warnings, "rule_versions": eligibility.rule_versions})
    except IntegrityError:
        raise ValidationError("Duplicate or invalid registration")
    return registration


def _needs_case(enrollment, exam) -> bool:
    ctx = RuleContext()
    history = attempt_history(enrollment.scholar, enrollment.offering.course)
    failures, _ = counted_failures(history, ctx)
    window_end = attempt_window_end(enrollment.scholar, enrollment.offering.course, history, ctx)
    return failures >= ctx("exam.max_regular_attempts").value or (
        window_end is not None and exam.scheduled_on > window_end)


def open_activity_evaluation(actor, enrollment, *, request=None) -> CourseAttempt:
    """SDRC opens an evaluation attempt for a mandatory activity component."""
    course = enrollment.offering.course
    decision = authorize_or_deny(actor, "academic.result.prepare.activity", enrollment, request=request)
    if course.category != Course.Category.ACTIVITY:
        raise ValidationError("Only activity components are evaluated this way")
    if enrollment.status != ScholarCourseEnrollment.Status.ENROLLED:
        raise ValidationError(f"Enrollment status is {enrollment.status}")
    ctx = RuleContext()
    history = attempt_history(enrollment.scholar, course)
    failures, blockers = counted_failures(history, ctx)
    if history["passed"] or history["pending"] or blockers:
        raise ValidationError(["Activity already passed or under evaluation"] + blockers)
    if failures >= ctx("exam.max_regular_attempts").value and approved_case(enrollment) is None:
        raise ValidationError("Further evaluation requires an approved third-attempt case")
    try:
        with transaction.atomic():
            attempt = CourseAttempt.objects.create(
                scholar=enrollment.scholar, course=course, enrollment=enrollment, attempt_no=history["next_no"],
                status=CourseAttempt.Status.SCHEDULED, exam_date=timezone.localdate())
            audit_ok(actor, "academic.activity.evaluation.open", attempt, decision, request=request)
    except IntegrityError:
        raise ValidationError("Invalid attempt number")
    return attempt
