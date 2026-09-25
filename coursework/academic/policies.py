"""Academic authorization policies and relationships, registered with
identity.authz at start-up. PROVISIONAL (RD-38) where the approving
authority is not confirmed; relationship rules (assigned faculty only,
own records only) are not provisional."""
from django.utils import timezone

from identity.authz import Policy, Rule, register_policies, register_relationship
from identity.capabilities import Capability as C


def _active_assignment(person, role, **where):
    from coursework.models import FacultySubjectAssignment
    if not person.faculty_profile_id:
        return False
    today = timezone.localdate()
    return FacultySubjectAssignment.objects.filter(
        faculty_id=person.faculty_profile_id, role=role, revoked_at__isnull=True, valid_from__lte=today,
        **where).exclude(valid_to__lt=today).exists()


def teaches_section(person, resource) -> bool:
    section = getattr(resource, "teaching_section", None)
    return section is not None and _active_assignment(person, "INSTRUCTOR", section=section)


def teaches_in_offering(person, resource) -> bool:
    """Instructor of any section of the offering (read access to the offering)."""
    offering = getattr(resource, "teaching_offering", None)
    return offering is not None and _active_assignment(person, "INSTRUCTOR", section__offering=offering)


def coordinates_offering(person, resource) -> bool:
    offering = getattr(resource, "teaching_offering", None)
    return offering is not None and _active_assignment(person, "COURSE_COORDINATOR", offering=offering)


ACADEMIC_OVERSIGHT = (C.PHD_CELL_OPERATOR, C.RND_CELL_OPERATOR, C.CISR_OPERATOR, C.ACADEMIC_ADMIN,
                      C.DEAN_RND, C.COE_OPERATOR)


def _p(*rules, privileged=False, description=""):
    return Policy(rules=tuple(rules), privileged=privileged, description=description)


