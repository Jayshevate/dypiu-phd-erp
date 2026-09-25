"""Cross-cutting academic authorization and audit tests."""
from datetime import time, timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied

from coursework.academic import attendance, examination, marks, results, structure
from coursework.academic.testing import TODAY, AcademicFixture
from identity import services as identity_services
from identity.capabilities import Capability as C
from identity.models import AuditEvent, Person


class AcademicSecurityTests(AcademicFixture):
    def setUp(self):
        super().setUp()
        self.e1 = self.enroll(self.sch1, self.sec_a)
        self.a1 = self.register(self.e1).attempt

    def test_administrative_accounts_have_no_implicit_academic_power(self):
        su = get_user_model().objects.create_superuser("root")
        root = Person.objects.create(full_name="Root", email="root@dypiu.ac.in", user=su)
        for actor in (self.sysadmin, root, self.vc):
            with self.assertRaises(PermissionDenied, msg=str(actor)):
                marks.enter_mark(actor, self.et, self.a1, marks=50)
            with self.assertRaises(PermissionDenied):
                attendance.create_session(actor, self.sec_a, date=TODAY, start_time=time(9), end_time=time(10))
        self.grade(self.a1, ca=50, et=50)
        r = results.prepare_result(self.coord, self.a1)
        for actor in (self.sysadmin, root, self.vc, self.dean, self.acad):
            with self.assertRaises(PermissionDenied, msg=str(actor)):
                results.verify_result(actor, r)

    def test_cross_department_faculty_denied_everything(self):
        with self.assertRaises(PermissionDenied):
            attendance.create_session(self.outsider, self.sec_a, date=TODAY, start_time=time(9), end_time=time(10))
        with self.assertRaises(PermissionDenied):
            marks.enter_mark(self.outsider, self.ca, self.a1, marks=50)
        with self.assertRaises(PermissionDenied):
            results.visible_result(self.outsider, self.a1)

    def test_expired_coordinator_assignment_loses_power(self):
        a = self.coord.faculty_profile.subject_assignments.get(role="COURSE_COORDINATOR")
        type(a).objects.filter(pk=a.pk).update(valid_to=TODAY - timedelta(days=1))
        with self.assertRaises(PermissionDenied):
            marks.enter_mark(self.coord, self.et, self.a1, marks=50)

    def test_revoked_assignment_loses_power(self):
        a = self.inst_a.faculty_profile.subject_assignments.get(role="INSTRUCTOR")
        structure.revoke_assignment(self.acad, a, reason="left")
        with self.assertRaises(PermissionDenied):
            marks.enter_mark(self.inst_a, self.ca, self.a1, marks=50)

    def test_privilege_escalation_via_capability_grant_blocked(self):
        with self.assertRaises(PermissionDenied):   # an academic admin cannot mint COE / R&D Cell operators
            identity_services.grant_capability(self.acad, self.coord, C.COE_OPERATOR, "INSTITUTION", basis="x")
        with self.assertRaises(PermissionDenied):
            identity_services.grant_capability(self.coe, self.coe, C.RND_CELL_OPERATOR, "INSTITUTION", basis="x")

    def test_scholar_cannot_mutate_academic_records(self):
        with self.assertRaises(PermissionDenied):
            marks.enter_mark(self.p_sch1, self.ca, self.a1, marks=100)
        self.grade(self.a1, ca=50, et=50)
        r = results.prepare_result(self.coord, self.a1)
        for fn in (results.verify_result, results.ratify_result):
            with self.assertRaises(PermissionDenied):
                fn(self.p_sch1, r)
        with self.assertRaises(PermissionDenied):
            examination.register_for_exam(self.p_sch1, self.enroll(self.sch2, self.sec_b), self.exam)

    def test_mutations_and_denials_are_audited(self):
        marks.enter_mark(self.coord, self.ca, self.a1, marks=50)
        ok = AuditEvent.objects.filter(action="academic.marks.enter.continuous", allowed=True).latest("id")
        self.assertEqual((ok.actor, ok.capability_used), (self.coord, "FACULTY"))
        with self.assertRaises(PermissionDenied):
            marks.enter_mark(self.outsider, self.et, self.a1, marks=50)
        denied = AuditEvent.objects.filter(action="academic.marks.enter.end_term", allowed=False).latest("id")
        self.assertEqual(denied.actor, self.outsider)
        from identity.audit import verify_chain
        self.assertEqual(verify_chain(), (True, None))


