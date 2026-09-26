"""Import types (Step A3): the canonical fields of each supported import, how a
cell value is validated and matched to real ERP records, how duplicates and
conflicts are recognised, and which existing domain service applies a row.

Nothing here writes directly: rows are applied through the same services (and
therefore the same authorization, validation and audit) as the individual
screens. Records are matched on stable identifiers (PRN, institutional e-mail,
course / department / school codes), never on display names alone, and
existing records are never updated or merged by an import."""
import difflib
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils import timezone

from core.models import Department, Designation, Faculty, School, University
from coursework.academic import institution, structure
from coursework.academic.enrollment import enroll
from coursework.models import (AcademicYear, Course, CourseOffering, FacultySubjectAssignment, ScholarCourseEnrollment,
                               Section, Semester)
from identity import provisioning
from scholars.models import Category, EntryQualification, Scholar

VALID, DUPLICATE, INVALID, AMBIGUOUS, MISSING, CONFLICT = "VALID", "DUPLICATE", "INVALID", "AMBIGUOUS", "MISSING", \
    "CONFLICT"
STATUSES = [VALID, DUPLICATE, INVALID, AMBIGUOUS, MISSING, CONFLICT]
BLOCKING = {INVALID, AMBIGUOUS, MISSING, CONFLICT}


class GenderChoices:
    """The scholar model's gender codes (declared inline on Scholar.gender)."""
    choices = [("F", "Female"), ("M", "Male"), ("O", "Other")]


class RowProblem(Exception):
    def __init__(self, status, reason):
        super().__init__(reason)
        self.status, self.reason = status, reason


def compact(text) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


# --- field kinds -------------------------------------------------------------------------------------------

@dataclass
class FieldSpec:
    name: str
    label: str
    required: bool = False
    kind: str = "text"            # text, email, date, int, bool, choice, department, university, course, scholar, faculty
    choices: object = None        # TextChoices class (for kind="choice")
    aliases: tuple = ()           # alternative column headers
    value_aliases: dict = field(default_factory=dict)  # extra accepted spellings for choice values
    example: str = ""
    help: str = ""
    min_value: int | None = None
    max_value: int | None = None
    max_length: int | None = None

    def format_text(self) -> str:
        return {
            "text": "Text", "email": "E-mail address", "int": "Whole number",
            "date": "Date: YYYY-MM-DD, or DD-MM-YYYY / DD/MM/YYYY (day first)",
            "bool": "Yes / No", "choice": "One of the allowed values",
            "department": "Existing department code (or exact department name)",
            "university": "Existing university code", "course": "Existing course code",
            "scholar": "Existing scholar PRN", "faculty": "Existing faculty institutional e-mail",
        }[self.kind]

    def allowed(self) -> list[str]:
        if self.kind == "choice":
            return [f"{v} ({label})" if compact(v) != compact(label) else str(v)
                    for v, label in self.choices.choices if v != ""]
        if self.kind == "bool":
            return ["Yes", "No"]
        return []


def _parse_date(text: str):
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise RowProblem(INVALID, f"'{text}' is not a date (use YYYY-MM-DD or DD-MM-YYYY)")


def _close(text, options):
    hits = difflib.get_close_matches(text, options, n=3, cutoff=0.75)
    return f" Did you mean: {', '.join(hits)}?" if hits else ""


