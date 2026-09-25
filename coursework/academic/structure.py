"""Academic structure: years, semesters, courses, offerings, sections,
faculty assignments and assessment schemes."""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from coursework.models import (AcademicYear, Assessment, Course, CourseOffering, FacultySubjectAssignment, MarkEntry,
                               Section, Semester)
from identity.authz import Policy, Rule

from .common import audit_ok, authorize_or_deny, authorize_policy_or_deny
from .config import RuleContext


def _create(model, actor, action, resource_for_auth, fields, request=None):
    decision = authorize_or_deny(actor, action, resource_for_auth, request=request)
    obj = model(**fields)
    obj.full_clean()
    try:
        with transaction.atomic():
            obj.save()
            audit_ok(actor, action + ".create", obj, decision, request=request,
                     after={k: str(v) for k, v in fields.items()})
    except IntegrityError as e:
        raise ValidationError(str(e))
    return obj


def create_academic_year(actor, *, code, start_date, end_date, request=None) -> AcademicYear:
    return _create(AcademicYear, actor, "academic.structure.manage", None,
                   dict(code=code, start_date=start_date, end_date=end_date), request)


def create_semester(actor, *, academic_year, term, name, start_date, end_date,
                    elective_registration_deadline=None, registration_opens=None, registration_closes=None,
                    request=None) -> Semester:
    if not (academic_year.start_date <= start_date and end_date <= academic_year.end_date):
        raise ValidationError("Semester dates must fall within the academic year")
    return _create(Semester, actor, "academic.structure.manage", None,
                   dict(academic_year=academic_year, term=term, name=name, start_date=start_date,
                        end_date=end_date, elective_registration_deadline=elective_registration_deadline,
                        registration_opens=registration_opens, registration_closes=registration_closes), request)


def create_course(actor, *, code, title, credits, category, activity_type="", applicable_categories=(),
                  department=None, has_ethics_submodule=False, required_for_all=None, request=None) -> Course:
    evaluation_body = Course.EvaluationBody.SDRC if category == Course.Category.ACTIVITY \
        else Course.EvaluationBody.COURSE_COORDINATOR
    if required_for_all is None:
        required_for_all = category != Course.Category.ELECTIVE
    return _create(Course, actor, "academic.structure.manage", department,
                   dict(code=code, title=title, credits=credits, category=category, activity_type=activity_type,
                        evaluation_body=evaluation_body, applicable_categories=list(applicable_categories),
                        department=department, has_ethics_submodule=has_ethics_submodule,
                        required_for_all=required_for_all), request)


def create_offering(actor, *, course, semester, capacity=None, status=CourseOffering.Status.OPEN,
                    request=None) -> CourseOffering:
    if not course.is_active:
        raise ValidationError(f"{course.code} is not active")
    return _create(CourseOffering, actor, "academic.structure.manage", course,
                   dict(course=course, semester=semester, capacity=capacity, status=status, created_by=actor),
                   request)


def create_section(actor, *, offering, code, capacity=None, request=None) -> Section:
    return _create(Section, actor, "academic.structure.manage", offering,
                   dict(offering=offering, code=code, capacity=capacity), request)


def assign_faculty(actor, *, faculty, offering, role, basis, section=None, valid_from=None, valid_to=None,
                   request=None) -> FacultySubjectAssignment:
    decision = authorize_or_deny(actor, "academic.faculty_assignment.manage", offering, request=request)
    if faculty.is_external:
        raise ValidationError("External faculty cannot hold a teaching assignment")
    if section is not None and section.offering_id != offering.pk:
        raise ValidationError("Section does not belong to this offering")
    if offering.course.category == Course.Category.ACTIVITY:
        raise ValidationError("Activity components are evaluated by SDRC; they have no teaching assignment")
    assignment = FacultySubjectAssignment(
        faculty=faculty, offering=offering, section=section, role=role, basis=basis,
        valid_from=valid_from or timezone.localdate(), valid_to=valid_to, assigned_by=actor)
    assignment.full_clean()
    try:
        with transaction.atomic():
            assignment.save()
            audit_ok(actor, "academic.faculty_assignment.create", assignment, decision, request=request,
                     after={"faculty_id": faculty.pk, "offering_id": offering.pk,
                            "section_id": getattr(section, "pk", None), "role": role, "basis": basis})
    except IntegrityError:
        raise ValidationError("Duplicate active assignment (or the offering already has a Course Coordinator)")
    return assignment


def revoke_assignment(actor, assignment: FacultySubjectAssignment, *, reason, request=None):
    decision = authorize_or_deny(actor, "academic.faculty_assignment.manage", assignment.offering, request=request)
    if assignment.revoked_at is not None:
        raise ValidationError("Assignment already revoked")
    with transaction.atomic():
        assignment.revoked_at = timezone.now()
        assignment.revoked_by = actor
        assignment.save(update_fields=["revoked_at", "revoked_by"])
        audit_ok(actor, "academic.faculty_assignment.revoke", assignment, decision, request=request,
                 reasons=[reason])
    return assignment


# --- Assessment schemes -------------------------------------------------------------

