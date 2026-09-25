from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from coursework.academic import config, marks, results, structure
from coursework.academic.testing import AcademicFixture
from coursework.models import Course, CourseOffering, CourseResult, MarkEntry, MarkEntryHistory, ResultEvent


class MarksTests(AcademicFixture):
    def setUp(self):
        super().setUp()
        self.e1 = self.enroll(self.sch1, self.sec_a)
        self.e2 = self.enroll(self.sch2, self.sec_b)
        self.a1 = self.register(self.e1).attempt
        self.a2 = self.register(self.e2).attempt

    def test_instructor_enters_continuous_marks_for_own_section_only(self):
        marks.enter_mark(self.inst_a, self.ca, self.a1, marks=40)
        with self.assertRaises(PermissionDenied):
            marks.enter_mark(self.inst_a, self.ca, self.a2, marks=40)      # section B
        with self.assertRaises(PermissionDenied):
            marks.enter_mark(self.inst_a, self.et, self.a1, marks=40)      # end-term: coordinator only

    def test_coordinator_enters_all_marks(self):
        marks.enter_mark(self.coord, self.ca, self.a2, marks=30)
        marks.enter_mark(self.coord, self.et, self.a2, marks=55)

    def test_unassigned_and_other_roles_denied(self):
        for actor in (self.outsider, self.inst_b, self.p_sch1, self.sdrc, self.coe, self.rnd, self.dean,
                      self.sysadmin):
            with self.assertRaises(PermissionDenied, msg=str(actor)):
                marks.enter_mark(actor, self.et, self.a1, marks=50)

    def test_impossible_marks_rejected(self):
        with self.assertRaises(ValidationError):
            marks.enter_mark(self.coord, self.ca, self.a1, marks=101)
        with self.assertRaises(ValidationError):
            marks.enter_mark(self.coord, self.ca, self.a1, marks=-1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            MarkEntry.objects.create(assessment=self.ca, attempt=self.a1, marks=-5, entered_by=self.coord)

    def test_correction_requires_reason_and_keeps_raw_history(self):
        marks.enter_mark(self.coord, self.ca, self.a1, marks=40)
        with self.assertRaises(ValidationError):
            marks.enter_mark(self.coord, self.ca, self.a1, marks=45)
        marks.enter_mark(self.coord, self.ca, self.a1, marks=45, reason="Totalling error")
        h = MarkEntryHistory.objects.get()
        self.assertEqual((h.old_marks, h.new_marks), (Decimal("40"), Decimal("45")))

    def test_assessment_must_match_attempt_offering(self):
        other = CourseOffering.objects.create(course=Course.objects.get(code="SIS7003"), semester=self.sem)
        structure.define_default_assessments(self.acad, other)
        foreign = other.assessments.get(kind="CONTINUOUS")
        self.assign(self.coord, other, "COURSE_COORDINATOR")
        with self.assertRaises(PermissionDenied):
            marks.enter_mark(self.coord, foreign, self.a1, marks=40)


class ResultTests(AcademicFixture):
    def setUp(self):
        super().setUp()
        self.e1 = self.enroll(self.sch1, self.sec_a)
        self.a1 = self.register(self.e1).attempt

    def test_missing_marks_never_defaulted(self):
        marks.enter_mark(self.coord, self.ca, self.a1, marks=40)
        with self.assertRaisesMessage(ValidationError, "Marks missing"):
            results.prepare_result(self.coord, self.a1)
        self.assertFalse(CourseResult.objects.exists())

    def test_grade_computed_from_configured_scheme(self):
        self.grade(self.a1, ca=80, et=70)                     # 0.5*80 + 0.5*70 = 75 -> B+ (8)
        r = results.prepare_result(self.coord, self.a1)
        self.assertEqual((r.total_marks, r.grade, r.grade_point, r.outcome), (Decimal("75.00"), "B+", Decimal("8"), "PASS"))
        self.assertTrue(r.is_provisional)                     # grading.scheme is AMBIGUOUS
        self.assertEqual(r.rule_versions["grading.scheme"]["status"], "AMBIGUOUS")

    def test_below_minimum_fails(self):
        self.grade(self.a1, ca=30, et=40)
        r = results.prepare_result(self.coord, self.a1)
        self.assertEqual(r.outcome, "FAIL")

    def test_absence_blocks_until_policy_decided(self):
        e2 = self.enroll(self.sch2, self.sec_b)
        a2 = self.register(e2).attempt
        self.grade(a2, ca=60, absent=True)
        with self.assertRaisesMessage(ValidationError, "result.absence_policy"):
            results.prepare_result(self.coord, a2)
        self.assertFalse(CourseResult.objects.filter(attempt=a2).exists())      # no guessed result
        self.configure("result.absence_policy", "ABSENT_IF_ANY_COMPONENT_ABSENT")
        r2 = results.prepare_result(self.coord, a2)
        self.assertEqual((r2.outcome, r2.grade, r2.total_marks), ("ABSENT", "AB", None))

    def test_chain_of_custody_and_separation_of_duties(self):
        self.grade(self.a1, ca=70, et=70)
        r = results.prepare_result(self.coord, self.a1)
        self.assertEqual((r.prepared_by, r.prepared_as), (self.coord, "FACULTY"))
        with self.assertRaises(ValidationError):
            results.ratify_result(self.coe, r)                # must be verified first
        for actor in (self.coord, self.coe, self.dean):
            with self.assertRaises(PermissionDenied):
                results.verify_result(actor, r)
        results.verify_result(self.rnd, r)
        for actor in (self.coord, self.rnd, self.dean, self.acad):
            with self.assertRaises(PermissionDenied):
                results.ratify_result(actor, r, acknowledge_provisional=True)
        with self.assertRaisesMessage(ValidationError, "acknowledgement"):     # grading.scheme is AMBIGUOUS
            results.ratify_result(self.coe, r)
        results.ratify_result(self.coe, r, acknowledge_provisional=True)
        ev = ResultEvent.objects.filter(result=r, action="RATIFY").get()
        self.assertIn("acknowledged provisional", ev.remarks)
        self.assertEqual([e.action for e in ResultEvent.objects.filter(result=r)], ["PREPARE", "VERIFY", "RATIFY"])
        self.a1.refresh_from_db()
        self.assertEqual((self.a1.status, self.a1.grade, self.a1.grade_point), ("RESULT_RATIFIED", "B", 7))

    def test_ratified_result_is_final_and_marks_locked(self):
        self.grade(self.a1, ca=70, et=70)
        r = self.ratify(self.a1)
        r.grade = "A+"
        with self.assertRaises(PermissionError):                 # model-level immutability
            r.save()
        with self.assertRaises(PermissionError):
            r.delete()
        r.refresh_from_db()
        with self.assertRaises(PermissionDenied):
            results.return_result(self.coe, r, reason="x")
        with self.assertRaises(ValidationError):
            results.prepare_result(self.coord, self.a1)
        with self.assertRaises(ValidationError):
            marks.enter_mark(self.coord, self.et, self.a1, marks=90, reason="x")

    def test_prepared_result_locks_marks_until_returned(self):
        self.grade(self.a1, ca=70, et=70)
        r = results.prepare_result(self.coord, self.a1)
        with self.assertRaises(PermissionDenied):
            marks.enter_mark(self.coord, self.et, self.a1, marks=90, reason="x")
        results.return_result(self.rnd, r, reason="Recheck end-term")
        marks.enter_mark(self.coord, self.et, self.a1, marks=90, reason="Rechecked")
        r = results.prepare_result(self.coord, self.a1)
        self.assertEqual(r.total_marks, Decimal("80.00"))

    def test_scholar_sees_result_only_after_ratification(self):
        self.grade(self.a1, ca=70, et=70)
        results.prepare_result(self.coord, self.a1)
        self.assertIsNone(results.visible_result(self.p_sch1, self.a1))
        self.assertIsNotNone(results.visible_result(self.rnd, self.a1))
        with self.assertRaises(PermissionDenied):
            results.visible_result(self.p_sch2, self.a1)      # cross-scholar
        r = CourseResult.objects.get(attempt=self.a1)
        results.verify_result(self.rnd, r)
        results.ratify_result(self.coe, r, acknowledge_provisional=True)
        self.assertEqual(results.visible_result(self.p_sch1, self.a1), r)

    def test_ethics_module_must_be_cleared(self):
        rme = CourseOffering.objects.create(course=Course.objects.get(code="SIS7001"), semester=self.sem, status="OPEN")
        structure.define_default_assessments(self.acad, rme)
        self.assign(self.coord, rme, "COURSE_COORDINATOR")
        e = self.enroll(self.sch1, section=None, offering=rme)
        ex = self.next_exam(course=rme.course)
        a = self.register(e, ex).attempt
        ca, et, module = [rme.assessments.get(kind=k) for k in ("CONTINUOUS", "END_TERM", "MANDATORY_MODULE")]
        marks.enter_mark(self.coord, ca, a, marks=90)
        marks.enter_mark(self.coord, et, a, marks=90)
        with self.assertRaises(ValidationError):
            marks.enter_mark(self.coord, module, a, marks=50)          # cleared/not cleared only
        marks.enter_mark(self.coord, module, a, cleared=False)
        r = results.prepare_result(self.coord, a)
        self.assertEqual(r.outcome, "FAIL")
        self.assertIn("Mandatory module not cleared", r.reasons[0])

    def test_configuration_change_applies_to_new_results_only(self):
        self.grade(self.a1, ca=40, et=40)
        r1 = results.prepare_result(self.coord, self.a1)
        self.assertEqual(r1.outcome, "PASS")                  # 40 meets the minimum of 40
        self.configure("coursework.course_pass_min_marks", "45")
        results.return_result(self.rnd, r1, reason="recompute")
        r2 = results.prepare_result(self.coord, self.a1)
        self.assertEqual(r2.outcome, "FAIL")
        self.assertEqual(r2.rule_versions["coursework.course_pass_min_marks"]["version"], 2)

    def test_relative_grading_blocks_until_method_defined(self):
        scheme = config.get("grading.scheme").value | {"mode": "RELATIVE"}
        self.configure("grading.scheme", scheme, status="AMBIGUOUS")
        self.grade(self.a1, ca=70, et=70)
        with self.assertRaisesMessage(ValidationError, "RD-01"):
            results.prepare_result(self.coord, self.a1)