def parse_value(spec: FieldSpec, raw):
    """Validate one non-blank cell. Raises RowProblem(INVALID / AMBIGUOUS)."""
    text = raw.strip() if isinstance(raw, str) else raw
    label = spec.label
    if spec.kind == "date":
        if isinstance(raw, datetime):
            return raw.date()
        if isinstance(raw, date):
            return raw
        return _parse_date(str(text))
    s = str(text).strip()
    if isinstance(raw, float) and raw.is_integer():
        s = str(int(raw))
    if spec.max_length and len(s) > spec.max_length:
        raise RowProblem(INVALID, f"{label} is longer than {spec.max_length} characters")
    if spec.kind == "text":
        return s
    if spec.kind == "email":
        try:
            validate_email(s)
        except ValidationError:
            raise RowProblem(INVALID, f"{label} '{s}' is not a valid e-mail address")
        return s.lower()
    if spec.kind == "int":
        try:
            n = int(float(s)) if re.fullmatch(r"-?\d+(\.0+)?", s) else int(s)
        except ValueError:
            raise RowProblem(INVALID, f"{label} '{s}' is not a whole number")
        if spec.min_value is not None and n < spec.min_value or spec.max_value is not None and n > spec.max_value:
            raise RowProblem(INVALID, f"{label} must be between {spec.min_value} and {spec.max_value}")
        return n
    if spec.kind == "bool":
        c = compact(s)
        if c in ("yes", "y", "true", "1"):
            return True
        if c in ("no", "n", "false", "0"):
            return False
        raise RowProblem(INVALID, f"{label} must be Yes or No, not '{s}'")
    if spec.kind == "choice":
        tokens = {}
        for v, lab in spec.choices.choices:
            if v == "":
                continue
            for t in (compact(v), compact(lab)):
                tokens.setdefault(t, set()).add(v)
        for alias, v in spec.value_aliases.items():
            tokens.setdefault(compact(alias), set()).add(v)
        hit = tokens.get(compact(s), set())
        if len(hit) == 1:
            return next(iter(hit))
        if len(hit) > 1:
            raise RowProblem(AMBIGUOUS, f"{label} '{s}' matches several values: {', '.join(sorted(hit))}")
        raise RowProblem(INVALID, f"{label} '{s}' is not an allowed value ({'; '.join(spec.allowed())})")
    if spec.kind == "department":
        by_code = list(Department.objects.filter(code__iexact=s))
        if len(by_code) == 1:
            return by_code[0]
        by_name = list(Department.objects.filter(name__iexact=s).select_related("school"))
        if len(by_name) == 1:
            return by_name[0]
        if len(by_name) > 1:
            raise RowProblem(AMBIGUOUS, f"Department '{s}' matches several departments "
                                        f"({', '.join(f'{d.code} in {d.school.name}' for d in by_name)}); use the code")
        names = list(Department.objects.values_list("code", flat=True)) + \
            list(Department.objects.values_list("name", flat=True))
        raise RowProblem(INVALID, f"No department with code or name '{s}'.{_close(s, names)}")
    if spec.kind == "university":
        u = University.objects.filter(code__iexact=s).first()
        if u is None:
            raise RowProblem(INVALID, f"No university with code '{s}'")
        return u
    if spec.kind == "course":
        c = Course.objects.filter(code__iexact=s).first()
        if c is None:
            raise RowProblem(INVALID, f"No course with code '{s}'.{_close(s.upper(), list(Course.objects.values_list('code', flat=True)))}")
        return c
    if spec.kind == "scholar":
        sch = Scholar.objects.filter(prn__iexact=s).select_related("department").first()
        if sch is None:
            raise RowProblem(INVALID, f"No scholar with PRN '{s}'")
        return sch
    if spec.kind == "faculty":
        matches = list(Faculty.objects.filter(email__iexact=s))
        if len(matches) > 1:
            raise RowProblem(AMBIGUOUS, f"Several faculty records use e-mail '{s}'")
        if not matches:
            raise RowProblem(INVALID, f"No faculty record with e-mail '{s}'")
        return matches[0]
    raise ValueError(spec.kind)


def fingerprint(value):
    if hasattr(value, "pk"):
        return f"{type(value).__name__}:{value.pk}"
    return compact(value) if isinstance(value, str) else str(value)


# --- import types --------------------------------------------------------------------------------------------

class ImportType:
    key = ""
    label = ""
    action = ""                   # the SAME policy as the individual operation
    description = ""
    provisionable = False
    fields: list[FieldSpec] = []

    def field(self, name) -> FieldSpec:
        return next(f for f in self.fields if f.name == name)

    def natural_key(self, v) -> tuple:
        raise NotImplementedError

    def describe_key(self, v) -> str:
        return " / ".join(str(fingerprint(x)) for x in self.natural_key(v))

    def resolve(self, v):
        """Resolve composite references (e.g. year + term → semester). May raise RowProblem."""

    def existing(self, v):
        """None, or RowProblem(DUPLICATE / CONFLICT) when the record already exists."""

    def apply(self, actor, v, options) -> tuple[list, list]:
        """Create through the domain services. Returns (created refs, provisioned person ids)."""
        raise NotImplementedError

    def as_dict(self):
        return {"key": self.key, "label": self.label, "description": self.description,
                "provisionable": self.provisionable,
                "fields": [{"name": f.name, "label": f.label, "required": f.required, "format": f.format_text(),
                            "allowed": f.allowed(), "example": f.example, "help": f.help} for f in self.fields]}


