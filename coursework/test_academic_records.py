from django.core.exceptions import PermissionDenied

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from coursework.academic import records
from coursework.academic.testing import TODAY, AcademicFixture, complete_coursework
from coursework.models import CourseAttempt, CourseResult, TranscriptIssue


class RecordsTests(AcademicFixture):
    def test_profile_uses_configured_category_and_requirements(self):
        prof = records.academic_profile(self.sch1)
        self.assertEqual((prof["coursework_category"], prof["required_credits"], prof["electives_required"]),
                         ("II", 17, 1))

    def test_coursework_completion_and_legacy_sync(self):
        status = records.coursework_status(self.sch1)
        self.assertFalse(status["complete"])
        complete_coursework(self.sch1)
        status = records.coursework_status(self.sch1)
        self.assertTrue(status["complete"], status["reasons"])
        self.assertEqual(status["credits_earned"], 17)
        self.sch1.refresh_from_db()
        self.assertEqual(self.sch1.coursework_completed_on, TODAY)     # lifecycle gate reads this
        self.assertEqual(records.academic_standing(self.sch1)["standing"], "COURSEWORK_COMPLETE")

    def test_standing_requires_third_attempt_after_two_failures(self):
        self.configure("exam.reattempt_carries_continuous_assessment", False)
        e = self.enroll(self.sch1)
        self.full_attempt(e, ca=10, et=10)
        self.assertEqual(records.academic_standing(self.sch1)["standing"], "IN_PROGRESS")
        self.full_attempt(e, ca=10, et=10, exam=self.next_exam())
        standing = records.academic_standing(self.sch1)
        self.assertEqual(standing["standing"], "THIRD_ATTEMPT_REQUIRED")
        self.assertEqual(standing["courses"]["SIS7002"]["counted_failures"], 2)

    def test_transcript_derived_from_ratified_results_only(self):
        e = self.enroll(self.sch1)
        reg = self.register(e)
        self.grade(reg.attempt, ca=90, et=80)
        from coursework.academic import results
        r = results.prepare_result(self.coord, reg.attempt)
        self.assertEqual(records.transcript(self.p_sch1, self.sch1)["rows"], [])   # not yet ratified
        results.verify_result(self.rnd, r)
        results.ratify_result(self.coe, r, acknowledge_provisional=True)
        t = records.transcript(self.p_sch1, self.sch1)
        row = t["rows"][0]
        self.assertEqual((row["course_code"], row["grade"], row["credits_earned"], row["attempt_no"]),
                         ("SIS7002", "A", 2, 1))
        self.assertTrue(t["provisional"])
        self.assertIn("not independently stored", t["source"])

    def test_transcript_access(self):
        records.transcript(self.rnd, self.sch1)
        records.transcript(self.dept_admin, self.sch1)
        for actor in (self.p_sch2, self.coord, self.inst_a, self.sysadmin, self.vc):
            with self.assertRaises(PermissionDenied, msg=str(actor)):
                records.transcript(actor, self.sch1)
        with self.assertRaises(PermissionDenied):
            records.transcript(self.dept_admin, self.sch3)       # other school's scholar


