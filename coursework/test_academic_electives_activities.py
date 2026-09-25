from django.core.exceptions import PermissionDenied, ValidationError

from coursework.academic import activities, electives, examination, marks, results, structure
from coursework.academic.enrollment import enroll
from coursework.academic.testing import TODAY, AcademicFixture
from coursework.models import (ActivitySubmission, Course, CourseOffering, ElectiveProposal, ElectiveProposalEvent)
from identity.capabilities import Capability as C
from identity.models import CapabilityAssignment
from supervision.models import SupervisorAssignment


class ElectiveTests(AcademicFixture):
    def setUp(self):
        super().setUp()
        self.elective = Course.objects.create(code="SIS7201", title="Elective", credits=3, category="ELECTIVE",
                                              required_for_all=False, applicable_categories=["II", "III"])
        self.sup = self.faculty(self.d1)
        CapabilityAssignment.objects.create(person=self.sup, capability=C.SUPERVISOR, scope_type="INSTITUTION",
                                            valid_from=TODAY, basis="x")
        SupervisorAssignment.objects.create(scholar=self.sch1, faculty=self.sup.faculty_profile, kind="SUPERVISOR",
                                            start_date=TODAY, approved_on=TODAY)
        self.approved_list(self.elective)

    def propose(self, actor=None, scholar=None, **kw):
        return electives.propose(actor or self.p_sch1, scholar or self.sch1, semester=self.sem,
                                 course=kw.pop("course", self.elective), justification="Needed for my research",
                                 research_relevance="Direct", **kw)

    def test_proposal_by_scholar_with_history(self):
        p = self.propose()
        self.assertEqual((p.status, p.credits), ("SUBMITTED", 3))
        self.assertEqual(ElectiveProposalEvent.objects.get(proposal=p).action, "SUBMITTED")

    def test_cross_scholar_and_category_checks(self):
        with self.assertRaises(PermissionDenied):
            self.propose(actor=self.p_sch2)
        with self.assertRaises(ValidationError):
            self.propose(actor=self.p_sch3, scholar=self.sch3)      # BTECH -> category I, not open
        with self.assertRaises(ValidationError):
            self.propose(course=self.stats)                          # not an elective

    def test_only_own_supervisor_recommends(self):
        p = self.propose()
        for actor in (self.coord, self.dean, self.p_sch1):
            with self.assertRaises(PermissionDenied):
                electives.supervisor_recommend(actor, p, recommend=True)
        electives.supervisor_recommend(self.sup, p, recommend=True, remarks="relevant")
        self.assertEqual(ElectiveProposal.objects.get(pk=p.pk).status, "SUPERVISOR_RECOMMENDED")

    def test_decision_blocked_while_authority_unresolved(self):
        self.configure("elective.supervisor_recommendation_required", False)
        p = self.propose()
        with self.assertRaisesMessage(ValidationError, "RD-38"):
            electives.decide(self.dean, p, approve=True)

    def test_decision_blocked_while_supervisor_recommendation_rule_unresolved(self):
        self.configure("elective.approval_authority", ["DEAN_RND"])
        p = self.propose()
        with self.assertRaisesMessage(ValidationError, "elective.supervisor_recommendation_required"):
            electives.decide(self.dean, p, approve=True)
        self.assertEqual(ElectiveProposal.objects.get(pk=p.pk).status, "SUBMITTED")

    def test_recommendation_required_blocks_decision_until_supervisor_recommends(self):
        self.configure("elective.approval_authority", ["DEAN_RND"])
        self.configure("elective.supervisor_recommendation_required", True)
        p = self.propose()
        with self.assertRaises(ValidationError):
            electives.decide(self.dean, p, approve=True)
        electives.supervisor_recommend(self.sup, p, recommend=True, remarks="relevant")
        electives.decide(self.dean, ElectiveProposal.objects.get(pk=p.pk), approve=True)
        self.assertEqual(ElectiveProposal.objects.get(pk=p.pk).status, "APPROVED")

    def test_proposal_requires_course_on_approved_list(self):
        other = Course.objects.create(code="SIS7299", title="Unlisted", credits=3, category="ELECTIVE",
                                      required_for_all=False)
        with self.assertRaises(ValidationError):
            self.propose(course=other)

    def test_configured_authority_decides_and_enables_enrollment(self):
        self.configure("elective.approval_authority", ["DEAN_RND"])
        self.configure("elective.supervisor_recommendation_required", False)
        p = self.propose()
        with self.assertRaises(PermissionDenied):
            electives.decide(self.coe, p, approve=True)
        electives.decide(self.dean, p, approve=True, remarks="ok")
        p.refresh_from_db()
        self.assertEqual((p.status, p.decided_as), ("APPROVED", "DEAN_RND"))
        off = CourseOffering.objects.create(course=self.elective, semester=self.sem, status="OPEN")
        e = enroll(self.phd, self.sch1, off)
        self.assertEqual(e.elective_proposal, p)