def _ref(obj):
    return {"model": obj._meta.label, "id": obj.pk}


def _same(a, b) -> bool:
    return compact(a) == compact(b)


class StructureImport(ImportType):
    key = "structure"
    label = "Schools and departments"
    action = "institution.structure.manage"
    description = "Creates schools (if new) and departments. Existing schools and departments are never changed."
    fields = [
        FieldSpec("school_code", "School code", True, aliases=("school", "school id"), example="SOE", max_length=20),
        FieldSpec("school_name", "School name", aliases=("name of school",), example="School of Engineering",
                  help="Needed only when the school is new", max_length=200),
        FieldSpec("department_code", "Department code", True, aliases=("dept code", "department id", "dept id"),
                  example="CSE", max_length=20),
        FieldSpec("department_name", "Department name", True, aliases=("department", "dept", "dept name"),
                  example="Computer Science and Engineering", max_length=200),
        FieldSpec("university_code", "University code", kind="university", aliases=("university",), example="DYPIU",
                  help="Optional; must be an existing university"),
    ]

    def natural_key(self, v):
        return (v["department_code"].upper(),)

    def existing(self, v):
        school = School.objects.filter(code__iexact=v["school_code"]).first()
        if school and v.get("school_name") and not _same(school.name, v["school_name"]):
            raise RowProblem(CONFLICT, f"School {school.code} exists as '{school.name}', not '{v['school_name']}'")
        dept = Department.objects.filter(code__iexact=v["department_code"]).select_related("school").first()
        if dept:
            if dept.school.code.lower() == v["school_code"].lower() and _same(dept.name, v["department_name"]):
                raise RowProblem(DUPLICATE, f"Department {dept.code} already exists")
            raise RowProblem(CONFLICT, f"Department code {dept.code} already exists as '{dept.name}' in "
                                       f"{dept.school.name}")
        if not school and not v.get("school_name"):
            raise RowProblem(MISSING, f"School {v['school_code']} is new: a school name is required")

    def apply(self, actor, v, options):
        refs = []
        school = School.objects.filter(code__iexact=v["school_code"]).first()
        if school is None:
            school = institution.create_school(actor, code=v["school_code"], name=v["school_name"],
                                               university=v.get("university_code"))
            refs.append(_ref(school))
        dept = institution.create_department(actor, school=school, code=v["department_code"],
                                             name=v["department_name"])
        return refs + [_ref(dept)], []


class CourseImport(ImportType):
    key = "courses"
    label = "Course catalogue"
    action = "academic.structure.manage"
    description = "Adds courses to the catalogue. Existing courses are never changed."
    fields = [
        FieldSpec("code", "Course code", True, aliases=("course code", "subject code", "code no"), example="SIS7101",
                  max_length=20),
        FieldSpec("title", "Title", True, aliases=("course title", "course name", "subject", "subject name", "name"),
                  example="Advanced Research Methods", max_length=200),
        FieldSpec("credits", "Credits", True, kind="int", aliases=("credit", "credit points"), example="3",
                  min_value=1, max_value=40),
        FieldSpec("category", "Category", True, kind="choice", choices=Course.Category, aliases=("course type", "type"),
                  value_aliases={"mandatory": "MANDATORY", "core": "MANDATORY", "activity": "ACTIVITY"},
                  example="ELECTIVE"),
        FieldSpec("activity_type", "Activity type", kind="choice", choices=Course.ActivityType,
                  help="Only for ACTIVITY courses", example=""),
        FieldSpec("department", "Department", kind="department", aliases=("dept", "owning department"),
                  example="CSE", help="Leave empty for a course open to all departments"),
        FieldSpec("has_ethics_submodule", "Has ethics sub-module", kind="bool", aliases=("ethics",), example="No"),
    ]

    def natural_key(self, v):
        return (v["code"].upper(),)

    def existing(self, v):
        c = Course.objects.filter(code__iexact=v["code"]).first()
        if c:
            if _same(c.title, v["title"]) and c.credits == v["credits"] and c.category == v["category"]:
                raise RowProblem(DUPLICATE, f"Course {c.code} already exists")
            raise RowProblem(CONFLICT, f"Course {c.code} already exists with different details "
                                       f"('{c.title}', {c.credits} credits, {c.category})")

    def apply(self, actor, v, options):
        c = structure.create_course(actor, code=v["code"], title=v["title"], credits=v["credits"],
                                    category=v["category"], activity_type=v.get("activity_type") or "",
                                    department=v.get("department"),
                                    has_ethics_submodule=bool(v.get("has_ethics_submodule")))
        return [_ref(c)], []


