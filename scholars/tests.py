from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from core import testing as t
from core.roles import Role
from scholars.models import AdmissionCycle, Application, ExtensionGrant, LeaveRecord
from scholars.services import apply_for_leave, request_extension
from supervision.services import propose_supervisor


class ApplicationTests(TestCase):
    def test_rpet_exemption_and_interview(self):
        cycle = AdmissionCycle.objects.create(name="2026-SEP")
        app = Application(cycle=cycle, name="A", email="a@x.org", category="FT", entry_qualification="BTECH",
                          department=t.department(), rpet_percent=Decimal("49.99"), interview_percent=Decimal("50"))
        self.assertFalse(app.eligible_for_selection)
        app.exemption = "gate"
        self.assertTrue(app.eligible_for_selection)
        app.interview_percent = Decimal("40")
        self.assertFalse(app.eligible_for_selection)


class ExtensionAndLeaveTests(TestCase):
    def setUp(self):
        t.seed()
        self.s = t.scholar(gender="M", pwd_percent=0)

    def test_programme_ceiling_extensions(self):
        self.assertEqual(self.s.programme_ceiling, date(2032, 8, 1))
        with self.assertRaisesMessage(ValidationError, "Relaxation"):
            request_extension(self.s, ExtensionGrant.Kind.RELAXATION)
        g = request_extension(self.s, ExtensionGrant.Kind.RE_REGISTRATION)
        self.assertEqual(g.approval.chain.steps.count(), 3)
        t.approve_all(g.approval)
        self.assertEqual(self.s.programme_ceiling, date(2034, 8, 1))
        with self.assertRaisesMessage(ValidationError, "already granted"):
            request_extension(self.s, ExtensionGrant.Kind.RE_REGISTRATION)

    def test_annual_leave_cap(self):
        apply_for_leave(self.s, LeaveRecord.Type.ANNUAL, date(2027, 1, 1), date(2027, 1, 20))
        with self.assertRaisesMessage(ValidationError, "cap 25"):
            apply_for_leave(self.s, LeaveRecord.Type.ANNUAL, date(2027, 6, 1), date(2027, 6, 6))
        apply_for_leave(self.s, LeaveRecord.Type.ANNUAL, date(2028, 6, 1), date(2028, 6, 25))

    def test_maternity_cap_and_approval(self):
        sup = t.faculty(with_user=True)
        t.approve_all(propose_supervisor(self.s, sup).approval)
        leave = apply_for_leave(self.s, LeaveRecord.Type.MATERNITY, date(2027, 1, 1), date(2027, 6, 30))
        with self.assertRaisesMessage(ValidationError, "240"):
            apply_for_leave(self.s, LeaveRecord.Type.MATERNITY, date(2028, 1, 1), date(2028, 3, 31))
        t.approve_all(leave.approval)
        leave.refresh_from_db()
        self.assertTrue(leave.approved)


class DashboardViewTests(TestCase):
    def setUp(self):
        t.seed()
        self.sup = t.faculty(with_user=True)
        self.mine = t.scholar()
        self.other = t.scholar()
        propose_supervisor(self.mine, self.sup)

    def test_supervisor_sees_only_own_scholars(self):
        self.client.force_login(self.sup.user)
        r = self.client.get("/scholars/")
        self.assertContains(r, self.mine.prn)
        self.assertNotContains(r, self.other.prn)
        self.assertEqual(self.client.get(f"/scholars/{self.other.prn}/").status_code, 403)
        self.assertContains(self.client.get(f"/scholars/{self.mine.prn}/"), "Phase 1: Admission")

    def test_oversight_role_sees_all(self):
        self.client.force_login(t.user(Role.DEAN_RD))
        r = self.client.get("/scholars/")
        self.assertContains(r, self.mine.prn)
        self.assertContains(r, self.other.prn)

    def test_scholar_redirected_to_own_page(self):
        u = t.user(Role.SCHOLAR)
        self.mine.user = u
        self.mine.save()
        self.client.force_login(u)
        self.assertContains(self.client.get("/scholars/"), "Phase 1: Admission")

    def test_login_required(self):
        self.assertEqual(self.client.get("/scholars/").status_code, 302)
