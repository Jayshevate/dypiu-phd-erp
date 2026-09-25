from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from coursework.models import Assessment, CourseAttempt, CourseResult, MarkEntry, MarkEntryHistory

from .common import audit_ok, authorize_or_deny, deny

ACTION_BY_KIND = {
    Assessment.Kind.CONTINUOUS: "academic.marks.enter.continuous",
    Assessment.Kind.END_TERM: "academic.marks.enter.end_term",
    Assessment.Kind.MANDATORY_MODULE: "academic.marks.enter.end_term",
    Assessment.Kind.ACTIVITY: "academic.marks.enter.activity",
}


def marks_locked(attempt) -> bool:
    result = CourseResult.objects.filter(attempt=attempt, is_current=True).first()
    return result is not None and result.status != CourseResult.Status.RETURNED


def enter_mark(actor, assessment: Assessment, attempt: CourseAttempt, *, marks=None, absent=False, cleared=None,
               reason="", request=None) -> MarkEntry:
    action = ACTION_BY_KIND[assessment.kind]
    decision = authorize_or_deny(actor, action, attempt, request=request)
    if attempt.enrollment_id is None or attempt.enrollment.offering_id != assessment.offering_id:
        deny(actor, action, "Assessment does not belong to this attempt's course offering", attempt,
             request=request)
    if attempt.status != CourseAttempt.Status.SCHEDULED:
        raise ValidationError("Marks can only be entered for an attempt under evaluation")
    if marks_locked(attempt):
        deny(actor, action, "Marks are locked: the result has been prepared, verified or ratified", attempt,
             request=request)

    if assessment.kind == Assessment.Kind.MANDATORY_MODULE:
        if cleared is None or marks is not None or absent:
            raise ValidationError("A mandatory module is recorded only as cleared / not cleared")
    elif absent:
        marks, cleared = None, None
    else:
        if marks is None:
            raise ValidationError("Marks are required (or mark the scholar absent)")
        marks = Decimal(str(marks))
        if marks < 0 or marks > assessment.max_marks:
            raise ValidationError(f"Marks must be between 0 and {assessment.max_marks}")
        cleared = None

    existing = MarkEntry.objects.filter(assessment=assessment, attempt=attempt).first()
    with transaction.atomic():
        if existing is None:
            entry = MarkEntry(assessment=assessment, attempt=attempt, marks=marks, is_absent=absent,
                              cleared=cleared, entered_by=actor)
            entry.full_clean()
            entry.save()
            audit_ok(actor, action, entry, decision, request=request,
                     after={"marks": str(marks), "absent": absent, "cleared": cleared})
            return entry
        if not reason.strip():
            raise ValidationError("A reason is required to change entered marks")
        MarkEntryHistory.objects.create(mark=existing, old_marks=existing.marks, old_is_absent=existing.is_absent,
                                        old_cleared=existing.cleared, new_marks=marks, new_is_absent=absent,
                                        new_cleared=cleared, reason=reason, changed_by=actor)
        before = {"marks": str(existing.marks), "absent": existing.is_absent, "cleared": existing.cleared}
        existing.marks, existing.is_absent, existing.cleared = marks, absent, cleared
        existing.full_clean()
        existing.save()
        audit_ok(actor, action + ".correct", existing, decision, request=request, before=before,
                 after={"marks": str(marks), "absent": absent, "cleared": cleared}, reasons=[reason])
        return existing
