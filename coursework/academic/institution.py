"""Institutional setup (Step A2): university, schools, departments and the
faculty / scholar records that people are later linked to.

Authorities:
- universities, schools, departments: `institution.structure.manage`
- faculty records: `faculty.record.manage`
  (both ACADEMIC_ADMIN, PROVISIONAL under RD-38)
- scholar records: the existing `scholar.edit_record` (PhD Cell, Academic Admin)

Institutional e-mail is required on faculty and scholar records and must be
unique. It is the key used to link a Person and, later, to match imports, so
two records never share one."""
from django.core.exceptions import ValidationError

from core.models import Department, Designation, Faculty, School, University
from scholars.models import Category, EntryQualification, Scholar

from .structure import _create


def _email(value) -> str:
    email = (value or "").strip().lower()
    if not email:
        raise ValidationError("An institutional e-mail address is required")
    return email


def create_university(actor, *, code, name, request=None) -> University:
    return _create(University, actor, "institution.structure.manage", None,
                   dict(code=code.strip(), name=name.strip()), request)


def create_school(actor, *, code, name, university=None, request=None) -> School:
    return _create(School, actor, "institution.structure.manage", None,
                   dict(code=code.strip(), name=name.strip(), university=university), request)


def create_department(actor, *, school, code, name, request=None) -> Department:
    return _create(Department, actor, "institution.structure.manage", school,
                   dict(school=school, code=code.strip(), name=name.strip()), request)


def create_faculty_record(actor, *, name, email, designation, department=None, is_external=False, affiliation="",
                          request=None) -> Faculty:
    email = _email(email)
    if designation not in Designation.values:
        raise ValidationError(f"Unknown designation {designation!r}")
    if not is_external and department is None:
        raise ValidationError("An internal faculty member needs a department")
    if Faculty.objects.filter(email__iexact=email).exists():
        raise ValidationError(f"A faculty record with e-mail {email} already exists")
    return _create(Faculty, actor, "faculty.record.manage", department,
                   dict(name=name.strip(), email=email, designation=designation, department=department,
                        is_external=is_external, affiliation=affiliation.strip()), request)


def create_scholar_record(actor, *, prn, name, email, gender, category, entry_qualification, department,
                          admission_date, registration_date=None, research_area="", pwd_percent=0,
                          request=None) -> Scholar:
    email = _email(email)
    prn = (prn or "").strip()
    if category not in Category.values:
        raise ValidationError(f"Unknown category {category!r}")
    if entry_qualification not in EntryQualification.values:
        raise ValidationError(f"Unknown entry qualification {entry_qualification!r}")
    if Scholar.objects.filter(prn__iexact=prn).exists():
        raise ValidationError(f"A scholar with PRN {prn} already exists")
    if Scholar.objects.filter(email__iexact=email).exists():
        raise ValidationError(f"A scholar record with e-mail {email} already exists")
    return _create(Scholar, actor, "scholar.edit_record", department,
                   dict(prn=prn, name=name.strip(), email=email, gender=gender, category=category,
                        entry_qualification=entry_qualification, department=department,
                        admission_date=admission_date, registration_date=registration_date,
                        research_area=research_area.strip(), pwd_percent=pwd_percent), request)