def define_default_assessments(actor, offering, *, ethics_module_name="Ethics / plagiarism module",
                               request=None) -> list[Assessment]:
    """Taught courses and electives: Continuous Assessment + End-term with the
    configured default weights (assessment.default_weights). Research
    Methodology & Ethics also gets its mandatory module (cleared / not cleared).
    Activity components have no default scheme: SDRC defines it explicitly."""
    decision = authorize_or_deny(actor, "academic.structure.manage", offering, request=request)
    course = offering.course
    if course.category == Course.Category.ACTIVITY:
        raise ValidationError("Activity evaluation schemes are not defined by the source; "
                              "SDRC must define them explicitly (define_activity_assessments)")
    if offering.assessments.exists():
        raise ValidationError("Offering already has an assessment scheme")
    ctx = RuleContext()
    weights = ctx("assessment.default_weights").value
    rows = [Assessment(offering=offering, name="Continuous Assessment", kind=Assessment.Kind.CONTINUOUS,
                       weight=Decimal(str(weights["CONTINUOUS"])), max_marks=Decimal("100"), sequence=1),
            Assessment(offering=offering, name="End-term evaluation", kind=Assessment.Kind.END_TERM,
                       weight=Decimal(str(weights["END_TERM"])), max_marks=Decimal("100"), sequence=2)]
    if course.has_ethics_submodule and ctx("coursework.ethics_module_required").value:
        rows.append(Assessment(offering=offering, name=ethics_module_name, kind=Assessment.Kind.MANDATORY_MODULE,
                               weight=Decimal("0"), max_marks=None, sequence=3))
    _validate_scheme(rows)
    with transaction.atomic():
        for r in rows:
            r.full_clean()
            r.save()
        audit_ok(actor, "academic.assessment_scheme.define", offering, decision, request=request,
                 after={"weights": {r.name: str(r.weight) for r in rows}, "rule_versions": ctx.versions})
    return rows


def define_activity_assessments(actor, offering, request=None) -> list[Assessment]:
    """Apply the approved activity scoring scheme (activity.scoring_scheme.<TYPE>):
    a list of [name, weight, max_marks] totalling 100. While the scheme is
    UNRESOLVED, no activity can be scored; nothing is invented here."""
    decision = authorize_or_deny(actor, "academic.activity.scheme.define", offering, request=request)
    course = offering.course
    if course.category != Course.Category.ACTIVITY:
        raise ValidationError("Only activity components use an activity scoring scheme")
    if offering.assessments.exists():
        raise ValidationError("Offering already has an assessment scheme")
    ctx = RuleContext()
    scheme = ctx(f"activity.scoring_scheme.{course.activity_type}")
    if scheme.unresolved:
        raise ValidationError(f"The scoring scheme for {course.get_activity_type_display()} is unresolved "
                              f"(activity.scoring_scheme.{course.activity_type}); requires institutional decision")
    rows = [Assessment(offering=offering, name=n, kind=Assessment.Kind.ACTIVITY, weight=Decimal(str(w)),
                       max_marks=Decimal(str(m)), sequence=i) for i, (n, w, m) in enumerate(scheme.value, 1)]
    _validate_scheme(rows)
    with transaction.atomic():
        for r in rows:
            r.full_clean()
            r.save()
        audit_ok(actor, "academic.assessment_scheme.define", offering, decision, request=request,
                 after={"weights": {r.name: str(r.weight) for r in rows}, "rule_versions": ctx.versions})
    return rows


def _validate_scheme(rows):
    total = sum((r.weight for r in rows if r.kind != Assessment.Kind.MANDATORY_MODULE), Decimal("0"))
    if total != Decimal("100"):
        raise ValidationError(f"Assessment weights must total 100 (got {total})")


def reweight_elective(actor, offering, *, continuous_weight: Decimal, approval_reference: str,
                      request=None) -> list[Assessment]:
    """Approved re-weighting of an elective within the configured limit, performed
    by the configured approving authority (assessment.elective_reweighting_approver).
    While that authority is UNRESOLVED, no re-weighting is possible."""
    ctx = RuleContext()
    approver = ctx("assessment.elective_reweighting_approver")
    if approver.unresolved:
        raise ValidationError("The authority that approves elective re-weighting is unresolved "
                              "(assessment.elective_reweighting_approver); requires institutional decision")
    decision = authorize_policy_or_deny(actor, "academic.assessment_scheme.reweight",
                                        Policy(rules=tuple(Rule(c) for c in approver.value), privileged=True),
                                        offering, request=request)
    if offering.course.category != Course.Category.ELECTIVE:
        raise ValidationError("Only electives may be re-weighted")
    if not approval_reference.strip():
        raise ValidationError("An approval reference is required for re-weighting")
    if MarkEntry.objects.filter(assessment__offering=offering).exists():
        raise ValidationError("Marks already entered; the scheme can no longer change")
    limit = Decimal(str(ctx("assessment.elective_reweighting_limit_percent").value))
    default = Decimal(str(ctx("assessment.default_weights").value["CONTINUOUS"]))
    continuous_weight = Decimal(str(continuous_weight))
    if abs(continuous_weight - default) > limit:
        raise ValidationError(f"Re-weighting beyond the configured limit of ±{limit}% "
                              f"(assessment.elective_reweighting_limit_percent)")
    ca = offering.assessments.get(kind=Assessment.Kind.CONTINUOUS)
    et = offering.assessments.get(kind=Assessment.Kind.END_TERM)
    before = {"CONTINUOUS": str(ca.weight), "END_TERM": str(et.weight)}
    with transaction.atomic():
        Assessment.objects.filter(pk=ca.pk).update(weight=continuous_weight, reweighting_reference=approval_reference)
        Assessment.objects.filter(pk=et.pk).update(weight=Decimal("100") - continuous_weight,
                                                   reweighting_reference=approval_reference)
        audit_ok(actor, "academic.assessment_scheme.reweight", offering, decision, request=request,
                 before=before, after={"CONTINUOUS": str(continuous_weight),
                                       "END_TERM": str(Decimal("100") - continuous_weight),
                                       "approval_reference": approval_reference,
                                       "rule_versions": ctx.versions})
    return list(offering.assessments.all())