class AdminBypassTests(AcademicFixture):
    def test_admin_cannot_mutate_academic_records(self):
        from coursework.models import Course, CourseAttempt
        su = get_user_model().objects.create_superuser("root2")
        self.client.force_login(su)
        self.assertEqual(self.client.get("/admin/coursework/course/add/").status_code, 403)
        self.assertEqual(self.client.get("/admin/coursework/courseattempt/add/").status_code, 403)
        self.assertEqual(self.client.get("/admin/coursework/academicruleparameter/add/").status_code, 403)
        course = Course.objects.get(code="SIS7002")
        self.client.post(f"/admin/coursework/course/{course.pk}/change/", {"credits": 99, "code": "SIS7002",
                                                                          "title": "x", "category": "MANDATORY"})
        course.refresh_from_db()
        self.assertEqual(course.credits, 2)
        e = self.enroll(self.sch1)
        a = self.register(e).attempt
        self.client.post(f"/admin/coursework/courseattempt/{a.pk}/delete/", {"post": "yes"})
        self.assertTrue(CourseAttempt.objects.filter(pk=a.pk).exists())


class AcademicWorkspaceTests(AcademicFixture):
    """Each academic role lands in its own workspace, and a workspace confers
    only its role's actions (the chain of custody cannot be collapsed)."""

    def test_each_academic_role_has_its_workspace(self):
        from identity.authz import workspaces_of
        expected = {
            self.acad: "academic_admin", self.cisr: "cisr", self.rnd: "rnd_cell", self.coe: "coe",
            self.dean: "dean", self.vc: "vc", self.phd: "phd_cell", self.dept_admin: "department",
            self.coord: "faculty", self.sdrc: "sdrc", self.p_sch1: "scholar",
        }
        for person, key in expected.items():
            self.assertIn(key, [w["key"] for w in workspaces_of(person)], person)
        self.assertEqual([w["key"] for w in workspaces_of(self.p_sch1)], ["scholar"])

    def test_result_chain_roles_cannot_act_outside_their_step(self):
        from coursework.academic import records
        e = self.enroll(self.sch1, self.sec_a)
        attempt = self.register(e).attempt
        self.grade(attempt, ca=40, et=40)
        for actor in (self.rnd, self.coe, self.dean, self.vc, self.acad, self.p_sch1):
            with self.assertRaises(PermissionDenied, msg=str(actor)):
                results.prepare_result(actor, attempt)
        r = results.prepare_result(self.coord, attempt)
        for actor in (self.coord, self.coe, self.dean, self.vc, self.acad):
            with self.assertRaises(PermissionDenied, msg=str(actor)):
                results.verify_result(actor, r)
        results.verify_result(self.rnd, r)
        for actor in (self.coord, self.rnd, self.rnd2, self.dean, self.vc, self.acad):
            with self.assertRaises(PermissionDenied, msg=str(actor)):
                results.ratify_result(actor, r, acknowledge_provisional=True)
        results.ratify_result(self.coe, r, acknowledge_provisional=True)
        with self.assertRaises(PermissionDenied):
            records.issue_transcript(self.rnd, self.sch1)

    def test_config_change_and_approval_are_role_bound(self):
        from coursework.academic import config
        change = config.propose_change(self.acad, "revaluation.enabled", value=True, status="CONFIGURABLE",
                                       source="s", reason="r")
        for actor in (self.acad, self.coe, self.vc, self.rnd):
            with self.assertRaises(PermissionDenied, msg=str(actor)):
                config.decide_change(actor, change, approve=True)
        for actor in (self.dean, self.coe, self.p_sch1):
            with self.assertRaises(PermissionDenied, msg=str(actor)):
                config.propose_change(actor, "revaluation.enabled", value=True, status="CONFIGURABLE",
                                      source="s", reason="r")
