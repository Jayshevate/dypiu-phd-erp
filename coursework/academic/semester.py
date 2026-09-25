"""Semester registration and fee / dues clearance."""
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from coursework.models import FeeClearance, SemesterRegistration
from scholars.models import Status as ScholarStatus

from .common import audit_ok, authorize_or_deny
from .config import RuleContext


def record_fee_clearance(actor, scholar, semester, *, cleared: bool, reference: str, request=None) -> FeeClearance:
    decision = authorize_or_deny(actor, "academic.fee_clearance.record", scholar, request=request)
    if not reference.strip():
        raise ValidationError("A receipt / accounts reference is required")
    with transaction.atomic():
        existing = FeeClearance.objects.select_for_update().filter(scholar=scholar, semester=semester).first()
        before = {"cleared": existing.cleared, "reference": existing.reference} if existing else None
        row, _ = FeeClearance.objects.update_or_create(scholar=scholar, semester=semester, defaults=dict(
            cleared=cleared, reference=reference, recorded_by=actor))
        audit_ok(actor, "academic.fee_clearance.record", row, decision, request=request, before=before,
                 after={"cleared": cleared, "reference": reference, "semester_id": semester.pk})
    return row


def register_semester(actor, scholar, semester, *, on=None, request=None) -> SemesterRegistration:
    decision = authorize_or_deny(actor, ["academic.semester.register.self", "academic.semester.register.manage"],
                                 scholar, request=request)
    on = on or timezone.localdate()
    ctx = RuleContext(on)
    problems = []
    if scholar.status != ScholarStatus.ACTIVE:
        problems.append("Scholar is not active")
    if semester.registration_opens is None:
        problems.append(f"No registration window is configured for {semester}")
    elif not (semester.registration_opens <= on <= semester.registration_closes):
        problems.append(f"Registration window is {semester.registration_opens} to {semester.registration_closes}")
    if on > semester.end_date:
        problems.append(f"{semester} has ended")
    clearance = FeeClearance.objects.filter(scholar=scholar, semester=semester, cleared=True).first()
    if ctx("semester_registration.requires_fee_clearance").value and clearance is None:
        problems.append(f"Fee / dues clearance not recorded for {semester}")
    if problems:
        raise ValidationError(problems)
    try:
        with transaction.atomic():
            reg = SemesterRegistration.objects.create(scholar=scholar, semester=semester, fee_clearance=clearance,
                                                      registered_by=actor)
            audit_ok(actor, "academic.semester.register", reg, decision, request=request,
                     after={"semester_id": semester.pk, "rule_versions": ctx.versions})
    except IntegrityError:
        raise ValidationError(f"Scholar is already registered for {semester}")
    return reg
