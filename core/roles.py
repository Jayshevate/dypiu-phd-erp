"""The 15 RBAC roles. Each role is a Django auth Group whose name is the
role value; ``seed_reference_data`` creates them."""
from django.db import models


class Role(models.TextChoices):
    SCHOLAR = "scholar", "Candidate / Scholar"
    SUPERVISOR = "supervisor", "Supervisor"
    CO_SUPERVISOR = "co_supervisor", "Co-Supervisor"
    TAC_MEMBER = "tac_member", "TAC Member"
    SDRC = "sdrc", "SDRC"
    DC = "dc", "Doctoral Committee"
    DEAN_RD = "dean_rd", "Dean / Dy. Dean R&D"
    VC = "vc", "Vice-Chancellor"
    COE = "coe", "Controller of Examinations"
    REGISTRAR = "registrar", "Registrar"
    HR = "hr", "HR Office"
    RD_OFFICE = "rd_office", "R&D Office"
    COURSE_COORDINATOR = "course_coordinator", "Course Coordinator"
    EXTERNAL_EXAMINER = "external_examiner", "External Examiner"
    DPEP_MEMBER = "dpep_member", "DPEP Member"


def user_has_role(user, role: str) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name=role).exists()
