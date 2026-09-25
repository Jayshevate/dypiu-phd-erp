from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from coursework.models import (AttendanceCorrection, AttendanceRecord, AttendanceSession, OrientationAttendance,
                               ScholarCourseEnrollment)

from .common import audit_ok, authorize_or_deny, deny
from .config import RuleContext


def create_session(actor, section, *, date, start_time, end_time, topic="", request=None) -> AttendanceSession:
    decision = authorize_or_deny(actor, "academic.attendance.record", section, request=request)
    session = AttendanceSession(section=section, faculty=actor.faculty_profile, date=date, start_time=start_time,
                                end_time=end_time, topic=topic, created_by=actor)
    session.full_clean()
    try:
        with transaction.atomic():
            session.save()
            audit_ok(actor, "academic.attendance.session.create", session, decision, request=request,
                     after={"section_id": section.pk, "date": str(date), "start": str(start_time)})
    except IntegrityError:
        raise ValidationError("A session already exists for this section at this date and time")
    return session


def record_attendance(actor, session, enrollment, *, status, mode="", reason="", request=None) -> AttendanceRecord:
    """Create or correct one scholar's attendance. Corrections require a reason
    and are kept in AttendanceCorrection."""
    decision = authorize_or_deny(actor, "academic.attendance.record", session, request=request)
    if enrollment.status == ScholarCourseEnrollment.Status.WITHDRAWN:
        raise ValidationError("Scholar has withdrawn from this course")
    if enrollment.offering_id != session.section.offering_id or (
            enrollment.section_id is not None and enrollment.section_id != session.section_id):
        deny(actor, "academic.attendance.record", "Scholar is not enrolled in this section", enrollment,
             request=request)
    if status == AttendanceRecord.Status.ABSENT:
        mode = ""
    elif mode not in AttendanceRecord.Mode.values:
        raise ValidationError("Presence must state the mode (PHYSICAL or ONLINE)")
    existing = AttendanceRecord.objects.filter(session=session, enrollment=enrollment).first()
    with transaction.atomic():
        if existing is None:
            record = AttendanceRecord(session=session, enrollment=enrollment, status=status, mode=mode,
                                      recorded_by=actor)
            record.full_clean()
            record.save()
            audit_ok(actor, "academic.attendance.record", record, decision, request=request,
                     after={"status": status, "mode": mode})
            return record
        if (existing.status, existing.mode) == (status, mode):
            return existing
        if not reason.strip():
            raise ValidationError("A reason is required to correct attendance")
        AttendanceCorrection.objects.create(record=existing, old_status=existing.status, old_mode=existing.mode,
                                            new_status=status, new_mode=mode, reason=reason, corrected_by=actor)
        before = {"status": existing.status, "mode": existing.mode}
        existing.status, existing.mode = status, mode
        existing.save(update_fields=["status", "mode", "updated_at"])
        audit_ok(actor, "academic.attendance.correct", existing, decision, request=request, before=before,
                 after={"status": status, "mode": mode}, reasons=[reason])
        return existing


def attendance_summary(enrollment, ctx: RuleContext | None = None) -> dict:
    """Server-side attendance: sessions held for the enrollment's section vs
    sessions attended (physical only, if attendance.physical_only)."""
    ctx = ctx or RuleContext()
    physical_only = ctx("attendance.physical_only").value
    sessions = AttendanceSession.objects.filter(section__offering=enrollment.offering)
    if enrollment.section_id:
        sessions = sessions.filter(section_id=enrollment.section_id)
    held = sessions.count()
    present = AttendanceRecord.objects.filter(enrollment=enrollment, session__in=sessions,
                                              status=AttendanceRecord.Status.PRESENT)
    if physical_only:
        present = present.filter(mode=AttendanceRecord.Mode.PHYSICAL)
    attended = present.count()
    percent = (Decimal(attended * 100) / held).quantize(Decimal("0.01"), ROUND_HALF_UP) if held else None
    return {"sessions_held": held, "sessions_attended": attended, "percent": percent,
            "physical_only": physical_only}


def record_orientation(actor, seminar, scholar, *, attended: bool, request=None) -> OrientationAttendance:
    decision = authorize_or_deny(actor, "academic.orientation.record", scholar, request=request)
    try:
        with transaction.atomic():
            row = OrientationAttendance.objects.create(seminar=seminar, scholar=scholar, attended=attended,
                                                       recorded_by=actor)
            audit_ok(actor, "academic.orientation.record", row, decision, request=request,
                     after={"seminar_id": seminar.pk, "attended": attended})
    except IntegrityError:
        raise ValidationError("Orientation attendance already recorded for this scholar")
    return row
