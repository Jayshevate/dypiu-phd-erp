"""Academic REST API: authorization and behaviour (Step 5C).

Every denial here is server-side; nothing depends on the UI hiding controls."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import Committee, CommitteeMembership
from coursework.academic import records
from coursework.academic.testing import TODAY, AcademicFixture
from coursework.models import (CourseResult, ExamEligibility, FacultySubjectAssignment, TranscriptIssue)
from identity.capabilities import Capability as C
from identity.capabilities import ScopeType as S
from identity.models import CapabilityAssignment

API = "/api/academic/"


class ApiFixture(AcademicFixture):
    def client_for(self, person):
        if person.user_id is None:
            person.user = get_user_model().objects.create_user(f"u{person.pk}")
            person.save(update_fields=["user"])
        c = APIClient()
        c.force_login(person.user)
        return c

    def setUp(self):
        super().setUp()
        self.e1 = self.enroll(self.sch1, self.sec_a)
        self.e2 = self.enroll(self.sch2, self.sec_b)
        self.a1 = self.register(self.e1).attempt
        self.a2 = self.register(self.e2).attempt

    def get(self, person, url, **params):
        return self.client_for(person).get(API + url, params)

    def post(self, person, url, data=None):
        return self.client_for(person).post(API + url, data or {}, format="json")

    def prepared_result(self):
        self.grade(self.a1, ca=40, et=40)
        r = self.post(self.coord, f"attempts/{self.a1.pk}/prepare-result/")
        self.assertEqual(r.status_code, 201, r.content)
        return CourseResult.objects.get(pk=r.json()["id"])


class AuthenticationTests(ApiFixture):
    def test_anonymous_gets_401_everywhere(self):
        c = APIClient()
        for url in ("me/dashboard/", "teaching/assignments/", "results/", "scholars/", "exam-cycles/",
                    "third-attempt-cases/", "rules/", f"scholars/{self.sch1.pk}/transcript/"):
            r = c.get(API + url)
            self.assertEqual(r.status_code, 401, url)
            self.assertEqual(r.json()["code"], "not_authenticated")

    def test_login_requires_csrf_and_a_linked_person(self):
        user = get_user_model().objects.create_user("scholar1", password="pw-123456")
        self.p_sch1.user = user
        self.p_sch1.save(update_fields=["user"])
        strict = APIClient(enforce_csrf_checks=True)
        self.assertEqual(strict.post("/api/auth/login/", {"username": "scholar1", "password": "pw-123456"},
                                     format="json").status_code, 403)
        token = strict.get("/api/auth/csrf/").json()["csrf_token"]
        r = strict.post("/api/auth/login/", {"username": "scholar1", "password": "pw-123456"}, format="json",
                        HTTP_X_CSRFTOKEN=token)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["person"]["id"], self.p_sch1.pk)
        self.assertEqual(strict.get(API + "me/dashboard/").status_code, 200)
        bad = APIClient().post("/api/auth/login/", {"username": "scholar1", "password": "wrong"}, format="json")
        self.assertEqual(bad.status_code, 401)

    def test_login_without_person_is_refused(self):
        get_user_model().objects.create_user("orphan", password="pw-123456")
        r = APIClient().post("/api/auth/login/", {"username": "orphan", "password": "pw-123456"}, format="json")
        self.assertEqual(r.status_code, 401)
        c = APIClient()
        c.force_login(get_user_model().objects.get(username="orphan"))
        self.assertEqual(c.get(API + "me/dashboard/").status_code, 403)

    def test_identity_me_reports_scope_and_relationships(self):
        body = self.client_for(self.inst_a).get("/identity/me/").json()
        self.assertEqual(body["scope"]["faculty_id"], self.inst_a.faculty_profile_id)
        self.assertEqual([t["role"] for t in body["scope"]["teaching"]], ["INSTRUCTOR"])
        self.assertEqual([w["key"] for w in body["workspaces"]], ["faculty"])
        dean = self.client_for(self.dean).get("/identity/me/").json()
        self.assertEqual(dean["workspaces"][0]["path"], "/dean-rd")

    def test_errors_never_expose_tracebacks(self):
        r = self.post(self.p_sch1, "me/exam-registrations/", {"enrollment_id": "x"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["code"], "validation_error")
        self.assertNotIn("Traceback", r.content.decode())


class ScholarIsolationTests(ApiFixture):
    def test_scholar_cannot_read_another_scholars_record_or_transcript(self):
        for url in (f"scholars/{self.sch1.pk}/record/", f"scholars/{self.sch1.pk}/transcript/"):
            self.assertEqual(self.get(self.p_sch2, url).status_code, 403, url)
        self.assertEqual(self.get(self.p_sch1, f"scholars/{self.sch1.pk}/record/").status_code, 200)

    def test_scholar_cannot_act_on_another_scholars_enrollment(self):
        r = self.post(self.p_sch2, "me/exam-registrations/", {"enrollment_id": self.e1.pk, "exam_id": self.exam.pk})
        self.assertEqual(r.status_code, 403)
        r = self.post(self.p_sch2, "me/third-attempt/", {"enrollment_id": self.e1.pk, "reason": "x"})
        self.assertEqual(r.status_code, 403)

    def test_browser_supplied_identity_is_ignored(self):
        body = self.get(self.p_sch1, "me/dashboard/", scholar_id=self.sch2.pk, role="COE_OPERATOR").json()
        self.assertEqual(body["profile"]["prn"], self.sch1.prn)
        r = self.prepared_result()
        c = self.client_for(self.p_sch1)
        resp = c.post(API + f"results/{r.pk}/verify/", {"role": "RND_CELL_OPERATOR", "capability": "RND_CELL_OPERATOR"},
                      format="json", HTTP_X_ROLE="RND_CELL_OPERATOR")
        self.assertEqual(resp.status_code, 403)

    def test_staff_have_no_scholar_workspace(self):
        for person in (self.coe, self.coord, self.dean):
            self.assertEqual(self.get(person, "me/dashboard/").status_code, 403)

    def test_scholar_sees_results_only_once_ratified(self):
        r = self.prepared_result()
        rows = self.get(self.p_sch1, "me/results/").json()["results"]
        self.assertIsNone(rows[0]["result"])
        self.assertEqual(self.get(self.p_sch1, f"results/{r.pk}/").status_code, 404)
        self.assertEqual(self.post(self.rnd, f"results/{r.pk}/verify/").status_code, 200)
        self.assertEqual(self.post(self.coe, f"results/{r.pk}/ratify/", {"acknowledge_provisional": True}).status_code,
                         200)
        row = self.get(self.p_sch1, "me/results/").json()["results"][0]
        self.assertEqual(row["result"]["status"], "RATIFIED")
        self.assertTrue(row["result"]["marks"])


class FacultyScopeTests(ApiFixture):
    def test_instructor_sees_only_own_section(self):
        roster = self.get(self.inst_b, f"offerings/{self.offering.pk}/roster/").json()["results"]
        self.assertEqual({r["scholar"]["id"] for r in roster}, {self.sch2.pk})
        coord_roster = self.get(self.coord, f"offerings/{self.offering.pk}/roster/").json()["results"]
        self.assertEqual({r["scholar"]["id"] for r in coord_roster}, {self.sch1.pk, self.sch2.pk})
        marks = self.get(self.inst_b, f"offerings/{self.offering.pk}/marks/").json()["results"]
        self.assertEqual({m["scholar"]["id"] for m in marks}, {self.sch2.pk})

    def test_faculty_a_cannot_touch_faculty_b_section(self):
        r = self.post(self.inst_a, f"sections/{self.sec_a.pk}/sessions/",
                      {"date": str(TODAY), "start_time": "10:00", "end_time": "11:00"})
        self.assertEqual(r.status_code, 201, r.content)
        session_id = r.json()["id"]
        self.assertEqual(self.get(self.inst_b, f"sessions/{session_id}/records/").status_code, 403)
        self.assertEqual(self.post(self.inst_b, f"sessions/{session_id}/records/",
                                   {"enrollment_id": self.e1.pk, "status": "PRESENT", "mode": "PHYSICAL"}).status_code,
                         403)
        self.assertEqual(self.post(self.inst_b, "marks/", {"assessment_id": self.ca.pk, "attempt_id": self.a1.pk,
                                                           "marks": 30}).status_code, 403)

    def test_unassigned_and_other_department_faculty_denied(self):
        for url in (f"offerings/{self.offering.pk}/", f"offerings/{self.offering.pk}/roster/",
                    f"offerings/{self.offering.pk}/marks/", f"offerings/{self.offering.pk}/results/"):
            self.assertEqual(self.get(self.outsider, url).status_code, 403, url)
        self.assertEqual(self.post(self.outsider, "marks/", {"assessment_id": self.ca.pk, "attempt_id": self.a1.pk,
                                                             "marks": 30}).status_code, 403)
        self.assertEqual(self.get(self.outsider, "teaching/assignments/").json()["results"], [])

    def test_instructor_cannot_enter_end_term_or_prepare_results(self):
        self.assertEqual(self.post(self.inst_a, "marks/", {"assessment_id": self.et.pk, "attempt_id": self.a1.pk,
                                                           "marks": 40}).status_code, 403)
        self.grade(self.a1, ca=40, et=40)
        self.assertEqual(self.post(self.inst_a, f"attempts/{self.a1.pk}/prepare-result/").status_code, 403)

    def test_expired_assignment_removes_access_immediately(self):
        self.assertEqual(self.get(self.inst_a, f"offerings/{self.offering.pk}/").status_code, 200)
        FacultySubjectAssignment.objects.filter(faculty=self.inst_a.faculty_profile).update(
            valid_to=TODAY - timedelta(days=1))
        self.assertEqual(self.get(self.inst_a, f"offerings/{self.offering.pk}/").status_code, 403)
        self.assertEqual(self.get(self.inst_a, "teaching/assignments/").json()["results"], [])

    def test_revoked_assignment_removes_access_immediately(self):
        FacultySubjectAssignment.objects.filter(faculty=self.coord.faculty_profile).update(revoked_at=timezone.now())
        self.assertEqual(self.get(self.coord, f"offerings/{self.offering.pk}/marks/").status_code, 403)
        self.assertEqual(self.post(self.coord, "marks/", {"assessment_id": self.et.pk, "attempt_id": self.a1.pk,
                                                          "marks": 40}).status_code, 403)

    def test_marks_cannot_change_after_preparation_or_ratification(self):
        r = self.prepared_result()
        resp = self.post(self.coord, "marks/", {"assessment_id": self.ca.pk, "attempt_id": self.a1.pk, "marks": 45,
                                                "reason": "fix"})
        self.assertEqual(resp.status_code, 403)
        self.post(self.rnd, f"results/{r.pk}/verify/")
        self.post(self.coe, f"results/{r.pk}/ratify/", {"acknowledge_provisional": True})
        self.assertEqual(self.post(self.coe, f"results/{r.pk}/return/", {"reason": "x"}).status_code, 403)
        self.assertEqual(self.post(self.coord, f"attempts/{self.a1.pk}/prepare-result/").status_code, 400)

    def test_stale_mark_edit_is_a_conflict(self):
        first = self.post(self.coord, "marks/", {"assessment_id": self.ca.pk, "attempt_id": self.a1.pk, "marks": 30})
        stamp = first.json()["updated_at"]
        self.post(self.coord, "marks/", {"assessment_id": self.ca.pk, "attempt_id": self.a1.pk, "marks": 31,
                                         "reason": "recount", "expected_updated_at": stamp})
        stale = self.post(self.coord, "marks/", {"assessment_id": self.ca.pk, "attempt_id": self.a1.pk, "marks": 32,
                                                 "reason": "again", "expected_updated_at": stamp})
        self.assertEqual(stale.status_code, 409)


class ResultChainApiTests(ApiFixture):
    def test_chain_with_separation_of_duties_and_role_bound_steps(self):
        r = self.prepared_result()
        for person in (self.coord, self.coe, self.dean, self.vc, self.p_sch1, self.inst_a):
            self.assertEqual(self.post(person, f"results/{r.pk}/verify/").status_code, 403, person)
        queue = self.get(self.rnd, "results/", stage="verify").json()
        self.assertEqual([x["id"] for x in queue["results"]], [r.pk])
        self.assertTrue(queue["results"][0]["can"]["verify"])
        self.assertEqual(self.get(self.coe, "results/", stage="verify").json()["results"], [])
        self.assertEqual(self.post(self.rnd, f"results/{r.pk}/verify/").status_code, 200)
        for person in (self.coord, self.rnd, self.rnd2, self.dean, self.vc):
            self.assertEqual(self.post(person, f"results/{r.pk}/ratify/", {"acknowledge_provisional": True})
                             .status_code, 403, person)
        no_ack = self.post(self.coe, f"results/{r.pk}/ratify/")
        self.assertEqual(no_ack.status_code, 400)
        self.assertIn("acknowledgement", no_ack.json()["detail"])
        self.assertEqual(self.post(self.coe, f"results/{r.pk}/ratify/", {"acknowledge_provisional": True})
                         .status_code, 200)

    def test_stale_transition_is_a_conflict(self):
        r = self.prepared_result()
        self.post(self.rnd, f"results/{r.pk}/verify/")
        stale = self.post(self.rnd2, f"results/{r.pk}/return/", {"reason": "x", "expected_status": "PREPARED"})
        self.assertEqual(stale.status_code, 409)

    def test_revoked_capability_loses_power(self):
        r = self.prepared_result()
        self.post(self.rnd, f"results/{r.pk}/verify/")
        CapabilityAssignment.objects.filter(person=self.coe, capability=C.COE_OPERATOR).update(
            revoked_at=timezone.now())
        self.assertEqual(self.post(self.coe, f"results/{r.pk}/ratify/", {"acknowledge_provisional": True})
                         .status_code, 403)


class ScopeTests(ApiFixture):
    def test_department_and_school_boundaries(self):
        self.assertEqual(self.get(self.dept_admin, f"scholars/{self.sch1.pk}/record/").status_code, 200)
        self.assertEqual(self.get(self.dept_admin, f"scholars/{self.sch3.pk}/record/").status_code, 403)
        listed = {s["id"] for s in self.get(self.dept_admin, "scholars/").json()["results"]}
        self.assertEqual(listed, {self.sch1.pk, self.sch2.pk})
        school2 = self.staff(C.SCHOOL_ADMIN, S.SCHOOL, school=self.s2)
        self.assertEqual(self.get(school2, f"scholars/{self.sch3.pk}/record/").status_code, 200)
        self.assertEqual(self.get(school2, f"scholars/{self.sch1.pk}/record/").status_code, 403)

    def test_sdrc_sees_only_its_school(self):
        from coursework.models import Course, CourseOffering
        off = CourseOffering.objects.create(course=Course.objects.get(code="SIS7006"), semester=self.sem, status="OPEN")
        self.enroll(self.sch1, offering=off)
        self.enroll(self.sch3, offering=off)
        mine = {r["scholar"]["id"] for r in self.get(self.sdrc, "sdrc/activities/").json()["results"]}
        theirs = {r["scholar"]["id"] for r in self.get(self.sdrc_s2, "sdrc/activities/").json()["results"]}
        self.assertEqual((mine, theirs), ({self.sch1.pk}, {self.sch3.pk}))
        self.assertEqual(self.get(self.coord, "sdrc/activities/").json()["results"], [])


class ApprovalApiTests(ApiFixture):
    def failed_twice(self):
        self.configure("exam.reattempt_carries_continuous_assessment", False)
        self.grade(self.a1, ca=10, et=10)
        self.ratify(self.a1)
        a = self.register(self.e1, self.next_exam()).attempt
        self.grade(a, ca=10, et=10)
        self.ratify(a)

    def test_third_attempt_dean_then_vc_only(self):
        self.failed_twice()
        info = self.get(self.p_sch1, "me/third-attempt/").json()
        self.assertEqual(info["eligible"][0]["enrollment_id"], self.e1.pk)
        case = self.post(self.p_sch1, "me/third-attempt/", {"enrollment_id": self.e1.pk, "reason": "illness"}).json()
        cid = case["id"]
        self.assertEqual(self.post(self.vc, f"third-attempt-cases/{cid}/decide/",
                                   {"approve": False, "remarks": "x"}).status_code, 403)
        for person in (self.vc, self.coe, self.p_sch1, self.acad):
            self.assertEqual(self.post(person, f"third-attempt-cases/{cid}/review/",
                                       {"recommend": True, "remarks": "x"}).status_code, 403, person)
        dean_view = self.get(self.dean, "third-attempt-cases/").json()["results"][0]
        self.assertTrue(dean_view["can"]["review"])
        self.assertFalse(dean_view["can"]["decide"])
        self.assertEqual(self.post(self.dean, f"third-attempt-cases/{cid}/review/",
                                   {"recommend": True, "remarks": "ok"}).status_code, 200)
        self.assertEqual(self.post(self.dean, f"third-attempt-cases/{cid}/decide/",
                                   {"approve": True, "remarks": "x"}).status_code, 403)
        vc_view = self.get(self.vc, "third-attempt-cases/").json()["results"][0]
        self.assertTrue(vc_view["can"]["decide"])
        self.assertIn("mentor", vc_view["approval_blocked_reason"])
        blocked = self.post(self.vc, f"third-attempt-cases/{cid}/decide/", {"approve": True, "remarks": "ok"})
        self.assertEqual(blocked.status_code, 400)
        self.assertEqual(self.post(self.vc, f"third-attempt-cases/{cid}/decide/",
                                   {"approve": False, "remarks": "no"}).status_code, 200)
        self.assertEqual(self.get(self.p_sch1, "me/third-attempt/").json()["cases"][0]["status"], "VC_REJECTED")

    def test_config_maker_checker_through_api(self):
        rules = self.get(self.acad, "rules/").json()
        self.assertTrue(rules["can_propose"])
        unresolved = [r for r in rules["results"] if r["status"] == "UNRESOLVED"]
        self.assertTrue(unresolved)
        r = self.post(self.acad, "rules/changes/", {"key": "revaluation.enabled", "value": False,
                                                    "status": "CONFIGURABLE", "source": "s", "reason": "r"})
        self.assertEqual(r.status_code, 201)
        cid = r.json()["id"]
        self.assertEqual(self.post(self.acad, f"rules/changes/{cid}/decide/", {"approve": True}).status_code, 403)
        self.assertEqual(self.post(self.coe, "rules/changes/", {"key": "revaluation.enabled", "value": True,
                                                                "status": "CONFIGURABLE", "source": "s",
                                                                "reason": "r"}).status_code, 403)
        self.assertEqual(self.post(self.dean, f"rules/changes/{cid}/decide/", {"approve": True}).status_code, 200)

    def test_unresolved_authority_blocks_elective_decision(self):
        self.approved_list(self.elective_course())
        p = self.post(self.p_sch1, "me/electives/", {"semester_id": self.sem.pk, "course_id": self.el.pk,
                                                     "justification": "need"})
        self.assertEqual(p.status_code, 201, p.content)
        rows = self.get(self.dean, "elective-proposals/").json()
        # Dean R&D may view (academic oversight) but cannot decide while the authority is UNRESOLVED
        self.assertEqual(rows["rules"]["elective.approval_authority"]["status"], "UNRESOLVED")
        self.assertFalse(rows["results"][0]["can"]["decide"])
        self.assertIn("elective.approval_authority", rows["results"][0]["decision_blocked_reason"])
        self.assertEqual(self.get(self.p_sch2, "elective-proposals/").json()["results"], [])
        resp = self.post(self.dean, f"elective-proposals/{p.json()['id']}/decide/", {"approve": True})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("elective.approval_authority", resp.json()["detail"])

    def elective_course(self):
        from coursework.models import Course
        self.el = Course.objects.create(code="SIS7801", title="E", credits=3, category="ELECTIVE",
                                        required_for_all=False)
        return self.el

    def test_elective_list_chain_via_api(self):
        self.elective_course()
        created = self.post(self.dept_admin, "elective-lists/", {
            "semester_id": self.sem.pk, "department_id": self.d1.pk, "items": [{"course_id": self.el.pk}]})
        self.assertEqual(created.status_code, 201, created.content)
        lid = created.json()["id"]
        other_dept = self.staff(C.DEPARTMENT_ADMIN, S.DEPARTMENT, department=self.d3)
        self.assertEqual(self.post(other_dept, f"elective-lists/{lid}/submit/").status_code, 403)
        self.assertEqual(self.post(self.dept_admin, f"elective-lists/{lid}/submit/").status_code, 200)
        dc = self.faculty(self.d1)
        committee = Committee.objects.create(type="DC", name="DC S1", school=self.s1)
        CommitteeMembership.objects.create(committee=committee, faculty=dc.faculty_profile)
        self.assertEqual(self.post(self.dean, f"elective-lists/{lid}/decide/", {"approve": True}).status_code, 403)
        view = self.get(dc, "elective-lists/").json()["results"][0]
        self.assertTrue(view["can"]["decide"])
        self.assertEqual(self.post(dc, f"elective-lists/{lid}/decide/", {"approve": True, "expected_step": 0})
                         .status_code, 200)
        self.assertEqual(self.post(self.dean, f"elective-lists/{lid}/decide/", {"approve": True, "expected_step": 0})
                         .status_code, 409)
        self.assertEqual(self.post(self.dean, f"elective-lists/{lid}/decide/", {"approve": True}).status_code, 200)
        lists = self.get(self.p_sch1, "me/electives/").json()["lists"]
        self.assertEqual([x["id"] for x in lists], [lid])


class OperationsApiTests(ApiFixture):
    def test_dashboard_numbers_come_from_the_record_service(self):
        body = self.get(self.p_sch1, "me/dashboard/").json()
        status = records.coursework_status(self.sch1)
        self.assertEqual(body["summary"]["credits_required"], status["required_credits"])
        self.assertEqual(body["summary"]["credits_earned"], status["credits_earned"])
        self.assertEqual(body["summary"]["credits_remaining"], status["required_credits"] - status["credits_earned"])
        self.assertEqual(body["upcoming_exams"][0]["course"], "SIS7002")

    def test_exam_preview_does_not_persist_and_registration_flow(self):
        before = ExamEligibility.objects.count()
        exams = self.get(self.p_sch1, "me/exams/").json()
        self.assertEqual(ExamEligibility.objects.count(), before)
        self.assertTrue(exams["registrations"])
        reg_id = exams["registrations"][0]["id"]
        self.assertEqual(self.post(self.p_sch1, f"exam-registrations/{reg_id}/hall-ticket/").status_code, 403)
        ticket = self.post(self.coe, f"exam-registrations/{reg_id}/hall-ticket/")
        self.assertEqual(ticket.status_code, 201)
        tid = ticket.json()["id"]
        self.assertEqual(self.get(self.p_sch1, f"hall-tickets/{tid}/").status_code, 200)
        self.assertEqual(self.get(self.p_sch2, f"hall-tickets/{tid}/").status_code, 403)
        regs = self.get(self.coe, f"exams/{self.exam.pk}/registrations/").json()
        self.assertEqual(len(regs["results"]), 2)
        self.assertEqual(self.get(self.coord, f"exams/{self.exam.pk}/registrations/").status_code, 403)

    def test_attendance_is_session_derived(self):
        sid = self.post(self.inst_a, f"sections/{self.sec_a.pk}/sessions/",
                        {"date": str(TODAY), "start_time": "10:00", "end_time": "11:00"}).json()["id"]
        self.post(self.inst_a, f"sessions/{sid}/records/", {"enrollment_id": self.e1.pk, "status": "PRESENT",
                                                            "mode": "PHYSICAL"})
        rows = self.get(self.p_sch1, "me/attendance/").json()["results"]
        self.assertEqual(rows[0]["summary"]["sessions_held"], 1)
        self.assertEqual(rows[0]["summary"]["percent"], "100.00")

    def test_transcript_operations(self):
        self.grade(self.a1, ca=45, et=45)
        self.ratify(self.a1)
        for person in (self.rnd, self.p_sch1, self.coord, self.dean):
            self.assertEqual(self.post(person, f"scholars/{self.sch1.pk}/transcript/issue/").status_code, 403, person)
        for person in (self.coord, self.inst_a, self.p_sch2, self.vc):
            self.assertEqual(self.get(person, f"scholars/{self.sch1.pk}/transcript/").status_code, 403, person)
        issued = self.post(self.coe, f"scholars/{self.sch1.pk}/transcript/issue/")
        self.assertEqual(issued.status_code, 201)
        mine = self.get(self.p_sch1, "me/transcript/").json()
        self.assertEqual(mine["issues"][0]["version"], 1)
        self.assertTrue(mine["issues"][0]["matches_current_records"])
        public = APIClient().get(API + f"transcripts/verify/{issued.json()['verification_code']}/").json()
        self.assertTrue(public["valid"])
        self.assertEqual(TranscriptIssue.objects.count(), 1)

    def test_governance_is_role_and_scope_bound(self):
        self.assertEqual(self.get(self.acad, "governance/offerings/").status_code, 200)
        self.assertEqual(self.get(self.coord, "governance/offerings/").status_code, 403)
        self.assertEqual(self.get(self.p_sch1, "governance/faculty/").status_code, 403)
        # a department admin may assign faculty only within their department's offerings
        from coursework.models import Course, CourseOffering
        d3_course = Course.objects.create(code="SIS7901", title="D3", credits=2, category="MANDATORY",
                                          required_for_all=False, department=self.d3)
        off = CourseOffering.objects.create(course=d3_course, semester=self.sem, status="OPEN")
        r = self.post(self.dept_admin, f"governance/offerings/{off.pk}/assignments/",
                      {"faculty_id": self.outsider.faculty_profile_id, "role": "COURSE_COORDINATOR", "basis": "x"})
        self.assertEqual(r.status_code, 403)

    def test_scholar_self_enrollment_uses_the_service(self):
        from coursework.models import Course, CourseOffering
        off = CourseOffering.objects.create(course=Course.objects.get(code="SIS7003"), semester=self.sem,
                                            status="OPEN")
        offered = [o["id"] for o in self.get(self.p_sch1, "me/offerings/").json()["results"]]
        self.assertIn(off.pk, offered)
        self.assertEqual(self.post(self.p_sch1, "me/enrollments/", {"offering_id": off.pk}).status_code, 201)
        self.assertEqual(self.post(self.p_sch2, f"scholars/{self.sch1.pk}/enrollments/",
                                   {"offering_id": off.pk}).status_code, 403)


class NegativeAuthorizationTests(ApiFixture):
    def test_every_api_route_requires_authentication(self):
        """Route audit: only the public transcript verification is anonymous."""
        from django.urls import URLPattern

        from coursework.api.urls import urlpatterns
        c = APIClient()
        for p in urlpatterns:
            assert isinstance(p, URLPattern)
            route = str(p.pattern).replace("<int:pk>", "1").replace("<str:code>", "x")
            for method in ("get", "post"):
                status = getattr(c, method)(API + route).status_code
                if route.startswith("transcripts/verify/"):
                    continue
                self.assertIn(status, (401, 405), f"{method.upper()} {route} -> {status}")

    def test_scholar_cannot_approve_anything(self):
        r = self.prepared_result()
        for url, data in ((f"results/{r.pk}/verify/", {}), (f"results/{r.pk}/ratify/", {"acknowledge_provisional": True}),
                          (f"results/{r.pk}/return/", {"reason": "x"}),
                          (f"scholars/{self.sch1.pk}/transcript/issue/", {}),
                          (f"scholars/{self.sch1.pk}/fee-clearance/", {"semester_id": self.sem.pk, "cleared": True,
                                                                        "reference": "r"}),
                          ("rules/changes/", {"key": "revaluation.enabled", "value": True, "status": "CONFIGURABLE",
                                              "source": "s", "reason": "r"}),
                          ("exam-cycles/", {"semester_id": self.sem.pk, "name": "x", "registration_opens": str(TODAY),
                                            "registration_closes": str(TODAY), "exam_start": str(TODAY),
                                            "exam_end": str(TODAY)})):
            self.assertEqual(self.post(self.p_sch1, url, data).status_code, 403, url)

    def test_unauthorized_elective_approval_with_configured_authority(self):
        self.configure("elective.approval_authority", ["DEAN_RND"])
        self.configure("elective.supervisor_recommendation_required", False)
        from coursework.models import Course
        el = Course.objects.create(code="SIS7811", title="E", credits=3, category="ELECTIVE", required_for_all=False)
        self.approved_list(el)
        pid = self.post(self.p_sch1, "me/electives/", {"semester_id": self.sem.pk, "course_id": el.pk,
                                                       "justification": "j"}).json()["id"]
        for person in (self.p_sch1, self.coord, self.coe, self.vc, self.dept_admin):
            self.assertEqual(self.post(person, f"elective-proposals/{pid}/decide/", {"approve": True}).status_code,
                             403, person)
        self.assertEqual(self.post(self.coord, f"elective-proposals/{pid}/recommend/", {"recommend": True})
                         .status_code, 403)
        row = self.get(self.dean, "elective-proposals/").json()["results"][0]
        self.assertTrue(row["can"]["decide"])
        self.assertEqual(self.post(self.dean, f"elective-proposals/{pid}/decide/", {"approve": True}).status_code, 200)

    def test_cross_school_sdrc_cannot_review_or_evaluate(self):
        from coursework.academic import activities
        from coursework.models import Course, CourseOffering
        off = CourseOffering.objects.create(course=Course.objects.get(code="SIS7006"), semester=self.sem, status="OPEN")
        e = self.enroll(self.sch1, offering=off)
        sub = activities.submit_artefact(self.p_sch1, e, artefact="REFLECTIVE_NOTE")
        self.assertEqual(self.post(self.sdrc_s2, f"activity-submissions/{sub.pk}/review/", {"accept": True})
                         .status_code, 403)
        self.assertEqual(self.post(self.sdrc_s2, f"enrollments/{e.pk}/open-evaluation/").status_code, 403)
        self.assertEqual(self.post(self.sdrc, f"activity-submissions/{sub.pk}/review/", {"accept": True})
                         .status_code, 200)

    def test_client_supplied_ids_cannot_bypass_authorization(self):
        # faculty cannot record attendance for a scholar of another section by naming the enrollment id
        sid = self.post(self.inst_b, f"sections/{self.sec_b.pk}/sessions/",
                        {"date": str(TODAY), "start_time": "10:00", "end_time": "11:00"}).json()["id"]
        r = self.post(self.inst_b, f"sessions/{sid}/records/", {"enrollment_id": self.e1.pk, "status": "PRESENT",
                                                                "mode": "PHYSICAL"})
        self.assertEqual(r.status_code, 403)
        # an office cannot enrol a scholar outside its authority by passing the scholar id
        self.assertEqual(self.post(self.coord, f"scholars/{self.sch3.pk}/enrollments/",
                                   {"offering_id": self.offering.pk}).status_code, 403)
        # a denial is audited
        from identity.models import AuditEvent
        self.assertTrue(AuditEvent.objects.filter(allowed=False, actor=self.coord).exists())


class RevaluationApiTests(ApiFixture):
    def test_revaluation_is_blocked_until_configured(self):
        self.grade(self.a1, ca=20, et=20)
        r = self.ratify(self.a1)
        info = self.get(self.p_sch1, "me/revaluations/").json()
        self.assertFalse(info["available"])
        self.assertEqual(info["rules"]["revaluation.reviewer"]["status"], "UNRESOLVED")
        resp = self.post(self.p_sch1, "me/revaluations/", {"result_id": r.pk, "reason": "recount"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("institutional decision", resp.json()["detail"])
        self.assertEqual(self.post(self.p_sch2, "me/revaluations/", {"result_id": r.pk, "reason": "x"}).status_code,
                         403)