class ActivityTests(AcademicFixture):
    def setUp(self):
        super().setUp()
        self.it = Course.objects.get(code="SIS7005")
        self.conf = Course.objects.get(code="SIS7006")
        self.it_off = CourseOffering.objects.create(course=self.it, semester=self.sem, status="OPEN")
        self.conf_off = CourseOffering.objects.create(course=self.conf, semester=self.sem, status="OPEN")
        self.configure("activity.scoring_scheme.CONFERENCE_WORKSHOP", [["SDRC evaluation", 100, 100]])
        structure.define_activity_assessments(self.acad, self.conf_off)
        self.e_it = self.enroll(self.sch1, offering=self.it_off)
        self.e_conf = self.enroll(self.sch1, offering=self.conf_off)

    def test_industrial_training_follows_documented_order(self):
        with self.assertRaises(ValidationError):
            activities.submit_artefact(self.p_sch1, self.e_it, artefact="REPORT")
        for artefact in ("PLAN", "LOGBOOK", "REPORT", "POSTER"):
            activities.submit_artefact(self.p_sch1, self.e_it, artefact=artefact)
        with self.assertRaises(ValidationError):
            activities.submit_artefact(self.p_sch1, self.e_it, artefact="REFLECTIVE_NOTE")

    def test_only_owner_submits_and_only_sdrc_of_school_reviews(self):
        with self.assertRaises(PermissionDenied):
            activities.submit_artefact(self.p_sch2, self.e_conf, artefact="REFLECTIVE_NOTE")
        s = activities.submit_artefact(self.p_sch1, self.e_conf, artefact="REFLECTIVE_NOTE")
        for actor in (self.coord, self.sdrc_s2, self.p_sch1, self.coe):
            with self.assertRaises(PermissionDenied):
                activities.review_artefact(actor, s, accept=True)
        activities.review_artefact(self.sdrc, s, accept=True)

    def test_reflective_note_must_be_evaluated_before_result(self):
        attempt = examination.open_activity_evaluation(self.sdrc, self.e_conf)
        assessment = self.conf_off.assessments.get()
        with self.assertRaises(PermissionDenied):
            marks.enter_mark(self.coord, assessment, attempt, marks=70)     # not the evaluation body
        marks.enter_mark(self.sdrc, assessment, attempt, marks=70)
        with self.assertRaisesMessage(ValidationError, "REFLECTIVE_NOTE"):
            results.prepare_result(self.sdrc, attempt)
        s = activities.submit_artefact(self.p_sch1, self.e_conf, artefact="REFLECTIVE_NOTE")
        activities.review_artefact(self.sdrc, s, accept=True)
        r = results.prepare_result(self.sdrc, attempt)
        self.assertEqual((r.outcome, r.prepared_as), ("PASS", "SDRC_MEMBER"))
        results.verify_result(self.rnd, r)
        results.ratify_result(self.coe, r, acknowledge_provisional=True)

    def test_coordinator_cannot_prepare_activity_result_and_sdrc_cannot_prepare_course(self):
        attempt = examination.open_activity_evaluation(self.sdrc, self.e_conf)
        with self.assertRaises(PermissionDenied):
            results.prepare_result(self.coord, attempt)
        e = self.enroll(self.sch2, self.sec_a)
        a = self.register(e).attempt
        with self.assertRaises(PermissionDenied):
            results.prepare_result(self.sdrc, a)

    def test_returned_artefact_needs_remarks_and_can_be_resubmitted(self):
        s = activities.submit_artefact(self.p_sch1, self.e_conf, artefact="REFLECTIVE_NOTE")
        with self.assertRaises(ValidationError):
            activities.review_artefact(self.sdrc, s, accept=False)
        activities.review_artefact(self.sdrc, s, accept=False, remarks="Too short")
        s2 = activities.submit_artefact(self.p_sch1, self.e_conf, artefact="REFLECTIVE_NOTE")
        self.assertEqual(ActivitySubmission.objects.filter(enrollment=self.e_conf).count(), 2)
        self.assertEqual(s2.status, "SUBMITTED")