def _provision(actor, record, options):
    mode = options.get("provision", "none")
    if mode == "none":
        return []
    person = provisioning.provision_for_record(actor, record, create_login=(mode == "login"))
    return [person.pk]


class FacultyImport(ImportType):
    key = "faculty"
    label = "Faculty records"
    action = "faculty.record.manage"
    description = ("Creates faculty records keyed by institutional e-mail and, optionally, their institutional "
                   "identity and a login awaiting activation. The Faculty workspace is derived by the server.")
    provisionable = True
    fields = [
        FieldSpec("name", "Full name", True, aliases=("faculty name", "name of faculty", "teacher name",
                                                      "employee name", "full name"), example="Dr Asha Rao",
                  max_length=200),
        FieldSpec("email", "Institutional e-mail", True, kind="email",
                  aliases=("email", "e mail", "email id", "mail id", "official email", "institutional email"),
                  example="asha.rao@dypiu.ac.in"),
        FieldSpec("designation", "Designation", True, kind="choice", choices=Designation,
                  aliases=("post", "position", "rank"), value_aliases={"asst professor": "ASSISTANT_PROFESSOR",
                                                                        "assoc professor": "ASSOCIATE_PROFESSOR"},
                  example="Professor"),
        FieldSpec("department", "Department", kind="department", aliases=("dept", "department name", "dept name",
                                                                          "department code", "dept code"),
                  example="CSE", help="Required unless external"),
        FieldSpec("is_external", "External", kind="bool", aliases=("external faculty",), example="No"),
        FieldSpec("affiliation", "Affiliation", aliases=("institution", "organisation", "organization"),
                  example="", max_length=255),
    ]

    def natural_key(self, v):
        return (v["email"],)

    def existing(self, v):
        f = Faculty.objects.filter(email__iexact=v["email"]).select_related("department").first()
        if f:
            if _same(f.name, v["name"]) and f.department_id == getattr(v.get("department"), "pk", None):
                raise RowProblem(DUPLICATE, f"A faculty record for {v['email']} already exists")
            raise RowProblem(CONFLICT, f"{v['email']} already belongs to faculty record '{f.name}' "
                                       f"({f.department.name if f.department_id else 'external'}); never merged")

    def apply(self, actor, v, options):
        f = institution.create_faculty_record(actor, name=v["name"], email=v["email"], designation=v["designation"],
                                              department=v.get("department"),
                                              is_external=bool(v.get("is_external")),
                                              affiliation=v.get("affiliation") or "")
        return [_ref(f)], _provision(actor, f, options)


