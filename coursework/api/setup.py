"""Institutional setup and provisioning API (Step A2): /api/academic/setup/...

Upstream-first setup: University → School → Department → Academic Year →
Semester → Course → Offering → Section → Faculty assignment → Scholar →
Enrollment. Offerings, sections, assignments and enrollment use the existing
governance / office endpoints. Every write goes through a domain or identity
service, which authorizes and audits it; the `can` flags only decide which
controls the UI shows."""
from django.contrib.auth.hashers import UNUSABLE_PASSWORD_PREFIX
from django.db.models import Q
from django.utils import timezone
from rest_framework import serializers
from rest_framework.response import Response

from core.models import Department, Designation, Faculty, School, University
from coursework.academic import institution, structure
from coursework.models import (AcademicYear, Course, CourseOffering, FacultySubjectAssignment, ScholarCourseEnrollment,
                               Section, Semester)
from identity import provisioning
from identity.models import Person
from scholars.models import Category, EntryQualification, Scholar

from .common import AcademicView, body, can, course_data, dt, load, paginate, require_any_holder, semester_data

SETUP_ACTIONS = ["institution.structure.manage", "academic.structure.manage", "faculty.record.manage",
                 "scholar.edit_record", "identity.person.manage", "academic.faculty_assignment.manage",
                 "academic.enrollment.manage"]


def _choices(enum):
    return [{"value": v, "label": str(label)} for v, label in enum.choices]


class StatusView(AcademicView):
    """Setup checklist: what exists at each level and whether this person may add to it."""

    def get(self, request):
        require_any_holder(request, SETUP_ACTIONS)
        p = self.person
        today = timezone.localdate()
        active_assignments = FacultySubjectAssignment.objects.filter(revoked_at__isnull=True, valid_from__lte=today) \
            .exclude(valid_to__lt=today)
        persons = Person.objects.filter(is_active=True)
        steps = [
            ("university", "University", University.objects.count(), "institution.structure.manage", []),
            ("schools", "Schools", School.objects.count(), "institution.structure.manage", []),
            ("departments", "Departments", Department.objects.count(), "institution.structure.manage", ["schools"]),
            ("academic_years", "Academic years", AcademicYear.objects.count(), "academic.structure.manage", []),
            ("semesters", "Semesters", Semester.objects.count(), "academic.structure.manage", ["academic_years"]),
            ("courses", "Course catalogue", Course.objects.filter(is_active=True).count(),
             "academic.structure.manage", []),
            ("offerings", "Course offerings", CourseOffering.objects.count(), "academic.structure.manage",
             ["courses", "semesters"]),
            ("sections", "Sections", Section.objects.count(), "academic.structure.manage", ["offerings"]),
            ("faculty", "Faculty records", Faculty.objects.count(), "faculty.record.manage", []),
            ("assignments", "Faculty assignments", active_assignments.count(), "academic.faculty_assignment.manage",
             ["offerings", "faculty"]),
            ("scholars", "Scholar records", Scholar.objects.count(), "scholar.edit_record", ["departments"]),
            ("people", "People with a login", persons.filter(user__isnull=False).count(), "identity.person.manage",
             []),
            ("enrollments", "Enrollments", ScholarCourseEnrollment.objects.exclude(status="WITHDRAWN").count(),
             "academic.enrollment.manage", ["offerings", "scholars"]),
        ]
        counts = {key: n for key, _, n, _, _ in steps}
        return Response({
            "steps": [{"key": key, "label": label, "count": n, "can_manage": can(p, action),
                       "blocked_by": [d for d in deps if counts[d] == 0]}
                      for key, label, n, action, deps in steps],
            "pending_activation": persons.filter(user__password__startswith=UNUSABLE_PASSWORD_PREFIX).count(),
            "choices": {
                "designation": _choices(Designation), "category": _choices(Category),
                "entry_qualification": _choices(EntryQualification),
                "gender": [{"value": "F", "label": "Female"}, {"value": "M", "label": "Male"},
                           {"value": "O", "label": "Other"}],
                "term": _choices(Semester.Term), "course_category": _choices(Course.Category),
                "activity_type": _choices(Course.ActivityType),
            },
        })


