from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase

from core import testing as t
from finance.models import GrantClaim, Semester, TAFeedback
from finance.services import assign_ta, grant_balance, register_for_ta, submit_grant_claim


class TATests(TestCase):
    def setUp(self):
        t.seed()
        self.s = t.scholar()
        self.odd = Semester.objects.create(code="2026-ODD", start_date=date(2026, 7, 1), end_date=date(2026, 11, 30),
                                           fee_deadline=date(2026, 7, 15))
        self.even = Semester.objects.create(code="2027-EVEN", start_date=date(2027, 1, 1), end_date=date(2027, 5, 31),
                                            fee_deadline=date(2027, 1, 15))
        self.fac = t.faculty()

    def assign(self, reg, hours, contact):
        return assign_ta(reg, school=self.fac.department.school, course_name="X", faculty=self.fac,
                         hours_per_week=hours, contact_hours_per_week=contact)

    def test_hour_caps(self):
        reg = register_for_ta(self.s, self.odd, date(2026, 7, 16))
        self.assign(reg, 12, 6)
        with self.assertRaisesMessage(ValidationError, "exceeds 20"):
            self.assign(reg, 9, 2)
        with self.assertRaisesMessage(ValidationError, "Contact load 11"):
            self.assign(reg, 8, 5)
        self.assign(reg, 8, 4)

    def test_form_deadline(self):
        with self.assertRaisesMessage(ValidationError, "within 2 days"):
            register_for_ta(self.s, self.odd, date(2026, 7, 18))

    def test_feedback_gates_next_semester(self):
        reg = register_for_ta(self.s, self.odd, date(2026, 7, 10))
        with self.assertRaisesMessage(ValidationError, "missing"):
            register_for_ta(self.s, self.even, date(2027, 1, 10))
        for src in TAFeedback.Source:
            TAFeedback.objects.create(registration=reg, source=src, rating=2, satisfactory=src != "STUDENTS")
        with self.assertRaisesMessage(ValidationError, "not satisfactory"):
            register_for_ta(self.s, self.even, date(2027, 1, 10))
        reg.feedback.update(satisfactory=True)
        register_for_ta(self.s, self.even, date(2027, 1, 10))


class GrantTests(TestCase):
    def setUp(self):
        t.seed()
        self.s = t.scholar()

    def test_lifetime_cap_and_gap(self):
        c1 = submit_grant_claim(self.s, "Conf A", date(2027, 3, 1), 30000)
        t.approve_all(c1.approval)
        c1.refresh_from_db()
        self.assertEqual(c1.state, GrantClaim.State.APPROVED)
        with self.assertRaisesMessage(ValidationError, "6 months"):
            submit_grant_claim(self.s, "Conf B", date(2027, 8, 31), 1000)
        with self.assertRaisesMessage(ValidationError, "exceeds remaining"):
            submit_grant_claim(self.s, "Conf B", date(2027, 9, 1), 25000)
        submit_grant_claim(self.s, "Conf B", date(2027, 9, 1), 20000)
        self.assertEqual(grant_balance(self.s), 0)