class ScholarImport(ImportType):
    key = "scholars"
    label = "Scholar records"
    action = "scholar.edit_record"
    description = ("Creates scholar records keyed by PRN and institutional e-mail and, optionally, their "
                   "institutional identity and a login awaiting activation. The Scholar workspace is derived.")
    provisionable = True
    fields = [
        FieldSpec("prn", "PRN", True, aliases=("prn no", "prn number", "permanent registration number",
                                               "registration number", "enrollment number", "enrolment number"),
                  example="PRN2026001", max_length=30),
        FieldSpec("name", "Full name", True, aliases=("scholar name", "student name", "candidate name", "full name",
                                                      "name of scholar", "name of candidate"),
                  example="Neha Kulkarni", max_length=200),
        FieldSpec("email", "Institutional e-mail", True, kind="email",
                  aliases=("email", "e mail", "email id", "mail id", "official email", "institutional email"),
                  example="neha.k@dypiu.ac.in"),
        FieldSpec("gender", "Gender", True, kind="choice",
                  choices=GenderChoices,
                  aliases=("sex",), example="F"),
        FieldSpec("category", "Category", True, kind="choice", choices=Category,
                  aliases=("mode", "programme category", "admission category"),
                  value_aliases={"full time": "FT", "fulltime": "FT"}, example="FT"),
        FieldSpec("entry_qualification", "Entry qualification", True, kind="choice", choices=EntryQualification,
                  aliases=("qualification", "entry", "qualifying degree"), example="MTECH",
                  help="Maps to a coursework category through the configured rule (RD-43)"),
        FieldSpec("department", "Department", True, kind="department",
                  aliases=("dept", "department name", "dept name", "department code", "dept code"), example="CSE"),
        FieldSpec("admission_date", "Admission date", True, kind="date", aliases=("date of admission", "admitted on"),
                  example="2026-07-10"),
        FieldSpec("registration_date", "Provisional registration date", kind="date",
                  aliases=("registration date", "date of registration"), example=""),
        FieldSpec("research_area", "Research area", aliases=("area of research", "research topic"), example="",
                  max_length=255),
        FieldSpec("pwd_percent", "PwD %", kind="int", aliases=("pwd", "disability percent"), example="0",
                  min_value=0, max_value=100),
    ]

    def natural_key(self, v):
        return (v["prn"].upper(),)

    def existing(self, v):
        s = Scholar.objects.filter(prn__iexact=v["prn"]).first()
        if s:
            if (s.email or "").lower() == v["email"] and _same(s.name, v["name"]):
                raise RowProblem(DUPLICATE, f"Scholar {s.prn} already exists")
            raise RowProblem(CONFLICT, f"PRN {s.prn} already belongs to '{s.name}' <{s.email}>; never merged")
        other = Scholar.objects.filter(email__iexact=v["email"]).first()
        if other:
            raise RowProblem(CONFLICT, f"{v['email']} already belongs to scholar {other.prn}")

    def apply(self, actor, v, options):
        s = institution.create_scholar_record(
            actor, prn=v["prn"], name=v["name"], email=v["email"], gender=v["gender"], category=v["category"],
            entry_qualification=v["entry_qualification"], department=v["department"],
            admission_date=v["admission_date"], registration_date=v.get("registration_date"),
            research_area=v.get("research_area") or "", pwd_percent=v.get("pwd_percent") or 0)
        return [_ref(s)], _provision(actor, s, options)


def _semester_fields():
    return [
        FieldSpec("academic_year", "Academic year", True, aliases=("year", "ay", "academic year code"),
                  example="2026-27"),
        FieldSpec("term", "Term", True, kind="choice", choices=Semester.Term, aliases=("semester", "sem", "session"),
                  value_aliases={"jan jun": "JAN_JUN", "jul dec": "JUL_DEC"}, example="JUL_DEC"),
    ]


def _offering(v):
    sem = Semester.objects.filter(academic_year__code__iexact=v["academic_year"], term=v["term"]).first()
    if sem is None:
        if not AcademicYear.objects.filter(code__iexact=v["academic_year"]).exists():
            raise RowProblem(INVALID, f"No academic year '{v['academic_year']}'")
        raise RowProblem(INVALID, f"No {v['term']} semester in {v['academic_year']}")
    offering = CourseOffering.objects.filter(course=v["course_code"], semester=sem).first()
    if offering is None:
        raise RowProblem(INVALID, f"{v['course_code'].code} is not offered in {sem.name}")
    v["_offering"] = offering
    if v.get("section"):
        section = Section.objects.filter(offering=offering, code__iexact=v["section"]).first()
        if section is None:
            raise RowProblem(INVALID, f"{v['course_code'].code} ({sem.name}) has no section '{v['section']}'")
        v["_section"] = section
    else:
        v["_section"] = None


