from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from core import testing as t
from core.approvals import decide, resolve_chain, start_approval
from core.models import ApprovalChain, ApprovalStep
from core.roles import Role


class ApprovalChainTests(TestCase):
    def setUp(self):
        t.seed()
        self.scholar = t.scholar()

    def test_steps_run_in_order_and_enforce_roles(self):
        req = start_approval("SYNOPSIS_EXTENSION", summary="x", scholar=self.scholar)
        dean = t.user(Role.DEAN_RD)
        with self.assertRaises(PermissionDenied):
            decide(req, dean, True)  # DC must go first
        req = decide(req, t.user(Role.DC), True)
        self.assertEqual(req.status, "PENDING")
        self.assertEqual(req.pending_step.role, Role.DEAN_RD)
        req = decide(req, dean, True)
        self.assertEqual(req.status, "APPROVED")
        with self.assertRaises(ValidationError):
            decide(req, dean, True)

    def test_rejection_closes_request(self):
        req = start_approval("DEGREE_AWARD", summary="x", scholar=self.scholar)
        req = decide(req, t.user(Role.DC), False, "incomplete")
        self.assertEqual(req.status, "REJECTED")
        self.assertIsNone(req.pending_step)

    def test_own_scholar_supervisor_step(self):
        from supervision.models import SupervisorAssignment
        mine = t.faculty(with_user=True)
        other = t.faculty(with_user=True)
        SupervisorAssignment.objects.create(scholar=self.scholar, faculty=mine, kind="SUPERVISOR",
                                            start_date=self.scholar.registration_date)
        req = start_approval("GRANT_CLAIM", summary="x", scholar=self.scholar)
        with self.assertRaises(PermissionDenied):
            decide(req, other.user, True)
        req = decide(req, mine.user, True)
        self.assertEqual(req.pending_step.role, Role.RD_OFFICE)

    def test_category_specific_chain_wins(self):
        special = ApprovalChain.objects.create(code="LEAVE", scholar_category="PT_EXTERNAL", name="PT leave")
        ApprovalStep.objects.create(chain=special, order=1, role=Role.DEAN_RD)
        self.assertEqual(resolve_chain("LEAVE", t.scholar(category="PT_EXTERNAL")), special)
        self.assertNotEqual(resolve_chain("LEAVE", self.scholar), special)

    def test_unknown_chain(self):
        with self.assertRaises(ValidationError):
            start_approval("NOPE", summary="x")