POLICIES = {
    # configuration: maker (ACADEMIC_ADMIN) → checker (DEAN_RND), provisional (RD-38)
    "academic.config.change": _p(Rule(C.ACADEMIC_ADMIN), privileged=True),
    "academic.config.approve": _p(Rule(C.DEAN_RND), privileged=True),
    # semester registration & fee / dues clearance (recording office provisional: D-CAP)
    "academic.semester.register.self": _p(Rule(C.SCHOLAR, "self")),
    "academic.semester.register.manage": _p(Rule(C.PHD_CELL_OPERATOR), Rule(C.RND_CELL_OPERATOR), privileged=True),
    "academic.fee_clearance.record": _p(Rule(C.PHD_CELL_OPERATOR), Rule(C.RND_CELL_OPERATOR), privileged=True),
    # department / school elective lists (approval chain: elective.list_approval_chain)
    "academic.elective_list.prepare": _p(Rule(C.DEPARTMENT_ADMIN), Rule(C.SCHOOL_ADMIN), privileged=True),
    # activity scoring schemes are applied from configuration (activity.scoring_scheme.*)
    "academic.activity.scheme.define": _p(Rule(C.ACADEMIC_ADMIN), Rule(C.CISR_OPERATOR), Rule(C.SDRC_MEMBER)),
    # hall tickets, question papers (setter appointment authority: question_paper.setter_appointed_by)
    "academic.hall_ticket.issue": _p(Rule(C.COE_OPERATOR), privileged=True),
    "academic.hall_ticket.view": _p(Rule(C.SCHOLAR, "self"), Rule(C.COE_OPERATOR), Rule(C.RND_CELL_OPERATOR),
                                    Rule(C.PHD_CELL_OPERATOR)),
    "academic.question_paper.receive": _p(Rule(C.COE_OPERATOR), privileged=True),
    # revaluation (reviewer authority: revaluation.reviewer)
    "academic.revaluation.request.self": _p(Rule(C.SCHOLAR, "self")),
    "academic.revaluation.request.manage": _p(Rule(C.PHD_CELL_OPERATOR), Rule(C.RND_CELL_OPERATOR), privileged=True),
    # transcript issuance (COE issues grade sheets: React M13)
    "academic.transcript.issue": _p(Rule(C.COE_OPERATOR), privileged=True),
    "academic.structure.manage": _p(Rule(C.ACADEMIC_ADMIN), Rule(C.CISR_OPERATOR), privileged=True,
                                    description="Courses, years, semesters, offerings, sections, assessment schemes"),
    "academic.faculty_assignment.manage": _p(Rule(C.ACADEMIC_ADMIN), Rule(C.DEPARTMENT_ADMIN),
                                             Rule(C.SCHOOL_ADMIN), privileged=True),
    # read an offering (roster, assessments): its teaching staff and the academic governance offices
    "academic.offering.view": _p(Rule(C.FACULTY, "coordinates_offering"), Rule(C.FACULTY, "teaches_in_offering"),
                                 Rule(C.ACADEMIC_ADMIN), Rule(C.CISR_OPERATOR), Rule(C.RND_CELL_OPERATOR),
                                 Rule(C.COE_OPERATOR)),
    # enrollment
    "academic.enrollment.self": _p(Rule(C.SCHOLAR, "self")),
    "academic.enrollment.manage": _p(Rule(C.RND_CELL_OPERATOR), Rule(C.PHD_CELL_OPERATOR),
                                     Rule(C.ACADEMIC_ADMIN), privileged=True),
    # read access to a scholar's academic record
    "academic.record.view": _p(Rule(C.SCHOLAR, "self"), Rule(C.SUPERVISOR, "supervises"),
                               Rule(C.FACULTY, "teaches_section"), Rule(C.FACULTY, "coordinates_offering"),
                               Rule(C.DEPARTMENT_ADMIN), Rule(C.SCHOOL_ADMIN), Rule(C.SDRC_MEMBER),
                               Rule(C.DC_MEMBER), *(Rule(c) for c in ACADEMIC_OVERSIGHT)),
    # attendance
    "academic.attendance.record": _p(Rule(C.FACULTY, "teaches_section"), Rule(C.FACULTY, "coordinates_offering")),
    "academic.orientation.record": _p(Rule(C.PHD_CELL_OPERATOR), Rule(C.RND_CELL_OPERATOR), Rule(C.DC_MEMBER)),
    # marks
    "academic.marks.enter.continuous": _p(Rule(C.FACULTY, "teaches_section"),
                                          Rule(C.FACULTY, "coordinates_offering")),
    "academic.marks.enter.end_term": _p(Rule(C.FACULTY, "coordinates_offering")),
    "academic.marks.enter.activity": _p(Rule(C.SDRC_MEMBER)),
    # results: prepared → verified → ratified
    "academic.result.prepare.course": _p(Rule(C.FACULTY, "coordinates_offering")),
    "academic.result.prepare.activity": _p(Rule(C.SDRC_MEMBER)),
    "academic.result.verify": _p(Rule(C.RND_CELL_OPERATOR), privileged=True),
    "academic.result.ratify": _p(Rule(C.COE_OPERATOR), privileged=True),
    "academic.result.return": _p(Rule(C.RND_CELL_OPERATOR), Rule(C.COE_OPERATOR), privileged=True),
    # examination
    "academic.exam.manage": _p(Rule(C.COE_OPERATOR), Rule(C.ACADEMIC_ADMIN), privileged=True),
    "academic.exam.register.self": _p(Rule(C.SCHOLAR, "self")),
    "academic.exam.register.manage": _p(Rule(C.COE_OPERATOR), Rule(C.RND_CELL_OPERATOR),
                                        Rule(C.PHD_CELL_OPERATOR), privileged=True),
    # third attempt: review/decide authorities come from third_attempt.review_chain (Dean R&D, then VC)
    "academic.third_attempt.submit": _p(Rule(C.SCHOLAR, "self"), Rule(C.PHD_CELL_OPERATOR),
                                        Rule(C.RND_CELL_OPERATOR)),
    # electives (decision authority is a runtime parameter: elective.approval_authority)
    "academic.elective.propose": _p(Rule(C.SCHOLAR, "self")),
    "academic.elective.recommend": _p(Rule(C.SUPERVISOR, "supervises")),
    # activities
    "academic.activity.submit": _p(Rule(C.SCHOLAR, "self")),
    "academic.activity.review": _p(Rule(C.SDRC_MEMBER)),
    # records
    "academic.transcript.view": _p(Rule(C.SCHOLAR, "self"), Rule(C.SUPERVISOR, "supervises"),
                                   Rule(C.DEPARTMENT_ADMIN), Rule(C.SCHOOL_ADMIN),
                                   *(Rule(c) for c in ACADEMIC_OVERSIGHT)),
}


def register():
    register_relationship("teaches_section", teaches_section)
    register_relationship("coordinates_offering", coordinates_offering)
    register_relationship("teaches_in_offering", teaches_in_offering)
    register_policies(POLICIES)