# --- institution structure -------------------------------------------------------------------------

def university_data(u):
    return {"id": u.pk, "code": u.code, "name": u.name, "schools": u.schools.count()}


def school_data(s):
    return {"id": s.pk, "code": s.code, "name": s.name, "university": s.university.name if s.university_id else None,
            "departments": s.departments.count()}


def department_data(d):
    return {"id": d.pk, "code": d.code, "name": d.name, "school_id": d.school_id, "school": d.school.name}


STRUCTURE_READERS = ["institution.structure.manage", "academic.structure.manage", "faculty.record.manage",
                     "scholar.edit_record", "academic.faculty_assignment.manage"]


class UniversitiesView(AcademicView):
    class In(serializers.Serializer):
        code = serializers.CharField(max_length=20)
        name = serializers.CharField(max_length=200)

    def get(self, request):
        require_any_holder(request, STRUCTURE_READERS)
        return Response({"results": [university_data(u) for u in University.objects.order_by("name")],
                         "can": {"create": can(self.person, "institution.structure.manage")}})

    def post(self, request):
        data = body(self.In, request)
        u = institution.create_university(self.person, code=data["code"], name=data["name"], request=request)
        return Response(university_data(u), status=201)


class SchoolsView(AcademicView):
    class In(serializers.Serializer):
        code = serializers.CharField(max_length=20)
        name = serializers.CharField(max_length=200)
        university_id = serializers.IntegerField(required=False, allow_null=True)

    def get(self, request):
        require_any_holder(request, STRUCTURE_READERS)
        qs = School.objects.select_related("university").order_by("name")
        return Response({"results": [school_data(s) for s in qs],
                         "can": {"create": can(self.person, "institution.structure.manage")}})

    def post(self, request):
        data = body(self.In, request)
        university = load(University, data["university_id"]) if data.get("university_id") else None
        s = institution.create_school(self.person, code=data["code"], name=data["name"], university=university,
                                      request=request)
        return Response(school_data(s), status=201)


class DepartmentsView(AcademicView):
    class In(serializers.Serializer):
        school_id = serializers.IntegerField()
        code = serializers.CharField(max_length=20)
        name = serializers.CharField(max_length=200)

    def get(self, request):
        require_any_holder(request, STRUCTURE_READERS)
        qs = Department.objects.select_related("school").order_by("school__name", "name")
        if request.query_params.get("school"):
            qs = qs.filter(school_id=request.query_params["school"])
        return Response({"results": [department_data(d) for d in qs],
                         "can": {"create": can(self.person, "institution.structure.manage")}})

    def post(self, request):
        data = body(self.In, request)
        d = institution.create_department(self.person, school=load(School, data["school_id"]), code=data["code"],
                                          name=data["name"], request=request)
        return Response(department_data(d), status=201)


# --- academic calendar and catalogue ---------------------------------------------------------------

def year_data(y):
    return {"id": y.pk, "code": y.code, "start_date": dt(y.start_date), "end_date": dt(y.end_date),
            "semesters": y.semesters.count()}


class AcademicYearsView(AcademicView):
    class In(serializers.Serializer):
        code = serializers.CharField(max_length=20)
        start_date = serializers.DateField()
        end_date = serializers.DateField()

    def get(self, request):
        require_any_holder(request, STRUCTURE_READERS)
        return Response({"results": [year_data(y) for y in AcademicYear.objects.order_by("-start_date")],
                         "can": {"create": can(self.person, "academic.structure.manage")}})

    def post(self, request):
        data = body(self.In, request)
        y = structure.create_academic_year(self.person, request=request, **data)
        return Response(year_data(y), status=201)