class TranscriptIssueTests(AcademicFixture):
    def ratified(self):
        e = self.enroll(self.sch1)
        return self.full_attempt(e, ca=90, et=80)

    def test_nothing_to_issue_without_ratified_results(self):
        with self.assertRaises(ValidationError):
            records.issue_transcript(self.coe, self.sch1)

    def test_only_coe_issues(self):
        self.ratified()
        for actor in (self.rnd, self.dean, self.p_sch1, self.coord, self.acad):
            with self.assertRaises(PermissionDenied, msg=str(actor)):
                records.issue_transcript(actor, self.sch1)

    def test_issue_is_versioned_hashed_and_verifiable(self):
        self.ratified()
        first = records.issue_transcript(self.coe, self.sch1)
        self.assertEqual(first.version, 1)
        self.assertEqual(first.content_hash, records.content_hash(records.transcript_data(self.sch1)))
        v = records.verify_transcript(first.verification_code)
        self.assertTrue(v["valid"] and v["matches_current_records"])
        self.assertIsNone(v["superseded_by_version"])
        second = records.issue_transcript(self.coe, self.sch1)
        self.assertEqual(second.version, 2)
        self.assertEqual(records.verify_transcript(first.verification_code)["superseded_by_version"], 2)
        self.assertFalse(records.verify_transcript("nope")["valid"])

    def test_verification_detects_changed_records(self):
        from coursework.academic import examination, marks, structure
        from coursework.academic.enrollment import enroll
        from coursework.models import Course, CourseOffering
        self.ratified()
        issue = records.issue_transcript(self.coe, self.sch1)
        course = Course.objects.get(code="SIS7001")
        off = CourseOffering.objects.create(course=course, semester=self.sem, status="OPEN")
        structure.define_default_assessments(self.acad, off)
        self.assign(self.coord, off, "COURSE_COORDINATOR")
        attempt = examination.register_for_exam(self.phd, enroll(self.phd, self.sch1, off),
                                                self.next_exam(course=course)).attempt
        for asmt in off.assessments.all():
            if asmt.kind == "MANDATORY_MODULE":
                marks.enter_mark(self.coord, asmt, attempt, cleared=True)
            else:
                marks.enter_mark(self.coord, asmt, attempt, marks=70)
        self.ratify(attempt)
        v = records.verify_transcript(issue.verification_code)
        self.assertTrue(v["valid"])
        self.assertFalse(v["matches_current_records"])

    def test_transcript_issue_is_append_only_and_carries_no_marks(self):
        self.ratified()
        issue = records.issue_transcript(self.coe, self.sch1)
        field_names = {f.name for f in TranscriptIssue._meta.get_fields()}
        self.assertFalse(field_names & {"marks", "grade", "grade_point", "signature", "seal"})
        issue.content_hash = "x" * 64
        with self.assertRaises(PermissionError):
            issue.save()
        with self.assertRaises(PermissionError):
            TranscriptIssue.objects.get(pk=issue.pk).delete()

    def test_transcript_rows_are_not_editable_through_the_record(self):
        r = self.ratified()
        r.total_marks = 99
        with self.assertRaises(PermissionError):
            r.save()
        with self.assertRaises(PermissionError):
            CourseResult.objects.get(pk=r.pk).delete()


class LegacyRecordTests(AcademicFixture):
    def test_legacy_attempts_are_not_counted_and_block_completion(self):
        CourseAttempt.objects.create(scholar=self.sch1, course=self.stats, attempt_no=1, exam_date=TODAY,
                                     marks=90, grade="A", grade_point=9)      # status defaults to LEGACY
        status = records.coursework_status(self.sch1)
        self.assertEqual(status["credits_earned"], 0)
        self.assertTrue(any("legacy" in r for r in status["reasons"]))
        self.assertEqual(records.transcript_data(self.sch1)["rows"], [])
        self.assertEqual(records.transcript_data(self.sch1)["legacy_records_excluded"], 1)

    def test_legacy_mutation_modules_are_gone(self):
        import importlib
        for name in ("coursework.services", "phd_rules.grading"):
            with self.assertRaises(ModuleNotFoundError):
                importlib.import_module(name)

    def test_retired_third_attempt_chain_cannot_be_started(self):
        from core.models import ApprovalChain
        self.assertFalse(ApprovalChain.objects.filter(code="COURSEWORK_THIRD_ATTEMPT").exists())

    def test_legacy_report_lists_without_modifying(self):
        from io import StringIO

        from django.core.management import call_command
        a = CourseAttempt.objects.create(scholar=self.sch1, course=self.stats, attempt_no=1, exam_date=TODAY,
                                         marks=90, grade="A", grade_point=9)
        out = StringIO()
        call_command("academic_legacy_report", stdout=out)
        self.assertIn("Legacy attempt records (not counted by the authoritative record): 1", out.getvalue())
        self.assertIn(self.sch1.prn, out.getvalue())
        a.refresh_from_db()
        self.assertEqual((a.status, a.grade), ("LEGACY", "A"))

    def test_attempt_number_constraint(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            CourseAttempt.objects.create(scholar=self.sch1, course=self.stats, attempt_no=4, exam_date=TODAY)
