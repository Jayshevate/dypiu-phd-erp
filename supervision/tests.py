from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase

from core import testing as t
from core.models import Designation
from supervision.services import current_supervisor, propose_supervisor, propose_tac, tac_complete


class SupervisorCapacityTests(TestCase):
    def setUp(self):
        t.seed()

    def test_assistant_professor_capped_at_four(self):
        prof = t.faculty(Designation.ASSISTANT_PROFESSOR)
        for _ in range(4):
            propose_supervisor(t.scholar(), prof)
        with self.assertRaisesMessage(ValidationError, "at capacity (4"):
            propose_supervisor(t.scholar(), prof)

    def test_co_supervision_counts_towards_cap(self):
        prof = t.faculty(Designation.ASSOCIATE_PROFESSOR)
        for _ in range(5):
            propose_supervisor(t.scholar(), prof)
        propose_supervisor(t.scholar(), prof, kind="CO_SUPERVISOR")
        with self.assertRaises(ValidationError):
            propose_supervisor(t.scholar(), prof, kind="CO_SUPERVISOR")

    def test_ended_assignment_frees_a_seat(self):
        prof = t.faculty(Designation.ASSISTANT_PROFESSOR)
        assignments = [propose_supervisor(t.scholar(), prof) for _ in range(4)]
        assignments[0].end_date = date(2027, 1, 1)
        assignments[0].save()
        propose_supervisor(t.scholar(), prof)

    def test_external_cannot_be_main_supervisor(self):
        ext = t.faculty(Designation.OTHER, is_external=True)
        with self.assertRaisesMessage(ValidationError, "co-supervisors"):
            propose_supervisor(t.scholar(), ext)
        propose_supervisor(t.scholar(), ext, kind="CO_SUPERVISOR")

    def test_service_remaining(self):
        prof = t.faculty(superannuation_date=date(2028, 1, 1))
        with self.assertRaisesMessage(ValidationError, "years of service"):
            propose_supervisor(t.scholar(), prof, on=date(2026, 8, 1))

    def test_one_supervisor_per_scholar(self):
        s = t.scholar()
        propose_supervisor(s, t.faculty())
        with self.assertRaisesMessage(ValidationError, "change-of-supervisor"):
            propose_supervisor(s, t.faculty())

    def test_approval_activates_rejection_ends(self):
        s1, s2 = t.scholar(), t.scholar()
        a1 = propose_supervisor(s1, t.faculty())
        self.assertIsNone(current_supervisor(s1))
        t.approve_all(a1.approval)
        self.assertIsNotNone(current_supervisor(s1))
        a2 = propose_supervisor(s2, t.faculty())
        from core.approvals import decide
        from core.roles import Role
        decide(a2.approval, t.user(Role.SDRC), False)
        a2.refresh_from_db()
        self.assertIsNotNone(a2.end_date)


class TACTests(TestCase):
    def setUp(self):
        t.seed()
        self.dept = t.department()
        self.other_dept = t.department()
        self.sup = t.faculty(dept=self.dept)
        self.scholar = t.scholar(dept=self.dept)
        t.approve_all(propose_supervisor(self.scholar, self.sup).approval)

    def members(self, n=2):
        return [t.faculty(dept=self.other_dept) for _ in range(n)]

    def test_happy_path(self):
        req = propose_tac(self.scholar, self.members())
        self.assertFalse(tac_complete(self.scholar))
        t.approve_all(req)
        self.assertTrue(tac_complete(self.scholar))

    def test_needs_approved_supervisor(self):
        s = t.scholar()
        with self.assertRaisesMessage(ValidationError, "DC-approved supervisor"):
            propose_tac(s, self.members())

    def test_exactly_two_members(self):
        with self.assertRaises(ValidationError):
            propose_tac(self.scholar, self.members(3))

    def test_co_supervisor_excluded(self):
        co = t.faculty(dept=self.other_dept)
        propose_supervisor(self.scholar, co, kind="CO_SUPERVISOR")
        with self.assertRaisesMessage(ValidationError, "cannot sit on the TAC"):
            propose_tac(self.scholar, [co, t.faculty(dept=self.other_dept)])

    def test_interdisciplinary(self):
        with self.assertRaisesMessage(ValidationError, "interdisciplinary"):
            propose_tac(self.scholar, [t.faculty(dept=self.dept), t.faculty(dept=self.other_dept)])

    def test_member_unique_across_same_supervisors_scholars(self):
        shared = t.faculty(dept=self.other_dept)
        t.approve_all(propose_tac(self.scholar, [shared, t.faculty(dept=self.other_dept)]))
        sibling = t.scholar(dept=self.dept)
        t.approve_all(propose_supervisor(sibling, self.sup).approval)
        with self.assertRaisesMessage(ValidationError, "another scholar of"):
            propose_tac(sibling, [shared, t.faculty(dept=self.other_dept)])
        # A scholar of a different supervisor may use the same member.
        unrelated = t.scholar(dept=self.dept)
        t.approve_all(propose_supervisor(unrelated, t.faculty(dept=self.dept)).approval)
        propose_tac(unrelated, [shared, t.faculty(dept=self.other_dept)])
