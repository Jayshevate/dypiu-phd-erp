"""Step 5B operations: semester registration, elective lists, hall tickets,
question-paper workflow, revaluation."""
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from core.models import Committee, CommitteeMembership
from coursework.academic import elective_lists, exam_admin, revaluation, semester
from coursework.academic.testing import TODAY, AcademicFixture
from coursework.models import (Course, CourseResult, ElectiveList, ElectiveListDecision, FeeClearance, HallTicket,
                               MarkEntry, RevaluationCase, RevaluationMark, Semester, SemesterRegistration)
from identity.capabilities import Capability as C
from identity.capabilities import ScopeType as S


def dc_member(fixture, school):
    person = fixture.faculty(fixture.d1 if school == fixture.s1 else fixture.d3)
    committee, _ = Committee.objects.get_or_create(type="DC", name=f"DC {school.code}", school=school)
    CommitteeMembership.objects.create(committee=committee, faculty=person.faculty_profile)
    return person


class SemesterRegistrationTests(AcademicFixture):
    def setUp(self):
        super().setUp()
        self.next_sem = Semester.objects.create(
            academic_year=self.year, term="JAN_JUN", name="Next", start_date=TODAY + timedelta(days=61),
            end_date=TODAY + timedelta(days=180), registration_opens=TODAY - timedelta(days=1),
            registration_closes=TODAY + timedelta(days=30))

    def test_requires_fee_clearance_then_registers_once(self):
        with self.assertRaisesMessage(ValidationError, "Fee / dues clearance"):
            semester.register_semester(self.p_sch1, self.sch1, self.next_sem)
        semester.record_fee_clearance(self.phd, self.sch1, self.next_sem, cleared=True, reference="RCPT-1")
        reg = semester.register_semester(self.p_sch1, self.sch1, self.next_sem)
        self.assertEqual(reg.fee_clearance.reference, "RCPT-1")
        with self.assertRaisesMessage(ValidationError, "already registered"):
            semester.register_semester(self.phd, self.sch1, self.next_sem)

    def test_uncleared_fee_does_not_count(self):
        semester.record_fee_clearance(self.phd, self.sch1, self.next_sem, cleared=False, reference="pending")
        with self.assertRaises(ValidationError):
            semester.register_semester(self.p_sch1, self.sch1, self.next_sem)

    def test_window_enforced(self):
        semester.record_fee_clearance(self.phd, self.sch1, self.next_sem, cleared=True, reference="R")
        with self.assertRaisesMessage(ValidationError, "Registration window"):
            semester.register_semester(self.p_sch1, self.sch1, self.next_sem, on=TODAY + timedelta(days=31))

    def test_no_window_blocks(self):
        from coursework.models import AcademicYear
        year = AcademicYear.objects.create(code="AY-NW", start_date=TODAY, end_date=TODAY + timedelta(days=300))
        sem = Semester.objects.create(academic_year=year, term="JAN_JUN", name="Nowin",
                                      start_date=TODAY, end_date=TODAY + timedelta(days=100))
        FeeClearance.objects.create(scholar=self.sch1, semester=sem, cleared=True, reference="x",
                                    recorded_by=self.phd)
        with self.assertRaisesMessage(ValidationError, "No registration window"):
            semester.register_semester(self.p_sch1, self.sch1, sem)

    def test_authorization(self):
        with self.assertRaises(PermissionDenied):
            semester.register_semester(self.p_sch2, self.sch1, self.next_sem)       # another scholar
        for actor in (self.p_sch1, self.coord, self.coe):
            with self.assertRaises(PermissionDenied):
                semester.record_fee_clearance(actor, self.sch1, self.next_sem, cleared=True, reference="R")
        with self.assertRaises(ValidationError):
            semester.record_fee_clearance(self.phd, self.sch1, self.next_sem, cleared=True, reference=" ")

    def test_fee_clearance_is_required_only_while_configured(self):
        self.configure("semester_registration.requires_fee_clearance", False)
        reg = semester.register_semester(self.p_sch1, self.sch1, self.next_sem)
        self.assertIsNone(reg.fee_clearance)

    def test_enrollment_and_exam_eligibility_need_registration(self):
        sch, _ = self.scholar(self.d1)          # not admitted to the semester
        with self.assertRaises(ValidationError):
            self.enroll(sch)

    def test_window_constraint(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Semester.objects.create(academic_year=self.year, term="JAN_JUN", name="Bad", start_date=TODAY,
                                    end_date=TODAY + timedelta(days=10), registration_opens=TODAY,
                                    registration_closes=TODAY - timedelta(days=1))
        with self.assertRaises(IntegrityError), transaction.atomic():
            SemesterRegistration.objects.create(scholar=self.sch1, semester=self.sem, registered_by=self.phd)


class ElectiveListTests(AcademicFixture):
    def setUp(self):
        super().setUp()
        self.e1 = Course.objects.create(code="SIS7401", title="E1", credits=3, category="ELECTIVE",
                                        required_for_all=False)
        self.dc = dc_member(self, self.s1)

    def prepared(self):
        return elective_lists.prepare_list(self.dept_admin, semester=self.sem, department=self.d1,
                                           items=[(self.e1, 30)])

    def test_only_scoped_department_admin_prepares(self):
        other = self.staff(C.DEPARTMENT_ADMIN, S.DEPARTMENT, department=self.d3)
        for actor in (other, self.p_sch1, self.coord):
            with self.assertRaises(PermissionDenied):
                elective_lists.prepare_list(actor, semester=self.sem, department=self.d1, items=[(self.e1, None)])
        with self.assertRaises(ValidationError):
            elective_lists.prepare_list(self.dept_admin, semester=self.sem, department=self.d1,
                                        items=[(self.stats, None)])        # not an elective
        with self.assertRaises(ValidationError):
            elective_lists.prepare_list(self.dept_admin, semester=self.sem, items=[(self.e1, None)])

    def test_staged_approval_follows_configured_chain(self):
        lst = elective_lists.submit_list(self.dept_admin, self.prepared())
        self.assertEqual(lst.approval_chain, ["DC_MEMBER", "DEAN_RND"])
        with self.assertRaises(PermissionDenied):
            elective_lists.decide_list(self.dean, lst, approve=True)          # not this step
        elective_lists.decide_list(self.dc, lst, approve=True)
        self.assertEqual((lst.status, lst.current_step), ("SUBMITTED", 1))
        elective_lists.decide_list(self.dean, lst, approve=True)
        self.assertEqual(ElectiveList.objects.get(pk=lst.pk).status, "APPROVED")
        self.assertEqual(ElectiveListDecision.objects.filter(elective_list=lst).count(), 2)

    def test_return_needs_remarks_and_list_can_be_resubmitted(self):
        lst = elective_lists.submit_list(self.dept_admin, self.prepared())
        with self.assertRaises(ValidationError):
            elective_lists.decide_list(self.dc, lst, approve=False)
        elective_lists.decide_list(self.dc, lst, approve=False, remarks="Add capacity")
        self.assertEqual(lst.status, "RETURNED")
        elective_lists.submit_list(self.dept_admin, lst)
        self.assertEqual((lst.status, lst.current_step), ("SUBMITTED", 0))

    def test_dc_of_another_school_cannot_decide(self):
        lst = elective_lists.submit_list(self.dept_admin, self.prepared())
        with self.assertRaises(PermissionDenied):
            elective_lists.decide_list(dc_member(self, self.s2), lst, approve=True)

    def test_separation_of_duties(self):
        from identity.models import CapabilityAssignment
        both = dc_member(self, self.s1)
        CapabilityAssignment.objects.create(person=both, capability=C.DEAN_RND, scope_type=S.INSTITUTION,
                                            valid_from=TODAY, basis="x", granted_by=self.sysadmin)
        lst = elective_lists.submit_list(self.dept_admin, self.prepared())
        elective_lists.decide_list(both, lst, approve=True)
        with self.assertRaises(PermissionDenied):
            elective_lists.decide_list(both, lst, approve=True)              # same person cannot approve twice

    def test_unresolved_chain_blocks_submission(self):
        self.configure("elective.list_approval_chain", None, status="UNRESOLVED")
        with self.assertRaisesMessage(ValidationError, "unresolved"):
            elective_lists.submit_list(self.dept_admin, self.prepared())


class HallTicketAndQuestionPaperTests(AcademicFixture):
    def setUp(self):
        super().setUp()
        self.reg = self.register(self.enroll(self.sch1))

    def test_coe_issues_one_live_ticket(self):
        for actor in (self.phd, self.p_sch1, self.coord):
            with self.assertRaises(PermissionDenied):
                exam_admin.issue_hall_ticket(actor, self.reg)
        t = exam_admin.issue_hall_ticket(self.coe, self.reg)
        self.assertTrue(t.number.startswith("HT-"))
        with self.assertRaisesMessage(ValidationError, "already exists"):
            exam_admin.issue_hall_ticket(self.coe, self.reg)
        with self.assertRaises(ValidationError):
            exam_admin.revoke_hall_ticket(self.coe, t, reason=" ")
        exam_admin.revoke_hall_ticket(self.coe, t, reason="Reissue")
        t2 = exam_admin.issue_hall_ticket(self.coe, self.reg)
        self.assertEqual(HallTicket.objects.filter(registration=self.reg).count(), 2)
        self.assertNotEqual(t2.number, t.number)

    def test_ticket_document_has_no_signature_and_is_scoped(self):
        t = exam_admin.issue_hall_ticket(self.coe, self.reg)
        doc = exam_admin.hall_ticket_document(self.p_sch1, t)
        self.assertEqual(doc["scholar"]["prn"], self.sch1.prn)
        self.assertNotIn("signature", doc)
        with self.assertRaises(PermissionDenied):
            exam_admin.hall_ticket_document(self.p_sch2, t)

    def test_withdrawn_registration_gets_no_ticket(self):
        type(self.reg).objects.filter(pk=self.reg.pk).update(status="CANCELLED")
        self.reg.refresh_from_db()
        with self.assertRaises(ValidationError):
            exam_admin.issue_hall_ticket(self.coe, self.reg)

    def test_question_paper_setter_appointment_and_receipt(self):
        from coursework.models import Exam
        dept_course = Course.objects.create(code="SIS7501", title="Dept course", credits=2, category="MANDATORY",
                                            required_for_all=False, department=self.d1)
        exam = Exam.objects.create(cycle=self.cycle, course=dept_course, scheduled_on=self.exam.scheduled_on,
                                   start_time=self.exam.start_time, end_time=self.exam.end_time)
        dc = dc_member(self, self.s1)
        # A DC member acts within their school: an institution-level course exam is out of scope.
        with self.assertRaises(PermissionDenied):
            exam_admin.appoint_question_paper_setter(dc, self.exam, self.inst_a, basis="DC minute 4")
        with self.assertRaises(PermissionDenied):
            exam_admin.appoint_question_paper_setter(dc_member(self, self.s2), exam, self.inst_a, basis="x")
        with self.assertRaises(PermissionDenied):
            exam_admin.appoint_question_paper_setter(self.coe, exam, self.inst_a, basis="DC minute 4")
        with self.assertRaises(ValidationError):
            exam_admin.appoint_question_paper_setter(dc, exam, self.inst_a, basis=" ")
        appt = exam_admin.appoint_question_paper_setter(dc, exam, self.inst_a, basis="DC minute 4")
        self.assertEqual(appt.appointed_as, "DC_MEMBER")
        with self.assertRaises(ValidationError):
            exam_admin.appoint_question_paper_setter(dc, exam, self.inst_a, basis="again")
        with self.assertRaises(PermissionDenied):
            exam_admin.record_question_paper_received(dc, appt)
        exam_admin.record_question_paper_received(self.coe, appt)
        self.assertEqual(appt.status, "PAPER_RECEIVED")
        with self.assertRaises(ValidationError):
            exam_admin.record_question_paper_received(self.coe, appt)


class RevaluationTests(AcademicFixture):
    def setUp(self):
        super().setUp()
        self.result = self.full_attempt(self.enroll(self.sch1), ca=20, et=20)     # FAIL
        self.reviewer = self.staff(C.DEAN_RND)

    def enable(self):
        self.configure("revaluation.enabled", True)
        self.configure("revaluation.request_window_days", 15)
        self.configure("revaluation.reviewer", ["DEAN_RND"])

    def test_blocked_while_unresolved(self):
        with self.assertRaisesMessage(ValidationError, "unresolved"):
            revaluation.request_revaluation(self.p_sch1, self.result, reason="recount")
        self.assertFalse(RevaluationCase.objects.exists())

    def test_disabled_blocks(self):
        self.enable()
        self.configure("revaluation.enabled", False)
        with self.assertRaisesMessage(ValidationError, "disabled"):
            revaluation.request_revaluation(self.p_sch1, self.result, reason="recount")

    def test_request_rules(self):
        self.enable()
        with self.assertRaises(PermissionDenied):
            revaluation.request_revaluation(self.p_sch2, self.result, reason="x")
        with self.assertRaises(ValidationError):
            revaluation.request_revaluation(self.p_sch1, self.result, reason=" ")
        revaluation.request_revaluation(self.p_sch1, self.result, reason="recount")
        with self.assertRaisesMessage(ValidationError, "already open"):
            revaluation.request_revaluation(self.p_sch1, self.result, reason="again")

    def test_window(self):
        self.enable()
        CourseResult.objects.filter(pk=self.result.pk).update(ratified_at=self.result.ratified_at - timedelta(days=16))
        self.result.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "window"):
            revaluation.request_revaluation(self.p_sch1, self.result, reason="recount")

    def test_unchanged_review_closes_case_and_keeps_original(self):
        self.enable()
        case = revaluation.request_revaluation(self.p_sch1, self.result, reason="recount")
        revaluation.review_revaluation(self.reviewer, case, revised_marks={self.et: 20}, remarks="Recounted")
        self.assertEqual(case.status, "REVIEWED_UNCHANGED")
        self.assertTrue(CourseResult.objects.get(pk=self.result.pk).is_current)

    def test_revised_result_supersedes_only_after_ratification_and_original_is_preserved(self):
        self.enable()
        raw = list(MarkEntry.objects.filter(attempt=self.result.attempt).values_list("assessment_id", "marks"))
        case = revaluation.request_revaluation(self.p_sch1, self.result, reason="recount")
        for actor in (self.coord, self.coe, self.rnd):       # original chain / non-reviewers
            with self.assertRaises(PermissionDenied):
                revaluation.review_revaluation(actor, case, revised_marks={self.et: 40}, remarks="r")
        with self.assertRaises(ValidationError):
            revaluation.review_revaluation(self.reviewer, case, revised_marks={self.et: 999}, remarks="r")
        revaluation.review_revaluation(self.reviewer, case, revised_marks={self.et: 45}, remarks="Missed page")
        revised = case.revised_result
        self.assertEqual(case.status, "REVISED_PENDING")
        self.assertFalse(revised.is_current)
        self.assertTrue(CourseResult.objects.get(pk=self.result.pk).is_current)

        from coursework.academic import results
        results.verify_result(self.rnd2, revised)
        results.ratify_result(self.coe, revised, acknowledge_provisional=True)
        original = CourseResult.objects.get(pk=self.result.pk)
        self.assertFalse(original.is_current)
        self.assertEqual((original.status, original.outcome, original.total_marks),
                         ("RATIFIED", self.result.outcome, self.result.total_marks))
        self.assertTrue(CourseResult.objects.get(pk=revised.pk).is_current)
        self.assertEqual(RevaluationCase.objects.get(pk=case.pk).status, "COMPLETED")
        self.assertEqual(list(MarkEntry.objects.filter(attempt=self.result.attempt)
                              .values_list("assessment_id", "marks")), raw)
        self.assertEqual(RevaluationMark.objects.get(case=case).original_marks, 20)
        with self.assertRaises(PermissionError):
            RevaluationMark.objects.get(case=case).save()

    def test_returned_revision_rejects_case(self):
        self.enable()
        case = revaluation.request_revaluation(self.p_sch1, self.result, reason="recount")
        revaluation.review_revaluation(self.reviewer, case, revised_marks={self.et: 45}, remarks="r")
        from coursework.academic import results
        results.return_result(self.rnd2, case.revised_result, reason="Not justified")
        self.assertEqual(RevaluationCase.objects.get(pk=case.pk).status, "REJECTED")
        self.assertTrue(CourseResult.objects.get(pk=self.result.pk).is_current)