class EnrollmentImport(ImportType):
    key = "enrollments"
    label = "Course enrollments"
    action = "academic.enrollment.manage"
    description = ("Enrolls existing scholars in existing offerings. Every enrollment rule (semester registration, "
                   "category applicability, capacity, electives) is applied exactly as for a single enrollment.")
    fields = [
        FieldSpec("prn", "PRN", True, kind="scholar", aliases=("prn no", "prn number", "scholar prn"),
                  example="PRN2026001"),
        FieldSpec("course_code", "Course code", True, kind="course", aliases=("course", "subject code"),
                  example="SIS7002"),
        *_semester_fields(),
        FieldSpec("section", "Section", aliases=("section code", "division", "batch"), example="A", max_length=10),
    ]

    def natural_key(self, v):
        return (v["prn"].prn.upper(), v["course_code"].code, v["academic_year"].upper(), v["term"])

    def resolve(self, v):
        _offering(v)

    def existing(self, v):
        e = ScholarCourseEnrollment.objects.filter(scholar=v["prn"], offering=v["_offering"]).first()
        if e:
            if e.status == ScholarCourseEnrollment.Status.WITHDRAWN:
                raise RowProblem(CONFLICT, f"{v['prn'].prn} withdrew from this offering; re-enrollment is not "
                                           "done by import")
            raise RowProblem(DUPLICATE, f"{v['prn'].prn} is already enrolled")

    def apply(self, actor, v, options):
        e = enroll(actor, v["prn"], v["_offering"], section=v["_section"])
        return [_ref(e)], []


class AssignmentImport(ImportType):
    key = "assignments"
    label = "Faculty teaching assignments"
    action = "academic.faculty_assignment.manage"
    description = "Assigns existing faculty to existing offerings / sections, with the approval basis recorded."
    fields = [
        FieldSpec("faculty_email", "Faculty e-mail", True, kind="faculty",
                  aliases=("faculty", "email", "faculty email id", "teacher email"), example="asha.rao@dypiu.ac.in"),
        FieldSpec("course_code", "Course code", True, kind="course", aliases=("course", "subject code"),
                  example="SIS7002"),
        *_semester_fields(),
        FieldSpec("role", "Role", True, kind="choice", choices=FacultySubjectAssignment.Role,
                  value_aliases={"instructor": "INSTRUCTOR", "coordinator": "COURSE_COORDINATOR",
                                 "cc": "COURSE_COORDINATOR"}, example="INSTRUCTOR"),
        FieldSpec("section", "Section", aliases=("section code", "division", "batch"), example="A",
                  help="Required for INSTRUCTOR, empty for COURSE_COORDINATOR", max_length=10),
        FieldSpec("basis", "Approval basis", True, aliases=("order", "order no", "approval reference", "reference"),
                  example="Office order AC/2026/14", max_length=255),
        FieldSpec("valid_from", "Valid from", kind="date", example=""),
        FieldSpec("valid_to", "Valid to", kind="date", example=""),
    ]

    def natural_key(self, v):
        return (v["faculty_email"].email.lower(), v["course_code"].code, v["academic_year"].upper(), v["term"],
                v["role"], (v.get("section") or "").upper())

    def resolve(self, v):
        _offering(v)

    def existing(self, v):
        today = timezone.localdate()
        active = FacultySubjectAssignment.objects.filter(
            faculty=v["faculty_email"], offering=v["_offering"], role=v["role"], section=v["_section"],
            revoked_at__isnull=True).exclude(valid_to__lt=today)
        if active.exists():
            raise RowProblem(DUPLICATE, "This assignment is already active")

    def apply(self, actor, v, options):
        a = structure.assign_faculty(actor, faculty=v["faculty_email"], offering=v["_offering"], role=v["role"],
                                     basis=v["basis"], section=v["_section"], valid_from=v.get("valid_from"),
                                     valid_to=v.get("valid_to"))
        return [_ref(a)], []


TYPES = {t.key: t for t in (StructureImport(), CourseImport(), FacultyImport(), ScholarImport(),
                            EnrollmentImport(), AssignmentImport())}
