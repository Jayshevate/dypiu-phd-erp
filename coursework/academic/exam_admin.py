"""Hall tickets and question-paper setter appointments."""
import secrets

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from coursework.models import ExamRegistration, HallTicket, QuestionPaperSetterAppointment
from identity.authz import Policy, Rule

from .common import audit_ok, authorize_or_deny, authorize_policy_or_deny
from .config import RuleContext


def issue_hall_ticket(actor, registration: ExamRegistration, *, request=None) -> HallTicket:
    decision = authorize_or_deny(actor, "academic.hall_ticket.issue", registration, request=request)
    if registration.status != ExamRegistration.Status.REGISTERED or not registration.eligibility.eligible:
        raise ValidationError("A hall ticket is issued only for an active, eligible registration")
    number = f"HT-{registration.exam.cycle_id:04d}-{registration.pk:06d}"
    try:
        with transaction.atomic():
            ticket = HallTicket.objects.create(
                registration=registration, number=number if not HallTicket.objects.filter(number=number).exists()
                else f"{number}-{secrets.token_hex(2)}", verification_code=secrets.token_hex(8), issued_by=actor)
            audit_ok(actor, "academic.hall_ticket.issue", ticket, decision, request=request,
                     after={"number": ticket.number})
    except IntegrityError:
        raise ValidationError("A live hall ticket already exists for this registration")
    return ticket


def revoke_hall_ticket(actor, ticket: HallTicket, *, reason: str, request=None) -> HallTicket:
    decision = authorize_or_deny(actor, "academic.hall_ticket.issue", ticket, request=request)
    if ticket.status != HallTicket.Status.ISSUED:
        raise ValidationError("Hall ticket is not live")
    if not reason.strip():
        raise ValidationError("A reason is required")
    with transaction.atomic():
        ticket.status, ticket.revoked_by, ticket.revoked_at = HallTicket.Status.REVOKED, actor, timezone.now()
        ticket.revocation_reason = reason
        ticket.save(update_fields=["status", "revoked_by", "revoked_at", "revocation_reason"])
        audit_ok(actor, "academic.hall_ticket.revoke", ticket, decision, request=request, reasons=[reason])
    return ticket


def hall_ticket_document(actor, ticket: HallTicket, *, request=None) -> dict:
    """Data for an official hall ticket, from authoritative records. No
    signature or seal is generated here."""
    authorize_or_deny(actor, "academic.hall_ticket.view", ticket, request=request)
    reg = ticket.registration
    scholar, exam = reg.enrollment.scholar, reg.exam
    return {
        "hall_ticket_number": ticket.number, "verification_code": ticket.verification_code,
        "status": ticket.status, "issued_at": ticket.issued_at,
        "scholar": {"prn": scholar.prn, "name": scholar.name, "department": str(scholar.department)},
        "exam": {"cycle": exam.cycle.name, "course_code": exam.course.code, "course_title": exam.course.title,
                 "date": exam.scheduled_on, "start_time": exam.start_time, "end_time": exam.end_time,
                 "venue": exam.venue},
        "attempt_no": reg.attempt_no,
        "note": "Official numbering format, signatures and seals are not defined in the ERP",
    }


def appoint_question_paper_setter(actor, exam, setter, *, basis: str, request=None) -> QuestionPaperSetterAppointment:
    authority = RuleContext()("question_paper.setter_appointed_by")
    if authority.unresolved:
        raise ValidationError("The appointing authority is unresolved (question_paper.setter_appointed_by)")
    decision = authorize_policy_or_deny(actor, "academic.question_paper.appoint",
                                        Policy(rules=tuple(Rule(c) for c in authority.value), privileged=True),
                                        exam, request=request)
    if not basis.strip():
        raise ValidationError("A basis (DC decision reference) is required")
    try:
        with transaction.atomic():
            appt = QuestionPaperSetterAppointment.objects.create(exam=exam, setter=setter, basis=basis,
                                                                 appointed_by=actor, appointed_as=decision.capability)
            audit_ok(actor, "academic.question_paper.appoint", appt, decision, request=request,
                     after={"exam_id": exam.pk, "setter_id": setter.pk, "basis": basis})
    except IntegrityError:
        raise ValidationError("This setter is already appointed for this exam")
    return appt


def record_question_paper_received(actor, appointment: QuestionPaperSetterAppointment, *,
                                   request=None) -> QuestionPaperSetterAppointment:
    decision = authorize_or_deny(actor, "academic.question_paper.receive", appointment, request=request)
    if appointment.status != QuestionPaperSetterAppointment.Status.APPOINTED:
        raise ValidationError(f"Appointment is {appointment.status}")
    with transaction.atomic():
        appointment.status = QuestionPaperSetterAppointment.Status.PAPER_RECEIVED
        appointment.received_by, appointment.received_at = actor, timezone.now()
        appointment.save(update_fields=["status", "received_by", "received_at"])
        audit_ok(actor, "academic.question_paper.receive", appointment, decision, request=request)
    return appointment
