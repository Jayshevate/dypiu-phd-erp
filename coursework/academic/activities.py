"""Mandatory activity components: scholar submits the required artefacts
(activity.required_artefacts); SDRC reviews them and evaluates the activity."""
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from coursework.models import ActivitySubmission, Course

from .common import audit_ok, authorize_or_deny
from .config import RuleContext


def submit_artefact(actor, enrollment, *, artefact: str, document=None, description="",
                    request=None) -> ActivitySubmission:
    decision = authorize_or_deny(actor, "academic.activity.submit", enrollment, request=request)
    course = enrollment.offering.course
    if course.category != Course.Category.ACTIVITY:
        raise ValidationError("Artefacts are submitted only for activity components")
    ctx = RuleContext()
    required = ctx("activity.required_artefacts").value.get(course.activity_type, [])
    if artefact not in required:
        raise ValidationError(f"{artefact} is not a required artefact for {course.get_activity_type_display()}")
    if ctx("activity.artefacts_in_order").value.get(course.activity_type):
        earlier = required[:required.index(artefact)]
        live = set(ActivitySubmission.objects.filter(enrollment=enrollment).exclude(
            status=ActivitySubmission.Status.RETURNED).values_list("artefact", flat=True))
        missing = [a for a in earlier if a not in live]
        if missing:
            raise ValidationError(f"Submit {missing} first (documented order: {required})")
    try:
        with transaction.atomic():
            sub = ActivitySubmission.objects.create(enrollment=enrollment, artefact=artefact,
                                                    document=document or "", description=description,
                                                    submitted_by=actor)
            audit_ok(actor, "academic.activity.submit", sub, decision, request=request, after={"artefact": artefact})
    except IntegrityError:
        raise ValidationError(f"{artefact} already submitted and not returned")
    return sub


def review_artefact(actor, submission: ActivitySubmission, *, accept: bool, remarks="", request=None):
    decision = authorize_or_deny(actor, "academic.activity.review", submission, request=request)
    if submission.status != ActivitySubmission.Status.SUBMITTED:
        raise ValidationError(f"Submission is {submission.status}")
    if not accept and not remarks.strip():
        raise ValidationError("Remarks are required when returning an artefact")
    with transaction.atomic():
        submission.status = ActivitySubmission.Status.ACCEPTED if accept else ActivitySubmission.Status.RETURNED
        submission.reviewed_by, submission.reviewed_at, submission.review_remarks = actor, timezone.now(), remarks
        submission.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_remarks"])
        audit_ok(actor, "academic.activity.review", submission, decision, request=request,
                 after={"status": submission.status})
    return submission