class SemestersView(AcademicView):
    class In(serializers.Serializer):
        academic_year_id = serializers.IntegerField()
        term = serializers.ChoiceField(choices=Semester.Term.choices)
        name = serializers.CharField(max_length=60)
        start_date = serializers.DateField()
        end_date = serializers.DateField()
        registration_opens = serializers.DateField(required=False, allow_null=True)
        registration_closes = serializers.DateField(required=False, allow_null=True)
        elective_registration_deadline = serializers.DateField(required=False, allow_null=True)

    def get(self, request):
        require_any_holder(request, STRUCTURE_READERS)
        qs = Semester.objects.select_related("academic_year").order_by("-start_date")
        return Response({"results": [{**semester_data(s),
                                      "elective_registration_deadline": dt(s.elective_registration_deadline)}
                                     for s in qs],
                         "can": {"create": can(self.person, "academic.structure.manage")}})

    def post(self, request):
        data = body(self.In, request)
        year = load(AcademicYear, data.pop("academic_year_id"))
        s = structure.create_semester(self.person, academic_year=year, request=request, **data)
        return Response(semester_data(s), status=201)


class CoursesView(AcademicView):
    class In(serializers.Serializer):
        code = serializers.CharField(max_length=20)
        title = serializers.CharField(max_length=200)
        credits = serializers.IntegerField(min_value=1)
        category = serializers.ChoiceField(choices=Course.Category.choices)
        activity_type = serializers.ChoiceField(choices=Course.ActivityType.choices, required=False, allow_blank=True,
                                                default="")
        applicable_categories = serializers.ListField(child=serializers.CharField(max_length=10), required=False,
                                                      default=list)
        department_id = serializers.IntegerField(required=False, allow_null=True)
        has_ethics_submodule = serializers.BooleanField(required=False, default=False)

    def get(self, request):
        require_any_holder(request, STRUCTURE_READERS)
        qs = Course.objects.select_related("department").order_by("code")
        return Response({"results": [{**course_data(c), "is_active": c.is_active,
                                      "applicable_categories": c.applicable_categories,
                                      "required_for_all": c.required_for_all,
                                      "has_ethics_submodule": c.has_ethics_submodule} for c in qs],
                         "can": {"create": can(self.person, "academic.structure.manage")}})

    def post(self, request):
        data = body(self.In, request)
        dept = load(Department, data.pop("department_id")) if data.get("department_id") else None
        data.pop("department_id", None)
        c = structure.create_course(self.person, department=dept, request=request, **data)
        return Response(course_data(c), status=201)


# --- faculty and scholar records + identity provisioning -------------------------------------------

def _persons_by(field, ids):
    return {getattr(p, f"{field}_id"): p for p in
            Person.objects.filter(**{f"{field}__in": ids}).select_related("user")}


def identity_data(person):
    if person is None:
        return {"person_id": None, "login": "NO_PERSON", "username": None}
    return {"person_id": person.pk, "login": provisioning.login_state(person),
            "username": person.user.get_username() if person.user_id else None, "is_active": person.is_active}


def faculty_record(f, person):
    return {"id": f.pk, "name": f.name, "email": f.email, "designation": f.designation,
            "department": f.department.name if f.department_id else None, "department_id": f.department_id,
            "is_external": f.is_external, "affiliation": f.affiliation, "identity": identity_data(person)}


def scholar_record(s, person):
    return {"id": s.pk, "prn": s.prn, "name": s.name, "email": s.email, "gender": s.gender, "category": s.category,
            "entry_qualification": s.entry_qualification, "department": s.department.name,
            "department_id": s.department_id, "admission_date": dt(s.admission_date),
            "registration_date": dt(s.registration_date), "status": s.status, "identity": identity_data(person)}


def _search(qs, request, fields):
    q = (request.query_params.get("q") or "").strip()
    if q:
        cond = Q()
        for f in fields:
            cond |= Q(**{f"{f}__icontains": q})
        qs = qs.filter(cond)
    return qs


