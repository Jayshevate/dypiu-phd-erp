"""Capabilities, scopes and workspaces.

A capability says WHAT a person may do, a scope says WHERE, and a
relationship (supervises / TAC member / self) says TO WHOM. Supervisor is a
capability held by a Faculty person, never a separate human identity.
"""
from django.db import models


class Capability(models.TextChoices):
    SCHOLAR = "SCHOLAR", "Scholar"
    FACULTY = "FACULTY", "Faculty"
    SUPERVISOR = "SUPERVISOR", "Supervisor"
    DEPARTMENT_ADMIN = "DEPARTMENT_ADMIN", "Department admin"
    SCHOOL_ADMIN = "SCHOOL_ADMIN", "School admin"
    SDRC_MEMBER = "SDRC_MEMBER", "SDRC member"
    DC_MEMBER = "DC_MEMBER", "DC member"
    EXAM_EVALUATOR = "EXAM_EVALUATOR", "Exam evaluator"
    PHD_CELL_OPERATOR = "PHD_CELL_OPERATOR", "PhD Cell operator"
    ACADEMIC_ADMIN = "ACADEMIC_ADMIN", "Academic admin"
    DEAN_RND = "DEAN_RND", "Dean R&D"
    COE_OPERATOR = "COE_OPERATOR", "COE operator"
    VC_OPERATOR = "VC_OPERATOR", "VC operator"
    SYSTEM_ADMIN = "SYSTEM_ADMIN", "System admin"


class ScopeType(models.TextChoices):
    INSTITUTION = "INSTITUTION", "Institution"
    SCHOOL = "SCHOOL", "School"
    DEPARTMENT = "DEPARTMENT", "Department"
    COMMITTEE = "COMMITTEE", "Committee"
    SCHOLAR = "SCHOLAR", "Single scholar"


# Capabilities that are never stored as grants: they are derived from the
# person's profiles (SCHOLAR, FACULTY) or committee memberships (DC, SDRC).
DERIVED_CAPABILITIES = frozenset({
    Capability.SCHOLAR, Capability.FACULTY, Capability.DC_MEMBER, Capability.SDRC_MEMBER,
})

# Allowed scope types per grantable capability.
ALLOWED_SCOPES = {
    Capability.SUPERVISOR: {ScopeType.DEPARTMENT, ScopeType.SCHOOL, ScopeType.INSTITUTION},
    Capability.DEPARTMENT_ADMIN: {ScopeType.DEPARTMENT},
    Capability.SCHOOL_ADMIN: {ScopeType.SCHOOL},
    Capability.EXAM_EVALUATOR: {ScopeType.SCHOLAR},
    Capability.PHD_CELL_OPERATOR: {ScopeType.INSTITUTION},
    Capability.ACADEMIC_ADMIN: {ScopeType.INSTITUTION},
    Capability.DEAN_RND: {ScopeType.INSTITUTION},
    Capability.COE_OPERATOR: {ScopeType.INSTITUTION},
    Capability.VC_OPERATOR: {ScopeType.INSTITUTION},
    Capability.SYSTEM_ADMIN: {ScopeType.INSTITUTION},
}

# Who may grant or revoke each capability. PROVISIONAL (RD-38): the official
# approval authorities are pending DYPIU confirmation.
GRANTABLE_BY = {
    Capability.SUPERVISOR: {Capability.ACADEMIC_ADMIN, Capability.SYSTEM_ADMIN},
    Capability.DEPARTMENT_ADMIN: {Capability.ACADEMIC_ADMIN, Capability.SYSTEM_ADMIN},
    Capability.SCHOOL_ADMIN: {Capability.ACADEMIC_ADMIN, Capability.SYSTEM_ADMIN},
    Capability.EXAM_EVALUATOR: {Capability.PHD_CELL_OPERATOR, Capability.SYSTEM_ADMIN},
    Capability.PHD_CELL_OPERATOR: {Capability.SYSTEM_ADMIN},
    Capability.ACADEMIC_ADMIN: {Capability.SYSTEM_ADMIN},
    Capability.DEAN_RND: {Capability.SYSTEM_ADMIN},
    Capability.COE_OPERATOR: {Capability.SYSTEM_ADMIN},
    Capability.VC_OPERATOR: {Capability.SYSTEM_ADMIN},
    Capability.SYSTEM_ADMIN: {Capability.SYSTEM_ADMIN},
}

# Workspace per capability (route shells in the React app). A person with
# several capabilities sees several workspaces; switching changes the view,
# never the identity.
WORKSPACES = {
    Capability.SCHOLAR: ("scholar", "/scholar"),
    Capability.FACULTY: ("faculty", "/faculty"),
    Capability.SUPERVISOR: ("supervisor", "/faculty/supervisor"),
    Capability.DEPARTMENT_ADMIN: ("department", "/department"),
    Capability.SCHOOL_ADMIN: ("school", "/school"),
    Capability.SDRC_MEMBER: ("sdrc", "/sdrc"),
    Capability.DC_MEMBER: ("dc", "/dc"),
    Capability.EXAM_EVALUATOR: ("examiner", "/examiner"),
    Capability.PHD_CELL_OPERATOR: ("phd_cell", "/phd-cell"),
    Capability.ACADEMIC_ADMIN: ("academic_admin", "/academic-admin"),
    Capability.DEAN_RND: ("dean", "/dean"),
    Capability.COE_OPERATOR: ("coe", "/coe"),
    Capability.VC_OPERATOR: ("vc", "/vc"),
    Capability.SYSTEM_ADMIN: ("system", "/system"),
}
