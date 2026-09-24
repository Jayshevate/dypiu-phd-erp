from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase

from core import testing as t
from core.approvals import start_approval
from coursework.models import AssessmentComponent, ComponentScore, Course
from coursework.services import evaluate, record_result, register_attempt


def pass_core(scholar, exam=date(2026, 12, 5), marks=75):
    for course in Course.objects.filter(required_for_all=True):
        a = register_attempt(scholar, course, exam)
        record_result(a, marks=marks, ethics_cleared=True if course.has_ethics_submodule else None)


class CourseworkTests(TestCase):
    def setUp(self):
        t.seed()
        self.scholar = t.scholar(entry_qualification="MTECH")
        self.elective = Course.objects.create(code="SIS7008-AI", title="Elective: AI", credits=3,
                                              category="ELECTIVE", required_for_all=False)

    def test_completion_requires_credits(self):
        pass_core(self.scholar)
        self.scholar.refresh_from_db()
        self.assertIsNone(self.scholar.coursework_completed_on)
        self.assertIn("credits earned 14 < required 17", evaluate(self.scholar).reasons)
        record_result(register_attempt(self.scholar, self.elective, date(2027, 5, 5)), marks=65)
        self.scholar.refresh_from_db()
        self.assertEqual(self.scholar.coursework_completed_on, date(2027, 5, 5))

    def test_ethics_result_required_for_sis7001(self):
        a = register_attempt(self.scholar, Course.objects.get(code="SIS7001"), date(2026, 12, 5))
        with self.assertRaisesMessage(ValidationError, "ethics"):
            record_result(a, marks=80)

    def test_third_attempt_needs_vc_override(self):
        course = Course.objects.get(code="SIS7002")
        record_result(register_attempt(self.scholar, course, date(2026, 12, 5)), marks=45)
        record_result(register_attempt(self.scholar, course, date(2027, 5, 5)), special_grade="AB")
        with self.assertRaisesMessage(ValidationError, "override"):
            register_attempt(self.scholar, course, date(2027, 12, 5))
        override = start_approval("COURSEWORK_THIRD_ATTEMPT", summary="3rd", scholar=self.scholar)
        with self.assertRaises(ValidationError):
            register_attempt(self.scholar, course, date(2027, 12, 5), override=override)  # still pending
        t.approve_all(override)
        override.refresh_from_db()
        a = register_attempt(self.scholar, course, date(2027, 12, 5), override=override)
        self.assertEqual(a.attempt_no, 3)

    def test_attempt_window(self):
        course = Course.objects.get(code="SIS7003")
        record_result(register_attempt(self.scholar, Course.objects.get(code="SIS7002"), date(2026, 12, 5)), marks=70)
        with self.assertRaisesMessage(ValidationError, "window"):
            register_attempt(self.scholar, course, date(2029, 5, 5))

    def test_cannot_reattempt_passed_course(self):
        course = Course.objects.get(code="SIS7002")
        record_result(register_attempt(self.scholar, course, date(2026, 12, 5)), marks=70)
        with self.assertRaisesMessage(ValidationError, "already passed"):
            register_attempt(self.scholar, course, date(2027, 5, 5))

    def test_rubric_and_gate_component(self):
        course = Course.objects.get(code="SIS7006")
        points = AssessmentComponent.objects.create(course=course, name="Points", weight=80)
        note = AssessmentComponent.objects.create(course=course, name="Reflective note", weight=20,
                                                  is_gate=True, gate_min_percent=40)
        a = register_attempt(self.scholar, course, date(2026, 12, 5))
        ComponentScore.objects.create(attempt=a, component=points, percent=90)
        ComponentScore.objects.create(attempt=a, component=note, percent=30)
        record_result(a)
        self.assertEqual((a.marks, a.grade), (78, "F"))
        ComponentScore.objects.filter(attempt=a, component=note).update(percent=50)
        record_result(a)
        self.assertEqual((a.marks, a.grade), (82, "A"))
