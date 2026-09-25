from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from coursework.academic import config, examination, results, third_attempt
from coursework.academic.testing import TODAY, AcademicFixture
from coursework.models import (CourseAttempt, Exam, ExamCycle, ExamEligibility, ExamRegistration, ThirdAttemptCase,
                               ThirdAttemptCaseEvent)


class ExamCycleTests(AcademicFixture):
    def test_cycle_dates_ordered_and_one_per_semester(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ExamCycle.objects.create(semester=self.sem, name="dup", registration_opens=TODAY,
                                     registration_closes=TODAY, exam_start=TODAY, exam_end=TODAY)
        with self.assertRaises(ValidationError):
            examination.create_exam_cycle(self.coe, semester=self.sem, name="bad", registration_opens=TODAY,
                                          registration_closes=TODAY - timedelta(days=1), exam_start=TODAY,
                                          exam_end=TODAY)

    def test_exam_within_cycle_and_not_for_activities(self):
        from coursework.models import Course
        with self.assertRaises(ValidationError):
            examination.create_exam(self.coe, cycle=self.cycle, course=Course.objects.get(code="SIS7006"),
                                    scheduled_on=self.cycle.exam_start, start_time=self.exam.start_time,
                                    end_time=self.exam.end_time)
        with self.assertRaises(ValidationError):
            examination.create_exam(self.coe, cycle=self.cycle, course=Course.objects.get(code="SIS7003"),
                                    scheduled_on=self.cycle.exam_end + timedelta(days=1),
                                    start_time=self.exam.start_time, end_time=self.exam.end_time)
        with self.assertRaises(PermissionDenied):
            examination.create_exam(self.coord, cycle=self.cycle, course=Course.objects.get(code="SIS7003"),
                                    scheduled_on=self.cycle.exam_start, start_time=self.exam.start_time,
                                    end_time=self.exam.end_time)


class ExamRegistrationTests(AcademicFixture):
    def setUp(self):
        super().setUp()
        self.e1 = self.enroll(self.sch1, self.sec_a)

    def test_self_registration_creates_attempt_and_persisted_eligibility(self):
        reg = examination.register_for_exam(self.p_sch1, self.e1, self.exam)
        self.assertEqual((reg.attempt_no, reg.attempt.status, reg.attempt.enrollment), (1, "SCHEDULED", self.e1))
        self.assertTrue(reg.eligibility.eligible)
        self.assertIn("exam.max_regular_attempts", reg.eligibility.rule_versions)

    def test_unauthorised_registration_denied(self):
        for actor in (self.p_sch2, self.coord, self.inst_a, self.dean, self.sysadmin):
            with self.assertRaises(PermissionDenied, msg=str(actor)):
                examination.register_for_exam(actor, self.e1, self.exam)
        self.assertFalse(ExamRegistration.objects.exists())

    def test_duplicate_registration_blocked(self):
        self.register(self.e1)
        with self.assertRaises(PermissionDenied):   # pending attempt makes the second one ineligible
            self.register(self.e1)
        self.assertEqual(ExamRegistration.objects.count(), 1)

    def test_registration_window_enforced(self):
        ExamCycle.objects.filter(pk=self.cycle.pk).update(registration_closes=TODAY - timedelta(days=1),
                                                          registration_opens=TODAY - timedelta(days=3))
        self.exam.refresh_from_db()
        with self.assertRaises(PermissionDenied):
            self.register(self.e1)
        elig = ExamEligibility.objects.latest("id")
        self.assertFalse(elig.eligible)
        self.assertIn("Registration window", elig.reasons[0])

    def test_passed_course_and_pending_result_block(self):
        self.full_attempt(self.e1, ca=80, et=80)
        with self.assertRaises(PermissionDenied):
            self.register(self.e1, self.next_exam())
        self.assertIn("already been passed", " ".join(ExamEligibility.objects.latest("id").reasons))

    def test_reattempt_blocked_while_ca_carry_forward_unresolved(self):
        self.full_attempt(self.e1, ca=10, et=10)
        reg = self.register(self.e1, self.next_exam())
        self.grade(reg.attempt, ca=60, et=60)
        with self.assertRaisesMessage(ValidationError, "exam.reattempt_carries_continuous_assessment"):
            results.prepare_result(self.coord, reg.attempt)

    def test_attempt_counting_and_third_attempt_requirement(self):
        self.configure("exam.reattempt_carries_continuous_assessment", False)
        self.full_attempt(self.e1, ca=10, et=10)
        a2 = self.full_attempt(self.e1, ca=10, et=10, exam=self.next_exam())
        self.assertEqual(a2.attempt.attempt_no, 2)
        with self.assertRaises(PermissionDenied):
            self.register(self.e1, self.next_exam())
        self.assertIn("third-attempt case", " ".join(ExamEligibility.objects.latest("id").reasons))

    def test_attendance_shortfall_blocks_full_time_scholar(self):
        from datetime import time
        from coursework.academic import attendance
        for d in range(4):
            s = attendance.create_session(self.inst_a, self.sec_a, date=TODAY - timedelta(days=d + 1),
                                          start_time=time(9), end_time=time(10))
            attendance.record_attendance(self.inst_a, s, self.e1, status="PRESENT" if d == 0 else "ABSENT",
                                         mode="PHYSICAL" if d == 0 else "")
        with self.assertRaises(PermissionDenied):
            self.register(self.e1)
        self.assertIn("below the required 75", " ".join(ExamEligibility.objects.latest("id").reasons))

    def test_part_time_attendance_rule_unresolved_blocks(self):
        from coursework.academic.testing import admit
        pt, _ = self.scholar(self.d1, category="PT_EXTERNAL")
        admit(pt, self.sem, self.phd)
        e = self.enroll(pt, self.sec_a)
        with self.assertRaises(PermissionDenied):
            self.register(e)
        self.assertIn("attendance.min_percent", " ".join(ExamEligibility.objects.latest("id").reasons))
        self.configure("attendance.min_percent", {"FT": 75, "PT": 75}, status="AMBIGUOUS")
        self.assertTrue(self.register(e).eligibility.eligible)

    def test_fee_clearance_and_semester_registration_required(self):
        from coursework.models import FeeClearance, SemesterRegistration
        FeeClearance.objects.filter(scholar=self.sch1, semester=self.sem).update(cleared=False)
        with self.assertRaises(PermissionDenied):
            self.register(self.e1)
        self.assertIn("Fee / dues clearance", " ".join(ExamEligibility.objects.latest("id").reasons))
        FeeClearance.objects.filter(scholar=self.sch1, semester=self.sem).update(cleared=True)
        SemesterRegistration.objects.filter(scholar=self.sch1, semester=self.sem).update(status="CANCELLED")
        with self.assertRaises(PermissionDenied):
            self.register(self.e1)
        self.assertIn("not registered for", " ".join(ExamEligibility.objects.latest("id").reasons))

    def test_absent_attempt_treatment_unresolved_blocks(self):
        self.configure("result.absence_policy", "ABSENT_IF_ANY_COMPONENT_ABSENT")
        self.full_attempt(self.e1, ca=60, et=None, absent=True)
        with self.assertRaises(PermissionDenied):
            self.register(self.e1, self.next_exam())
        self.assertIn("exam.absent_counts_as_attempt", " ".join(ExamEligibility.objects.latest("id").reasons))
        self.configure("exam.absent_counts_as_attempt", True)
        self.assertEqual(self.register(self.e1, self.next_exam()).attempt_no, 2)

    def test_invalid_attempt_numbers_blocked_in_db(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            CourseAttempt.objects.create(scholar=self.sch1, course=self.stats, attempt_no=4, exam_date=TODAY)
        with self.assertRaises(IntegrityError), transaction.atomic():
            CourseAttempt.objects.create(scholar=self.sch1, course=self.stats, attempt_no=0, exam_date=TODAY)


class ThirdAttemptTests(AcademicFixture):
    def setUp(self):
        super().setUp()
        self.e1 = self.enroll(self.sch1, self.sec_a)

    def exhaust(self):
        self.configure("exam.reattempt_carries_continuous_assessment", False)
        self.configure("third_attempt.mentor_required", False)
        self.full_attempt(self.e1, ca=10, et=10)
        self.full_attempt(self.e1, ca=10, et=10, exam=self.next_exam())

    def test_case_only_when_attempts_exhausted(self):
        with self.assertRaises(ValidationError):
            third_attempt.submit_case(self.p_sch1, self.e1, reason="illness")
        self.exhaust()
        case = third_attempt.submit_case(self.p_sch1, self.e1, reason="illness")
        self.assertEqual((case.attempts_used, len(case.previous_attempts)), (2, 2))
        with self.assertRaises(ValidationError):  # one open case per course
            third_attempt.submit_case(self.p_sch1, self.e1, reason="again")

    def test_dean_then_vc_with_separation_of_duties(self):
        self.exhaust()
        case = third_attempt.submit_case(self.p_sch1, self.e1, reason="illness")
        with self.assertRaises(PermissionDenied):      # VC before Dean
            third_attempt.vc_decide(self.vc, case, approve=True, remarks="x")
        for actor in (self.p_sch1, self.vc, self.coe, self.acad, self.sysadmin):
            with self.assertRaises(PermissionDenied):
                third_attempt.dean_review(actor, case, recommend=True, remarks="x")
        third_attempt.dean_review(self.dean, case, recommend=True, remarks="genuine")
        for actor in (self.dean, self.p_sch1, self.coe):
            with self.assertRaises(PermissionDenied):
                third_attempt.vc_decide(actor, case, approve=True, remarks="x")
        third_attempt.vc_decide(self.vc, case, approve=True, remarks="approved")
        self.assertEqual([e.action for e in ThirdAttemptCaseEvent.objects.filter(case=case)],
                         ["SUBMITTED", "DEAN_RECOMMENDED", "VC_APPROVED"])

    def test_approved_case_enables_third_attempt_once(self):
        self.exhaust()
        case = third_attempt.submit_case(self.p_sch1, self.e1, reason="illness")
        third_attempt.dean_review(self.dean, case, recommend=True, remarks="ok")
        third_attempt.vc_decide(self.vc, case, approve=True, remarks="ok")
        reg = self.register(self.e1, self.next_exam())
        self.assertEqual((reg.attempt_no, reg.third_attempt_case), (3, case))
        case.refresh_from_db()
        self.assertEqual(case.status, ThirdAttemptCase.Status.CONSUMED)

    def test_rejected_case_gives_no_attempt(self):
        self.exhaust()
        case = third_attempt.submit_case(self.p_sch1, self.e1, reason="illness")
        third_attempt.dean_review(self.dean, case, recommend=False, remarks="no")
        third_attempt.vc_decide(self.vc, case, approve=False, remarks="no")
        with self.assertRaises(PermissionDenied):
            self.register(self.e1, self.next_exam())

    def test_third_attempt_registration_requires_case_in_db(self):
        from coursework.models import ExamEligibility
        a = CourseAttempt.objects.create(scholar=self.sch1, course=self.stats, attempt_no=3, exam_date=TODAY,
                                         enrollment=self.e1)
        el = ExamEligibility.objects.create(exam=self.exam, enrollment=self.e1, eligible=True, attempt_no=3)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ExamRegistration.objects.create(exam=self.exam, enrollment=self.e1, attempt=a, attempt_no=3,
                                            eligibility=el, registered_by=self.phd)


class ThirdAttemptMentorRuleTests(AcademicFixture):
    def test_vc_approval_blocked_while_mentor_rule_unresolved(self):
        e1 = self.enroll(self.sch1, self.sec_a)
        self.configure("exam.reattempt_carries_continuous_assessment", False)
        self.full_attempt(e1, ca=10, et=10)
        self.full_attempt(e1, ca=10, et=10, exam=self.next_exam())
        case = third_attempt.submit_case(self.p_sch1, e1, reason="illness")
        third_attempt.dean_review(self.dean, case, recommend=True, remarks="ok")
        with self.assertRaisesMessage(ValidationError, "third_attempt.mentor_required"):
            third_attempt.vc_decide(self.vc, case, approve=True, remarks="ok")
        self.configure("third_attempt.mentor_required", True)
        with self.assertRaisesMessage(ValidationError, "mentor must be assigned"):
            third_attempt.vc_decide(self.vc, case, approve=True, remarks="ok")
        third_attempt.vc_decide(self.vc, case, approve=True, remarks="ok", mentor=self.coord.faculty_profile)

    def test_legacy_third_attempt_chain_is_retired(self):
        from django.core.exceptions import ValidationError as VE
        from core.approvals import start_approval
        with self.assertRaises(VE):
            start_approval("COURSEWORK_THIRD_ATTEMPT", summary="old path")
