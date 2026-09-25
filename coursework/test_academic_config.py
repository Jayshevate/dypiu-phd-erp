from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError

from coursework.academic import config
from coursework.academic.testing import TODAY, AcademicFixture
from coursework.models import AcademicRuleParameter, ParameterChangeRequest
from identity.models import AuditEvent


class ConfigurationTests(AcademicFixture):
    def test_every_parameter_classified_and_sourced(self):
        rows = AcademicRuleParameter.objects.filter(version=1)
        self.assertGreaterEqual(rows.count(), 44)
        for r in rows:
            self.assertIn(r.status, ("CONFIRMED", "CONFIGURABLE", "AMBIGUOUS", "UNRESOLVED"), r.key)
            self.assertTrue(r.source, r.key)
            if r.status == "UNRESOLVED":
                self.assertIsNone(r.value, r.key)
        self.assertEqual(config.get("grading.scheme").status, "AMBIGUOUS")
        self.assertTrue(config.get("result.absence_policy").unresolved)
        self.assertEqual(config.get("third_attempt.review_chain").value, ["DEAN_RND", "VC_OPERATOR"])

    def test_proposal_is_not_authoritative_until_approved(self):
        change = config.propose_change(self.acad, "attendance.min_percent", value={"FT": 75, "PT": 60},
                                       status="CONFIGURABLE", source="DC resolution 7", reason="PT rule decided")
        self.assertEqual(change.state, "PENDING")
        self.assertIsNone(config.get("attendance.min_percent").value["PT"])      # nothing changed yet
        config.decide_change(self.dean, change, approve=True, remarks="ok")
        p = config.get("attendance.min_percent")
        self.assertEqual((p.version, p.value["PT"]), (2, 60))
        row = AcademicRuleParameter.objects.get(key="attendance.min_percent", version=2)
        self.assertEqual((row.created_by, row.approved_by, row.change_request), (self.acad, self.dean, change))
        ev = AuditEvent.objects.filter(action="academic.config.approve").latest("id")
        self.assertEqual((ev.before["version"], ev.after["version"]), (1, 2))

    def test_effective_date_respected(self):
        change = config.propose_change(self.acad, "exam.max_regular_attempts", value=2, status="CONFIRMED",
                                       source="x", reason="restated", effective_from=TODAY + timedelta(days=30))
        config.decide_change(self.dean, change, approve=True)
        self.assertEqual(config.get("exam.max_regular_attempts").version, 1)
        self.assertEqual(config.get("exam.max_regular_attempts", TODAY + timedelta(days=31)).version, 2)

    def test_rejection_creates_no_version(self):
        change = config.propose_change(self.acad, "coursework.min_gpa", value="5.5", status="CONFIGURABLE",
                                       source="x", reason="x")
        change = config.decide_change(self.dean, change, approve=False, remarks="no")
        self.assertEqual(config.get("coursework.min_gpa").value, "6.00")
        self.assertEqual(change.state, "REJECTED")

    def test_maker_cannot_approve_and_roles_enforced(self):
        both = self.staff("ACADEMIC_ADMIN")
        from identity.models import CapabilityAssignment
        CapabilityAssignment.objects.create(person=both, capability="DEAN_RND", scope_type="INSTITUTION",
                                            valid_from=TODAY, basis="x", granted_by=self.sysadmin)
        change = config.propose_change(both, "coursework.min_gpa", value="6.00", status="CONFIRMED", source="x",
                                       reason="x")
        with self.assertRaises(PermissionDenied):
            config.decide_change(both, change, approve=True)
        for actor in (self.coe, self.cisr, self.sysadmin, self.p_sch1, self.dean):
            with self.assertRaises(PermissionDenied):
                config.propose_change(actor, "exam.max_regular_attempts", value=5, status="CONFIGURABLE",
                                      source="x", reason="x")
        for actor in (self.acad, self.coe, self.vc, self.sysadmin):
            with self.assertRaises(PermissionDenied):
                config.decide_change(actor, change, approve=True)

    def test_one_pending_change_per_key(self):
        config.propose_change(self.acad, "coursework.min_gpa", value="6.00", status="CONFIRMED", source="x", reason="x")
        with self.assertRaises(ValidationError):
            config.propose_change(self.acad, "coursework.min_gpa", value="6.00", status="CONFIRMED", source="x",
                                  reason="x")

    def test_versions_are_immutable(self):
        row = AcademicRuleParameter.objects.get(key="coursework.min_gpa", version=1)
        with self.assertRaises(PermissionError):
            row.save()
        with self.assertRaises(PermissionError):
            AcademicRuleParameter.objects.filter(key="coursework.min_gpa").update(value="5")
        with self.assertRaises(PermissionError):
            row.delete()

    def test_unresolved_value_rules_and_unknown_keys(self):
        with self.assertRaises(ValidationError):
            config.propose_change(self.acad, "elective.approval_authority", value=["DC_MEMBER"], status="UNRESOLVED",
                                  source="x", reason="x")
        with self.assertRaises(ValidationError):
            config.propose_change(self.acad, "elective.approval_authority", value=None, status="CONFIGURABLE",
                                  source="x", reason="x")
        with self.assertRaises(ValidationError):
            config.propose_change(self.acad, "made.up.key", value=1, status="CONFIGURABLE", source="x", reason="x")
        self.assertFalse(ParameterChangeRequest.objects.exists())
