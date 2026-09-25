"""Shared fixture for academic tests. Fixture objects are created directly;
behaviour under test goes through the academic services."""
from datetime import time, timedelta
from itertools import count

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from core.models import Committee, CommitteeMembership, Department, Designation, Faculty, School
from coursework.models import (AcademicYear, Assessment, Course, CourseOffering, CourseResult, ElectiveList,
                               ElectiveListItem, Exam, ExamCycle, FacultySubjectAssignment, FeeClearance, Section,
                               Semester, SemesterRegistration)
from coursework.seed import seed_courses
from identity import services as identity_services
from identity.capabilities import Capability as C
from identity.capabilities import ScopeType as S
from identity.models import CapabilityAssignment, Person
from scholars.models import Scholar

from . import activities, config, electives, examination, marks, results, structure
from .enrollment import enroll

_n = count(1)
TODAY = timezone.localdate()


class AcademicFixture(TestCase):
    def setUp(self):
        seed_courses()
        self.s1 = School.objects.create(code="S1", name="School One")
        self.s2 = School.objects.create(code="S2", name="School Two")
        self.d1 = Department.objects.create(school=self.s1, code="D1", name="Dept One")
        self.d2 = Department.objects.create(school=self.s1, code="D2", name="Dept Two")
        self.d3 = Department.objects.create(school=self.s2, code="D3", name="Dept Three")
        self.sysadmin = identity_services.bootstrap_system_admin(
            full_name="Sys", email="sys@dypiu.ac.in", basis="bootstrap").person

        self.acad = self.staff(C.ACADEMIC_ADMIN)
        self.cisr = self.staff(C.CISR_OPERATOR)
        self.rnd = self.staff(C.RND_CELL_OPERATOR)
        self.rnd2 = self.staff(C.RND_CELL_OPERATOR)
        self.coe = self.staff(C.COE_OPERATOR)
        self.dean = self.staff(C.DEAN_RND)
        self.vc = self.staff(C.VC_OPERATOR)
        self.phd = self.staff(C.PHD_CELL_OPERATOR)
        self.dept_admin = self.staff(C.DEPARTMENT_ADMIN, S.DEPARTMENT, department=self.d1)

        self.coord = self.faculty(self.d1)
        self.inst_a = self.faculty(self.d1)
        self.inst_b = self.faculty(self.d2)
        self.outsider = self.faculty(self.d3)
        self.sdrc = self.faculty(self.d1)
        committee = Committee.objects.create(type="SDRC", name="SDRC S1", school=self.s1)
        CommitteeMembership.objects.create(committee=committee, faculty=self.sdrc.faculty_profile)
        self.sdrc_s2 = self.faculty(self.d3)
        committee2 = Committee.objects.create(type="SDRC", name="SDRC S2", school=self.s2)
        CommitteeMembership.objects.create(committee=committee2, faculty=self.sdrc_s2.faculty_profile)

        self.sch1, self.p_sch1 = self.scholar(self.d1)
        self.sch2, self.p_sch2 = self.scholar(self.d1)
        self.sch3, self.p_sch3 = self.scholar(self.d3, entry_qualification="BTECH")

        self.year = AcademicYear.objects.create(code="AY", start_date=TODAY - timedelta(days=200),
                                                end_date=TODAY + timedelta(days=200))
        self.sem = Semester.objects.create(academic_year=self.year, term="JUL_DEC", name="Current",
                                           start_date=TODAY - timedelta(days=100), end_date=TODAY + timedelta(days=60),
                                           registration_opens=TODAY - timedelta(days=10),
                                           registration_closes=TODAY + timedelta(days=10))
        for scholar in (self.sch1, self.sch2, self.sch3):
            admit(scholar, self.sem, self.phd)
        self.stats = Course.objects.get(code="SIS7002")
        self.offering = CourseOffering.objects.create(course=self.stats, semester=self.sem, status="OPEN")
        self.sec_a = Section.objects.create(offering=self.offering, code="A")
        self.sec_b = Section.objects.create(offering=self.offering, code="B")
        self.assign(self.coord, self.offering, "COURSE_COORDINATOR")
        self.assign(self.inst_a, self.offering, "INSTRUCTOR", self.sec_a)
        self.assign(self.inst_b, self.offering, "INSTRUCTOR", self.sec_b)
        structure.define_default_assessments(self.acad, self.offering)
        self.ca = self.offering.assessments.get(kind=Assessment.Kind.CONTINUOUS)
        self.et = self.offering.assessments.get(kind=Assessment.Kind.END_TERM)
        self.cycle = ExamCycle.objects.create(
            semester=self.sem, name="Cycle", registration_opens=TODAY - timedelta(days=5),
            registration_closes=TODAY + timedelta(days=5), exam_start=TODAY + timedelta(days=10),
            exam_end=TODAY + timedelta(days=20))
        self.exam = Exam.objects.create(cycle=self.cycle, course=self.stats, scheduled_on=TODAY + timedelta(days=12),
                                        start_time=time(10), end_time=time(13))

    # --- builders ---
    def staff(self, cap, scope=S.INSTITUTION, **target):
        i = next(_n)
        user = get_user_model().objects.create_user(f"st{i}")  # no password: fast fixture
        p = Person.objects.create(full_name=f"Staff {i}", email=f"st{i}@dypiu.ac.in", user=user)
        CapabilityAssignment.objects.create(person=p, capability=cap, scope_type=scope, valid_from=TODAY,
                                            basis="fixture", granted_by=self.sysadmin, **target)
        return p

    def faculty(self, dept):
        i = next(_n)
        fac = Faculty.objects.create(name=f"Prof {i}", designation=Designation.PROFESSOR, department=dept)
        return Person.objects.create(full_name=f"Prof {i}", email=f"f{i}@dypiu.ac.in", faculty_profile=fac)

    def scholar(self, dept, entry_qualification="MTECH", category="FT"):
        i = next(_n)
        s = Scholar.objects.create(prn=f"P{i:04d}", name=f"Scholar {i}", gender="M", category=category,
                                   entry_qualification=entry_qualification, department=dept,
                                   admission_date=TODAY - timedelta(days=90),
                                   registration_date=TODAY - timedelta(days=60))
        p = Person.objects.create(full_name=s.name, email=f"s{i}@dypiu.ac.in", scholar_profile=s)
        return s, p

    def assign(self, person, offering, role, section=None, valid_to=None, valid_from=None):
        return FacultySubjectAssignment.objects.create(
            faculty=person.faculty_profile, offering=offering, section=section, role=role,
            valid_from=valid_from or TODAY - timedelta(days=30), valid_to=valid_to, basis="fixture")

    def next_exam(self, course=None, days=12):
        """A further cycle + exam (re-attempts sit a later exam)."""
        i = next(_n)
        year = AcademicYear.objects.create(code=f"AY{i}", start_date=TODAY - timedelta(days=200),
                                           end_date=TODAY + timedelta(days=400))
        sem = Semester.objects.create(academic_year=year, term="JAN_JUN", name=f"Later {i}",
                                      start_date=TODAY - timedelta(days=10), end_date=TODAY + timedelta(days=300),
                                      registration_opens=TODAY - timedelta(days=10),
                                      registration_closes=TODAY + timedelta(days=10))
        for scholar in Scholar.objects.filter(semester_registrations__semester=self.sem).distinct():
            admit(scholar, sem, self.phd)
        cycle = ExamCycle.objects.create(semester=sem, name=f"Cycle {i}", registration_opens=TODAY - timedelta(days=1),
                                         registration_closes=TODAY + timedelta(days=1),
                                         exam_start=TODAY + timedelta(days=2), exam_end=TODAY + timedelta(days=200))
        return Exam.objects.create(cycle=cycle, course=course or self.stats, scheduled_on=TODAY + timedelta(days=days),
                                   start_time=time(10), end_time=time(13))

    def enroll(self, scholar, section=None, offering=None):
        if offering is None:
            offering, section = self.offering, section or self.sec_a
        return enroll(self.phd, scholar, offering, section=section)

    def register(self, enrollment, exam=None):
        return examination.register_for_exam(self.phd, enrollment, exam or self.exam)

    def grade(self, attempt, ca=None, et=None, absent=False, coordinator=None):
        coordinator = coordinator or self.coord
        if ca is not None:
            marks.enter_mark(coordinator, self.ca, attempt, marks=ca)
        if absent:
            marks.enter_mark(coordinator, self.et, attempt, absent=True)
        elif et is not None:
            marks.enter_mark(coordinator, self.et, attempt, marks=et)

    def ratify(self, attempt) -> CourseResult:
        r = results.prepare_result(self.coord, attempt)
        results.verify_result(self.rnd, r)
        return results.ratify_result(self.coe, r, acknowledge_provisional=True)

    def configure(self, key, value, status="CONFIGURABLE"):
        """Institutional decision for a test: proposed by Academic Admin, approved by Dean R&D."""
        change = config.propose_change(self.acad, key, value=value, status=status, source="test decision",
                                       reason="test")
        return config.decide_change(self.dean, change, approve=True)

    def approved_list(self, course, semester=None, department=None):
        return approved_elective_list(course, semester or self.sem, department or self.d1, self.dept_admin)

    def full_attempt(self, enrollment, ca, et, absent=False, exam=None):
        reg = self.register(enrollment, exam)
        self.grade(reg.attempt, ca=ca, et=et, absent=absent)
        return self.ratify(reg.attempt)


