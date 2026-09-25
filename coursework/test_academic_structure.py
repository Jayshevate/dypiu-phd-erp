from datetime import time, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from coursework.academic import structure
from coursework.academic.testing import TODAY, AcademicFixture
from coursework.models import AcademicYear, Assessment, Course, CourseOffering, FacultySubjectAssignment, Section, Semester
from identity.models import AuditEvent


class CourseCatalogueTests(AcademicFixture):
    def test_seed_has_three_taught_and_three_activity_components(self):
        self.assertEqual(Course.objects.filter(category="MANDATORY").count(), 3)
        acts = Course.objects.filter(category="ACTIVITY")
        self.assertEqual(acts.count(), 3)
        self.assertTrue(all(c.evaluation_body == "SDRC" and c.activity_type for c in acts))
        self.assertTrue(Course.objects.get(code="SIS7001").has_ethics_submodule)

    def test_create_course_authorised_and_audited(self):
        c = structure.create_course(self.cisr, code="SIS7101", title="Elective X", credits=3, category="ELECTIVE",
                                    applicable_categories=["I", "III"], department=self.d1)
        self.assertEqual((c.evaluation_body, c.required_for_all), ("COURSE_COORDINATOR", False))
        self.assertTrue(AuditEvent.objects.filter(action="academic.structure.manage.create", allowed=True).exists())
        for actor in (self.coord, self.dept_admin, self.coe, self.p_sch1, self.sysadmin):
            with self.assertRaises(PermissionDenied):
                structure.create_course(actor, code=f"X{actor.pk}", title="t", credits=3, category="ELECTIVE")

    def test_invalid_credit_and_activity_consistency_blocked_in_db(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Course.objects.create(code="BAD1", title="t", credits=0, category="MANDATORY")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Course.objects.create(code="BAD2", title="t", credits=2, category="ACTIVITY")  # no activity type / SDRC
        with self.assertRaises(IntegrityError), transaction.atomic():
            Course.objects.create(code="BAD3", title="t", credits=2, category="MANDATORY", evaluation_body="SDRC")


class StructureTests(AcademicFixture):
    def test_academic_dates_validated(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            AcademicYear.objects.create(code="BAD", start_date=TODAY, end_date=TODAY - timedelta(days=1))
        with self.assertRaises(ValidationError):
            structure.create_semester(self.acad, academic_year=self.year, term="JAN_JUN", name="x",
                                      start_date=self.year.start_date - timedelta(days=1), end_date=TODAY)

    def test_offering_and_section_uniqueness(self):
        with self.assertRaises(ValidationError):
            structure.create_offering(self.acad, course=self.stats, semester=self.sem)
        with self.assertRaises(ValidationError):
            structure.create_section(self.acad, offering=self.offering, code="A")
        sec = structure.create_section(self.acad, offering=self.offering, code="C")
        self.assertEqual(sec.offering, self.offering)

    def test_course_offering_section_assignment_chain(self):
        c = structure.create_course(self.cisr, code="SIS7102", title="E", credits=3, category="ELECTIVE",
                                    department=self.d1)
        off = structure.create_offering(self.acad, course=c, semester=self.sem)
        sec = structure.create_section(self.acad, offering=off, code="A")
        a = structure.assign_faculty(self.dept_admin, faculty=self.inst_a.faculty_profile, offering=off, section=sec,
                                     role="INSTRUCTOR", basis="order 12")
        self.assertEqual((a.offering, a.section, a.faculty), (off, sec, self.inst_a.faculty_profile))


class FacultyAssignmentTests(AcademicFixture):
    def test_one_coordinator_and_no_duplicate_instructor(self):
        with self.assertRaises(ValidationError):
            structure.assign_faculty(self.acad, faculty=self.inst_a.faculty_profile, offering=self.offering,
                                     role="COURSE_COORDINATOR", basis="x")
        with self.assertRaises(ValidationError):
            structure.assign_faculty(self.acad, faculty=self.inst_a.faculty_profile, offering=self.offering,
                                     section=self.sec_a, role="INSTRUCTOR", basis="x")

    def test_role_section_consistency_in_db(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            FacultySubjectAssignment.objects.create(faculty=self.outsider.faculty_profile, offering=self.offering,
                                                    role="INSTRUCTOR", valid_from=TODAY, basis="x")

    def test_department_admin_cross_scope_denied(self):
        other = structure.create_course(self.cisr, code="SIS7103", title="E", credits=3, category="ELECTIVE",
                                        department=self.d3)
        off = structure.create_offering(self.acad, course=other, semester=self.sem)
        with self.assertRaises(PermissionDenied):
            structure.assign_faculty(self.dept_admin, faculty=self.outsider.faculty_profile, offering=off,
                                     role="COURSE_COORDINATOR", basis="x")

    def test_activity_components_have_no_teaching_assignment(self):
        act = CourseOffering.objects.create(course=Course.objects.get(code="SIS7006"), semester=self.sem)
        with self.assertRaises(ValidationError):
            structure.assign_faculty(self.acad, faculty=self.coord.faculty_profile, offering=act,
                                     role="COURSE_COORDINATOR", basis="x")


class AssessmentSchemeTests(AcademicFixture):
    def test_default_50_50_from_configuration(self):
        self.assertEqual((self.ca.weight, self.et.weight), (Decimal("50"), Decimal("50")))

    def test_ethics_module_added_for_research_methodology(self):
        off = CourseOffering.objects.create(course=Course.objects.get(code="SIS7001"), semester=self.sem)
        rows = structure.define_default_assessments(self.acad, off)
        kinds = sorted(r.kind for r in rows)
        self.assertEqual(kinds, ["CONTINUOUS", "END_TERM", "MANDATORY_MODULE"])

    def test_activity_scheme_comes_only_from_approved_configuration(self):
        off = CourseOffering.objects.create(course=Course.objects.get(code="SIS7005"), semester=self.sem)
        with self.assertRaises(ValidationError):
            structure.define_default_assessments(self.acad, off)
        with self.assertRaisesMessage(ValidationError, "unresolved"):          # nothing invented
            structure.define_activity_assessments(self.acad, off)
        self.configure("activity.scoring_scheme.INDUSTRIAL_TRAINING", [["Report", 60, 100], ["Poster", 30, 100]])
        with self.assertRaisesMessage(ValidationError, "total 100"):
            structure.define_activity_assessments(self.acad, off)
        self.configure("activity.scoring_scheme.INDUSTRIAL_TRAINING", [["Report", 70, 100], ["Poster", 30, 100]])
        with self.assertRaises(PermissionDenied):   # institution-level course: outside a school SDRC's scope
            structure.define_activity_assessments(self.sdrc, off)
        rows = structure.define_activity_assessments(self.acad, off)
        self.assertEqual(len(rows), 2)

    def test_elective_reweighting_within_limit_only(self):
        c = Course.objects.create(code="SIS7104", title="E", credits=3, category="ELECTIVE", required_for_all=False)
        off = CourseOffering.objects.create(course=c, semester=self.sem)
        structure.define_default_assessments(self.acad, off)
        with self.assertRaisesMessage(ValidationError, "unresolved"):          # approver not decided
            structure.reweight_elective(self.dean, off, continuous_weight=Decimal("60"), approval_reference="DC/1")
        self.configure("assessment.elective_reweighting_approver", ["DEAN_RND"])
        with self.assertRaises(PermissionDenied):
            structure.reweight_elective(self.acad, off, continuous_weight=Decimal("60"), approval_reference="DC/1")
        with self.assertRaises(ValidationError):
            structure.reweight_elective(self.dean, off, continuous_weight=Decimal("65"), approval_reference="DC/1")
        with self.assertRaises(ValidationError):
            structure.reweight_elective(self.dean, off, continuous_weight=Decimal("55"), approval_reference="")
        structure.reweight_elective(self.dean, off, continuous_weight=Decimal("60"), approval_reference="DC/1")
        weights = dict(off.assessments.values_list("kind", "weight"))
        self.assertEqual((weights["CONTINUOUS"], weights["END_TERM"]), (Decimal("60"), Decimal("40")))
        with self.assertRaises(ValidationError):  # taught courses cannot be re-weighted
            structure.reweight_elective(self.dean, self.offering, continuous_weight=Decimal("55"),
                                        approval_reference="x")

    def test_impossible_assessment_definitions_blocked_in_db(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Assessment.objects.create(offering=self.offering, name="bad", kind="CONTINUOUS", weight=0,
                                      max_marks=100)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Assessment.objects.create(offering=self.offering, name="bad2", kind="MANDATORY_MODULE", weight=10)
