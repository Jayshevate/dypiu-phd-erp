from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from coursework.models import Course, CourseOffering, ElectiveProposal, ScholarCourseEnrollment, SemesterRegistration
from scholars.models import Status as ScholarStatus

from .common import audit_ok, authorize_or_deny, coursework_category
from .config import RuleContext


def enroll(actor, scholar, offering: CourseOffering, *, section=None, request=None) -> ScholarCourseEnrollment:
    decision = authorize_or_deny(actor, ["academic.enrollment.self", "academic.enrollment.manage"], scholar,
                                 request=request)
    ctx = RuleContext()
    problems = []
    course = offering.course
    if scholar.status != ScholarStatus.ACTIVE:
        problems.append("Scholar is not active")
    if offering.status != CourseOffering.Status.OPEN:
        problems.append("Offering is not open for enrollment")
    if ctx("semester_registration.required").value and not SemesterRegistration.objects.filter(
            scholar=scholar, semester=offering.semester, status=SemesterRegistration.Status.REGISTERED).exists():
        problems.append(f"Scholar is not registered for {offering.semester}")
    if section is not None and section.offering_id != offering.pk:
        problems.append("Section does not belong to this offering")
    if not course.applies_to(coursework_category(scholar, ctx)):
        problems.append(f"{course.code} is not applicable to the scholar's coursework category")
    if offering.capacity is not None and offering.enrollments.filter(
            status=ScholarCourseEnrollment.Status.ENROLLED).count() >= offering.capacity:
        problems.append("Offering is full")
    if section is not None and section.capacity is not None and section.enrollments.filter(
            status=ScholarCourseEnrollment.Status.ENROLLED).count() >= section.capacity:
        problems.append("Section is full")
    if ScholarCourseEnrollment.objects.filter(scholar=scholar, offering__course=course).exclude(
            status=ScholarCourseEnrollment.Status.WITHDRAWN).exists():
        problems.append(f"Scholar already holds an enrollment in {course.code}")
    proposal = None
    if course.category == Course.Category.ELECTIVE:
        proposal = ElectiveProposal.objects.filter(scholar=scholar, course=course,
                                                   status=ElectiveProposal.Status.APPROVED).first()
        if proposal is None:
            problems.append("Elective enrollment requires an approved elective proposal")
    if problems:
        raise ValidationError(problems)
    enrollment = ScholarCourseEnrollment(scholar=scholar, offering=offering, section=section,
                                         elective_proposal=proposal, enrolled_by=actor)
    try:
        with transaction.atomic():
            enrollment.save()
            audit_ok(actor, "academic.enrollment.create", enrollment, decision, request=request,
                     after={"offering_id": offering.pk, "section_id": getattr(section, "pk", None)})
    except IntegrityError:
        raise ValidationError("Duplicate enrollment")
    return enrollment


def withdraw(actor, enrollment: ScholarCourseEnrollment, *, reason, request=None):
    decision = authorize_or_deny(actor, "academic.enrollment.manage", enrollment, request=request)
    if enrollment.attempts.exists():
        raise ValidationError("An enrollment with examination attempts cannot be withdrawn")
    with transaction.atomic():
        enrollment.status = ScholarCourseEnrollment.Status.WITHDRAWN
        enrollment.save(update_fields=["status"])
        audit_ok(actor, "academic.enrollment.withdraw", enrollment, decision, request=request, reasons=[reason])
    return enrollment