# --- module-level helpers (also used by other apps' tests) --------------------------------

def admit(scholar, semester, recorded_by):
    """Fixture shortcut: fee clearance + semester registration."""
    clearance = FeeClearance.objects.create(scholar=scholar, semester=semester, cleared=True, reference="fixture",
                                            recorded_by=recorded_by)
    return SemesterRegistration.objects.create(scholar=scholar, semester=semester, fee_clearance=clearance,
                                               registered_by=recorded_by)


def approved_elective_list(course, semester, department, prepared_by):
    lst = ElectiveList.objects.create(department=department, semester=semester, prepared_by=prepared_by,
                                      status=ElectiveList.Status.APPROVED, approval_chain=["fixture"])
    ElectiveListItem.objects.create(elective_list=lst, course=course)
    return lst


def _staff(cap, granted_by, scope=S.INSTITUTION, **target):
    i = next(_n)
    p = Person.objects.create(full_name=f"Staff {i}", email=f"cw{i}@dypiu.ac.in")
    CapabilityAssignment.objects.create(person=p, capability=cap, scope_type=scope, valid_from=TODAY,
                                        basis="fixture", granted_by=granted_by, **target)
    return p


def complete_coursework(scholar, *, marks_awarded=85):
    """Complete a scholar's whole coursework through the AUTHORITATIVE academic
    services only (for integration tests such as the lifecycle journey)."""
    from core.models import Committee, CommitteeMembership, Designation, Faculty
    from .records import academic_profile

    root = Person.objects.filter(capability_assignments__capability=C.SYSTEM_ADMIN).first() or \
        identity_services.bootstrap_system_admin(full_name="Sys", email=f"sys{next(_n)}@dypiu.ac.in",
                                                 basis="bootstrap").person
    acad, dean, phd, rnd, coe = (_staff(c, root) for c in (C.ACADEMIC_ADMIN, C.DEAN_RND, C.PHD_CELL_OPERATOR,
                                                           C.RND_CELL_OPERATOR, C.COE_OPERATOR))
    dept_admin = _staff(C.DEPARTMENT_ADMIN, root, S.DEPARTMENT, department=scholar.department)
    coord = Person.objects.create(full_name="Coordinator", email=f"co{next(_n)}@dypiu.ac.in",
                                  faculty_profile=Faculty.objects.create(name="Coordinator", department=scholar.department,
                                                                         designation=Designation.PROFESSOR))
    sdrc = Person.objects.create(full_name="SDRC", email=f"sd{next(_n)}@dypiu.ac.in",
                                 faculty_profile=Faculty.objects.create(name="SDRC", department=scholar.department,
                                                                        designation=Designation.PROFESSOR))
    committee = Committee.objects.create(type="SDRC", name=f"SDRC {next(_n)}", school=scholar.department.school)
    CommitteeMembership.objects.create(committee=committee, faculty=sdrc.faculty_profile)
    me = getattr(scholar, "person", None) or Person.objects.create(
        full_name=scholar.name, email=f"sc{next(_n)}@dypiu.ac.in", scholar_profile=scholar)

    def decide(key, value):
        config.decide_change(dean, config.propose_change(acad, key, value=value, status="CONFIGURABLE",
                                                         source="test decision", reason="test"), approve=True)
    for activity in ("INDUSTRIAL_TRAINING", "CONFERENCE_WORKSHOP", "RESEARCH_SEMINAR"):
        if config.get(f"activity.scoring_scheme.{activity}").unresolved:
            decide(f"activity.scoring_scheme.{activity}", [["SDRC evaluation", 100, 100]])
    if config.get("elective.approval_authority").unresolved:
        decide("elective.approval_authority", ["DEAN_RND"])
    if config.get("elective.supervisor_recommendation_required").unresolved:
        decide("elective.supervisor_recommendation_required", False)

    i = next(_n)
    year = AcademicYear.objects.create(code=f"CW{i}", start_date=TODAY - timedelta(days=200),
                                       end_date=TODAY + timedelta(days=200))
    sem = Semester.objects.create(academic_year=year, term="JUL_DEC", name=f"CW {i}",
                                  start_date=TODAY - timedelta(days=100), end_date=TODAY + timedelta(days=100),
                                  registration_opens=TODAY - timedelta(days=5), registration_closes=TODAY + timedelta(days=5))
    admit(scholar, sem, phd)
    cycle = ExamCycle.objects.create(semester=sem, name=f"CW cycle {i}", registration_opens=TODAY - timedelta(days=5),
                                     registration_closes=TODAY + timedelta(days=5),
                                     exam_start=TODAY + timedelta(days=10), exam_end=TODAY + timedelta(days=40))

    courses = list(Course.objects.filter(category="MANDATORY", required_for_all=True, is_active=True))
    for n in range(academic_profile(scholar)["electives_required"]):
        elective = Course.objects.create(code=f"EL{i}{n}", title=f"Elective {n}", credits=3, category="ELECTIVE",
                                         required_for_all=False)
        approved_elective_list(elective, sem, scholar.department, dept_admin)
        proposal = electives.propose(me, scholar, semester=sem, course=elective, justification="research need")
        electives.decide(dean, proposal, approve=True)
        courses.append(elective)
    for day, course in enumerate(courses):
        off = CourseOffering.objects.create(course=course, semester=sem, status="OPEN")
        structure.define_default_assessments(acad, off)
        FacultySubjectAssignment.objects.create(faculty=coord.faculty_profile, offering=off, role="COURSE_COORDINATOR",
                                                valid_from=TODAY - timedelta(days=1), basis="fixture")
        e = enroll(phd, scholar, off)
        exam = Exam.objects.create(cycle=cycle, course=course, scheduled_on=TODAY + timedelta(days=10 + day),
                                   start_time=time(10), end_time=time(13))
        attempt = examination.register_for_exam(phd, e, exam).attempt
        for a in off.assessments.all():
            if a.kind == Assessment.Kind.MANDATORY_MODULE:
                marks.enter_mark(coord, a, attempt, cleared=True)
            else:
                marks.enter_mark(coord, a, attempt, marks=marks_awarded)
        r = results.prepare_result(coord, attempt)
        results.verify_result(rnd, r)
        results.ratify_result(coe, r, acknowledge_provisional=True)
    required = {"INDUSTRIAL_TRAINING": ["PLAN", "LOGBOOK", "REPORT", "POSTER"],
                "CONFERENCE_WORKSHOP": ["REFLECTIVE_NOTE"], "RESEARCH_SEMINAR": ["PRESENTATION"]}
    for course in Course.objects.filter(category="ACTIVITY", required_for_all=True, is_active=True):
        off = CourseOffering.objects.create(course=course, semester=sem, status="OPEN")
        structure.define_activity_assessments(acad, off)
        e = enroll(phd, scholar, off)
        for artefact in required[course.activity_type]:
            activities.review_artefact(sdrc, activities.submit_artefact(me, e, artefact=artefact), accept=True)
        attempt = examination.open_activity_evaluation(sdrc, e)
        marks.enter_mark(sdrc, off.assessments.get(), attempt, marks=marks_awarded)
        r = results.prepare_result(sdrc, attempt)
        results.verify_result(rnd, r)
        results.ratify_result(coe, r, acknowledge_provisional=True)
    scholar.refresh_from_db()
    return scholar
