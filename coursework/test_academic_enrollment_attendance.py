from datetime import time, timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from coursework.academic import attendance
from coursework.academic.enrollment import enroll, withdraw
from coursework.academic.testing import TODAY, AcademicFixture
from coursework.models import AttendanceCorrection, AttendanceRecord, Course, CourseOffering, ScholarCourseEnrollment


class EnrollmentTests(AcademicFixture):
    def test_scholar_self_enrollment_and_staff_enrollment(self):
        e = enroll(self.p_sch1, self.sch1, self.offering, section=self.sec_a)
        self.assertEqual((e.status, e.enrolled_by), ("ENROLLED", self.p_sch1))
        e2 = enroll(self.phd, self.sch2, self.offering, section=self.sec_b)
        self.assertEqual(e2.section, self.sec_b)

    def test_cross_scholar_and_faculty_enrollment_denied(self):
        for actor in (self.p_sch1, self.coord, self.inst_a, self.coe, self.sysadmin):
            with self.assertRaises(PermissionDenied, msg=str(actor)):
                enroll(actor, self.sch2, self.offering, section=self.sec_a)

    def test_duplicate_enrollment_blocked(self):
        self.enroll(self.sch1)
        with self.assertRaises(ValidationError):
            self.enroll(self.sch1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ScholarCourseEnrollment.objects.create(scholar=self.sch1, offering=self.offering)

    def test_section_must_belong_to_offering_and_category_must_apply(self):
        other = CourseOffering.objects.create(course=Course.objects.get(code="SIS7003"), semester=self.sem,
                                              status="OPEN")
        with self.assertRaises(ValidationError):
            enroll(self.phd, self.sch1, other, section=self.sec_a)
        only_one = Course.objects.create(code="CAT1", title="Cat I only", credits=3, category="MANDATORY",
                                         applicable_categories=["I"])
        off = CourseOffering.objects.create(course=only_one, semester=self.sem, status="OPEN")
        with self.assertRaises(ValidationError):
            enroll(self.phd, self.sch1, off)           # MTECH -> category II
        enroll(self.phd, self.sch3, off)                # BTECH -> category I

    def test_elective_requires_approved_proposal(self):
        c = Course.objects.create(code="ELX", title="E", credits=3, category="ELECTIVE", required_for_all=False)
        off = CourseOffering.objects.create(course=c, semester=self.sem, status="OPEN")
        with self.assertRaisesMessage(ValidationError, "approved elective proposal"):
            enroll(self.phd, self.sch1, off)

    def test_withdraw_only_without_attempts(self):
        e = self.enroll(self.sch1)
        self.register(e)
        with self.assertRaises(ValidationError):
            withdraw(self.phd, e, reason="x")


class AttendanceTests(AcademicFixture):
    def setUp(self):
        super().setUp()
        self.e1 = self.enroll(self.sch1, self.sec_a)
        self.e2 = self.enroll(self.sch2, self.sec_b)

    def session(self, actor=None, section=None, day=0):
        return attendance.create_session(actor or self.inst_a, section or self.sec_a,
                                         date=TODAY - timedelta(days=day), start_time=time(9), end_time=time(10))

    def test_assigned_instructor_records_attendance(self):
        s = self.session()
        r = attendance.record_attendance(self.inst_a, s, self.e1, status="PRESENT", mode="PHYSICAL")
        self.assertEqual((r.status, r.mode, r.recorded_by), ("PRESENT", "PHYSICAL", self.inst_a))
        self.assertEqual(s.faculty, self.inst_a.faculty_profile)

    def test_unassigned_and_other_section_faculty_denied(self):
        with self.assertRaises(PermissionDenied):
            self.session(actor=self.inst_b, section=self.sec_a)   # teaches B, not A
        with self.assertRaises(PermissionDenied):
            self.session(actor=self.outsider)
        s = self.session()
        for actor in (self.inst_b, self.outsider, self.p_sch1, self.coe, self.sysadmin):
            with self.assertRaises(PermissionDenied):
                attendance.record_attendance(actor, s, self.e1, status="PRESENT", mode="PHYSICAL")

    def test_coordinator_may_record_any_section(self):
        s = self.session(actor=self.coord, section=self.sec_b)
        attendance.record_attendance(self.coord, s, self.e2, status="ABSENT")

    def test_expired_assignment_denied(self):
        a = self.inst_a.faculty_profile.subject_assignments.get(section=self.sec_a)
        type(a).objects.filter(pk=a.pk).update(valid_from=TODAY - timedelta(days=30), valid_to=TODAY - timedelta(days=1))
        with self.assertRaises(PermissionDenied):
            self.session()

    def test_scholar_not_in_section_rejected(self):
        s = self.session()
        with self.assertRaises(PermissionDenied):
            attendance.record_attendance(self.inst_a, s, self.e2, status="PRESENT", mode="PHYSICAL")

    def test_duplicate_session_and_record_blocked(self):
        s = self.session()
        with self.assertRaises(ValidationError):
            self.session()
        attendance.record_attendance(self.inst_a, s, self.e1, status="ABSENT")
        with self.assertRaises(IntegrityError), transaction.atomic():
            AttendanceRecord.objects.create(session=s, enrollment=self.e1, status="ABSENT",
                                            recorded_by=self.inst_a)

    def test_correction_requires_reason_and_keeps_history(self):
        s = self.session()
        attendance.record_attendance(self.inst_a, s, self.e1, status="ABSENT")
        with self.assertRaises(ValidationError):
            attendance.record_attendance(self.inst_a, s, self.e1, status="PRESENT", mode="PHYSICAL")
        attendance.record_attendance(self.inst_a, s, self.e1, status="PRESENT", mode="PHYSICAL",
                                     reason="Marked in error")
        c = AttendanceCorrection.objects.get()
        self.assertEqual((c.old_status, c.new_status, c.corrected_by), ("ABSENT", "PRESENT", self.inst_a))
        with self.assertRaises(PermissionError):
            c.save()

    def test_percentage_computed_server_side_physical_only(self):
        for day, (status, mode) in enumerate([("PRESENT", "PHYSICAL"), ("PRESENT", "ONLINE"),
                                              ("ABSENT", ""), ("PRESENT", "PHYSICAL")]):
            s = self.session(day=day)
            attendance.record_attendance(self.inst_a, s, self.e1, status=status, mode=mode)
        summary = attendance.attendance_summary(self.e1)
        self.assertEqual((summary["sessions_held"], summary["sessions_attended"], str(summary["percent"])),
                         (4, 2, "50.00"))

    def test_mode_consistency_in_db(self):
        s = self.session()
        with self.assertRaises(IntegrityError), transaction.atomic():
            AttendanceRecord.objects.create(session=s, enrollment=self.e1, status="PRESENT", mode="",
                                            recorded_by=self.inst_a)
