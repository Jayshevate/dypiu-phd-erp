"""Shared fixtures for tests (and handy in a shell for demo data)."""
from datetime import date
from itertools import count

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

from core.approvals import decide
from core.models import Department, Designation, Faculty, School
from core.roles import Role
from core.seed import seed_chains
from coursework.seed import seed_courses
from scholars.models import Scholar

_n = count(1)


def seed():
    for role in Role:
        Group.objects.get_or_create(name=role.value)
    seed_chains()
    seed_courses()


def user(*roles, username=None):
    u = get_user_model().objects.create_user(username or f"user{next(_n)}", password="x")
    for r in roles:
        u.groups.add(Group.objects.get(name=r))
    return u


def department(code=None):
    code = code or f"D{next(_n)}"
    school, _ = School.objects.get_or_create(code="SOE", defaults={"name": "School of Engineering"})
    return Department.objects.create(school=school, code=code, name=f"Dept {code}")


def faculty(designation=Designation.PROFESSOR, dept=None, with_user=False, **kw):
    u = user(Role.SUPERVISOR) if with_user else None
    return Faculty.objects.create(name=kw.pop("name", f"Prof {next(_n)}"), designation=designation,
                                  department=dept or department(), user=u, **kw)


def scholar(dept=None, **kw):
    defaults = dict(prn=f"PRN{next(_n):04d}", name="Test Scholar", gender="M", category="FT",
                    entry_qualification="MTECH", admission_date=date(2026, 7, 1),
                    registration_date=date(2026, 8, 1), registration_fee_paid=True)
    defaults.update(kw)
    return Scholar.objects.create(department=dept or department(), **defaults)


def approve_all(req, superuser=None):
    """Push an approval request through every step as a superuser."""
    su = superuser or get_user_model().objects.filter(is_superuser=True).first() or \
        get_user_model().objects.create_superuser(f"root{next(_n)}", password="x")
    while req.status == "PENDING":
        req = decide(req, su, approved=True)
    return req
