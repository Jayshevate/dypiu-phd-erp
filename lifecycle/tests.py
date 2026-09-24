from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase

from core import testing as t
from core.models import Committee
from coursework.models import Course
from coursework.tests import pass_core
from coursework.services import record_result, register_attempt
from lifecycle import engine, services
from lifecycle.models import DCReview, ProgressReport, ResearchProposal, Synopsis, Thesis, Viva
from scholars.models import Phase, Status
from supervision.services import propose_supervisor, propose_tac


def nominees(n=8):
    return [dict(name=f"Examiner {i}", affiliation="IIT", email=f"e{i}@example.org") for i in range(n)]


class LifecycleTests(TestCase):
    def setUp(self):
        t.seed()
        self.dept, self.other = t.department(), t.department()
        self.s = t.scholar(dept=self.dept, entry_qualification="MTECH")

    def test_admission_gate(self):
        self.s.registration_fee_paid = False
        self.s.save()
        gate = engine.evaluate_gate(self.s)
        self.assertIn("Registration fee not paid", gate.reasons)
        with self.assertRaises(engine.TransitionBlocked):
            engine.advance(self.s)

    def test_sponsored_part_time_needs_letter(self):
        s = t.scholar(category="PT_SPONSORED")
        self.assertIn("Sponsorship / NOC letter required for this part-time category", engine.evaluate_gate(s).reasons)

    def test_full_journey_to_award(self):
        s = self.s
        s = engine.advance_all(s)
        self.assertEqual(s.phase, Phase.COURSEWORK)

        # Supervisor + TAC during coursework
        t.approve_all(propose_supervisor(s, t.faculty(dept=self.dept)).approval)
        t.approve_all(propose_tac(s, [t.faculty(dept=self.other), t.faculty(dept=self.other)]))

        pass_core(s)
        elective = Course.objects.create(code="SIS7008-X", title="Elective", credits=3, category="ELECTIVE",
                                         required_for_all=False)
        record_result(register_attempt(s, elective, date(2027, 5, 5)), marks=88)
        s = engine.advance_all(s)
        self.assertEqual(s.phase, Phase.RESEARCH_PROPOSAL)  # coursework + supervisor/TAC gates both passed

        p = ResearchProposal.objects.create(scholar=s, title="T", submitted_on=date(2027, 9, 1))
        services.record_proposal_outcome(p, ResearchProposal.Outcome.RECOMMENDED)
        s = engine.advance_all(s)
        self.assertEqual(s.phase, Phase.PROGRESS)

        r = ProgressReport.objects.create(scholar=s, period_no=1, due_date=date(2027, 2, 1), submitted_on=date(2027, 1, 20))
        services.record_tac_review(r, ProgressReport.Outcome.SATISFACTORY, date(2027, 1, 28))
        self.assertIn("Pre-submission synopsis not submitted", engine.evaluate_gate(s).reasons)
        syn = Synopsis.objects.create(scholar=s, submitted_on=date(2029, 6, 1))
        s = engine.advance_all(s)
        self.assertEqual(s.phase, Phase.SYNOPSIS)
        syn.tac_cleared_on = date(2029, 7, 1)
        syn.save()
        s = engine.advance_all(s)
        self.assertEqual(s.phase, Phase.THESIS_SUBMISSION)

        thesis = Thesis.objects.create(scholar=s, title="T", submitted_on=date(2029, 9, 1),
                                       plagiarism_percent=12, fees_paid=True)
        self.assertTrue(any("Plagiarism" in r for r in engine.evaluate_gate(s).reasons))
        thesis.plagiarism_percent = 8
        thesis.save()
        s = engine.advance_all(s)
        self.assertEqual(s.phase, Phase.THESIS_EVALUATION)

        with self.assertRaises(ValidationError):
            services.submit_examiner_panel(thesis, nominees(7))
        t.approve_all(services.submit_examiner_panel(thesis, nominees()))
        thesis.refresh_from_db()
        chosen = list(thesis.nominations.all()[:3])
        services.select_examiners(thesis, chosen, date(2029, 10, 1))
        verdicts = ["COMMEND", "NOT_COMMEND", "COMMEND"]
        for n, v in zip(chosen, verdicts):
            n.report.received_on, n.report.recommendation = date(2029, 12, 1), v
            n.report.save()
        s = engine.advance_all(s)
        self.assertEqual(s.phase, Phase.VIVA)

        dpep = Committee.objects.create(type="DPEP", name="DPEP")
        late = Viva(thesis=thesis, dpep=dpep, scheduled_on=date(2030, 3, 1))
        with self.assertRaisesMessage(ValidationError, "2 months"):
            services.validate_viva(late)
        viva = Viva(thesis=thesis, dpep=dpep, scheduled_on=date(2030, 1, 20),
                    candidate_notified_on=date(2030, 1, 10))
        with self.assertRaisesMessage(ValidationError, "notified by"):
            services.validate_viva(viva)
        viva.candidate_notified_on = date(2030, 1, 5)
        viva.invitation_published_on = date(2030, 1, 13)
        services.validate_viva(viva)
        viva.outcome = Viva.Outcome.SATISFACTORY
        viva.save()
        s = engine.advance_all(s)
        self.assertEqual(s.phase, Phase.DEGREE_AWARD)

        award = services.start_degree_award(s)
        self.assertEqual(award.approval.chain.steps.count(), 5)
        t.approve_all(award.approval)
        award.refresh_from_db()
        award.issued_on, award.certificate_no = date(2030, 3, 1), "DYPIU/PHD/2030/001"
        award.save()
        s = engine.advance_all(s)
        self.assertEqual(s.status, Status.AWARDED)
        self.assertEqual(s.transitions.count(), 10)

    def test_minimum_duration_blocks_early_thesis(self):
        s = self.s
        s.phase = Phase.THESIS_SUBMISSION
        s.save()
        Thesis.objects.create(scholar=s, title="T", submitted_on=date(2029, 1, 1), plagiarism_percent=1, fees_paid=True)
        self.assertTrue(any("Minimum duration" in r for r in engine.evaluate_gate(s).reasons))

    def test_two_failed_proposals_cancel_registration(self):
        p1 = ResearchProposal.objects.create(scholar=self.s, title="T", submitted_on=date(2027, 9, 1))
        services.record_proposal_outcome(p1, ResearchProposal.Outcome.RESUBMIT)
        self.s.refresh_from_db()
        self.assertEqual(self.s.status, Status.ACTIVE)
        p2 = ResearchProposal.objects.create(scholar=self.s, attempt_no=2, title="T", submitted_on=date(2028, 1, 1))
        services.record_proposal_outcome(p2, ResearchProposal.Outcome.NOT_RECOMMENDED)
        self.s.refresh_from_db()
        self.assertEqual(self.s.status, Status.CANCELLED)
        self.assertFalse(engine.evaluate_gate(self.s).open)

    def test_two_warnings_open_dc_review(self):
        reports = [ProgressReport.objects.create(scholar=self.s, period_no=i, due_date=date(2027, 2, 1))
                   for i in (1, 2, 3)]
        self.assertIsNone(services.record_tac_review(reports[0], "UNSATISFACTORY", date(2027, 2, 5)))
        self.assertIsNone(services.record_tac_review(reports[1], "SATISFACTORY", date(2027, 8, 5)))
        review = services.record_tac_review(reports[2], "UNSATISFACTORY", date(2028, 2, 5))
        self.assertIsNotNone(review)
        self.assertEqual(review.warnings.count(), 2)
        self.assertEqual(services.open_warnings(self.s).count(), 0)
        services.decide_dc_review(review, DCReview.Decision.CANCEL, date(2028, 3, 1), "no progress")
        self.s.refresh_from_db()
        self.assertEqual(self.s.status, Status.CANCELLED)

    def test_examiner_escalation(self):
        thesis = Thesis.objects.create(scholar=self.s, title="T", submitted_on=date(2029, 9, 1),
                                       plagiarism_percent=1, fees_paid=True)
        t.approve_all(services.submit_examiner_panel(thesis, nominees()))
        thesis.refresh_from_db()
        noms = list(thesis.nominations.all())
        services.select_examiners(thesis, noms[:3], date(2029, 10, 1))
        remind, replace = services.examiner_escalations(date(2029, 11, 29))
        self.assertEqual(remind.count(), 0)
        remind, replace = services.examiner_escalations(date(2029, 11, 30))
        self.assertEqual(remind.count(), 3)
        remind.update(reminded_on=date(2029, 11, 30))
        _, replace = services.examiner_escalations(date(2029, 12, 30))
        self.assertEqual(replace.count(), 3)
        new = services.replace_examiner(replace.first(), noms[3], date(2030, 1, 2))
        self.assertTrue(new.examiner.selected)
        with self.assertRaises(ValidationError):
            services.replace_examiner(new, noms[0], date(2030, 1, 2))  # already selected