class FacultyRecordsView(AcademicView):
    class In(serializers.Serializer):
        name = serializers.CharField(max_length=200)
        email = serializers.EmailField()
        designation = serializers.ChoiceField(choices=Designation.choices)
        department_id = serializers.IntegerField(required=False, allow_null=True)
        is_external = serializers.BooleanField(required=False, default=False)
        affiliation = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")

    def get(self, request):
        require_any_holder(request, ["faculty.record.manage"])
        qs = _search(Faculty.objects.select_related("department").order_by("name"), request,
                     ["name", "email", "department__name"])
        if request.query_params.get("department"):
            qs = qs.filter(department_id=request.query_params["department"])
        page = paginate(request, qs)
        persons = _persons_by("faculty_profile", [f.pk for f in page["results"]])
        page["results"] = [faculty_record(f, persons.get(f.pk)) for f in page["results"]]
        page["can"] = {"create": can(self.person, "faculty.record.manage"),
                       "provision": can(self.person, "identity.person.manage")}
        return Response(page)

    def post(self, request):
        data = body(self.In, request)
        dept = load(Department, data.pop("department_id")) if data.get("department_id") else None
        data.pop("department_id", None)
        f = institution.create_faculty_record(self.person, department=dept, request=request, **data)
        return Response(faculty_record(f, None), status=201)


class ScholarRecordsView(AcademicView):
    class In(serializers.Serializer):
        prn = serializers.CharField(max_length=30)
        name = serializers.CharField(max_length=200)
        email = serializers.EmailField()
        gender = serializers.ChoiceField(choices=[("F", "F"), ("M", "M"), ("O", "O")])
        category = serializers.ChoiceField(choices=Category.choices)
        entry_qualification = serializers.ChoiceField(choices=EntryQualification.choices)
        department_id = serializers.IntegerField()
        admission_date = serializers.DateField()
        registration_date = serializers.DateField(required=False, allow_null=True)
        research_area = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
        pwd_percent = serializers.IntegerField(min_value=0, max_value=100, required=False, default=0)

    def get(self, request):
        require_any_holder(request, ["scholar.edit_record"])
        qs = _search(Scholar.objects.select_related("department").order_by("prn"), request,
                     ["prn", "name", "email", "department__name"])
        if request.query_params.get("department"):
            qs = qs.filter(department_id=request.query_params["department"])
        qs = [s for s in qs if can(self.person, "scholar.edit_record", s)]
        page = paginate(request, qs)
        persons = _persons_by("scholar_profile", [s.pk for s in page["results"]])
        page["results"] = [scholar_record(s, persons.get(s.pk)) for s in page["results"]]
        page["can"] = {"create": can(self.person, "scholar.edit_record"),
                       "provision": can(self.person, "identity.person.manage")}
        return Response(page)

    def post(self, request):
        data = body(self.In, request)
        dept = load(Department, data.pop("department_id"))
        s = institution.create_scholar_record(self.person, department=dept, request=request, **data)
        return Response(scholar_record(s, None), status=201)


class ProvisionView(AcademicView):
    """Create the Person (and a login awaiting activation) for a faculty or scholar record."""

    class In(serializers.Serializer):
        kind = serializers.ChoiceField(choices=[("faculty", "faculty"), ("scholar", "scholar")])
        record_id = serializers.IntegerField()
        create_login = serializers.BooleanField(required=False, default=True)

    def post(self, request):
        data = body(self.In, request)
        record = load(Faculty if data["kind"] == "faculty" else Scholar, data["record_id"])
        person = provisioning.provision_for_record(self.person, record, create_login=data["create_login"],
                                                   request=request)
        return Response(identity_data(person), status=201)


class ActivationView(AcademicView):
    """Issue a one-time activation link for a login that has never been activated."""

    def post(self, request, pk):
        person = load(Person, pk)
        link = provisioning.issue_activation(self.person, person, request=request)
        return Response({"path": f"/academic/activate?uid={link['uid']}&token={link['token']}",
                         "expires_in_hours": _timeout_hours()})


def _timeout_hours():
    from django.conf import settings
    return settings.PASSWORD_RESET_TIMEOUT // 3600
