from datetime import date, timedelta
from io import StringIO
from itertools import count

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.utils import timezone

from core.models import Committee, CommitteeMembership, Department, Designation, Faculty, School
from identity import audit, services
from identity.authz import authorize, effective_capabilities, require, visible_scholars, workspaces_of
from identity.capabilities import Capability as C
from identity.capabilities import ScopeType as S
from identity.models import AuditEvent, CapabilityAssignment, Person
from scholars.models import Scholar
from supervision.models import SupervisorAssignment, TACMembership

_n = count(1)
TODAY = timezone.localdate()


class IdentityFixture(TestCase):
    """Two schools, three departments, scholars in each, and helpers to build
    persons. Fixture persons/grants are created directly; behaviour under test
    goes through identity.services / identity.authz."""

    def setUp(self):
        self.s1 = School.objects.create(code="S1", name="School One")
        self.s2 = School.objects.create(code="S2", name="School Two")
        self.d1 = Department.objects.create(school=self.s1, code="D1", name="Dept One")
        self.d2 = Department.objects.create(school=self.s1, code="D2", name="Dept Two")
        self.d3 = Department.objects.create(school=self.s2, code="D3", name="Dept Three")
        self.sch_d1 = self.scholar(self.d1)
        self.sch_d1b = self.scholar(self.d1)
        self.sch_d2 = self.scholar(self.d2)
        self.sch_d3 = self.scholar(self.d3)
        self.sysadmin = services.bootstrap_system_admin(full_name="Sys Admin", email="sys@dypiu.ac.in",
                                                        basis="bootstrap order 1").person

    # --- builders ---
    def scholar(self, dept, **kw):
        i = next(_n)
        return Scholar.objects.create(prn=f"P{i:04d}", name=f"Scholar {i}", gender="M", category="FT",
                                      entry_qualification="MTECH", department=dept, admission_date=date(2026, 7, 1),
                                      registration_date=date(2026, 8, 1), **kw)

    def person(self, *, faculty_dept=None, scholar=None, login=True, external=False):
        i = next(_n)
        user = get_user_model().objects.create_user(f"u{i}", password="x") if login else None
        faculty = None
        if faculty_dept is not None or external:
            faculty = Faculty.objects.create(name=f"Prof {i}", designation=Designation.PROFESSOR,
                                             department=faculty_dept, is_external=external)
        return Person.objects.create(full_name=f"Person {i}", email=f"p{i}@dypiu.ac.in", user=user,
                                     faculty_profile=faculty, scholar_profile=scholar)

    def grant(self, person, cap, scope=S.INSTITUTION, **target):
        return CapabilityAssignment.objects.create(person=person, capability=cap, scope_type=scope,
                                                   valid_from=TODAY, basis="fixture", granted_by=self.sysadmin,
                                                   **target)

    def supervise(self, faculty_person, scholar, approved=True, ended=False, kind="SUPERVISOR"):
        return SupervisorAssignment.objects.create(
            scholar=scholar, faculty=faculty_person.faculty_profile, kind=kind, start_date=TODAY,
            approved_on=TODAY if approved else None, end_date=TODAY if ended else None)

    def can(self, person, action, resource=None):
        return authorize(person, action, resource).allowed


class UnauthorizedAccessTests(IdentityFixture):
    def test_anonymous_and_unlinked_logins(self):
        self.assertEqual(self.client.get("/identity/me/").status_code, 401)
        bare = get_user_model().objects.create_user("bare", password="x")
        self.client.force_login(bare)
        self.assertEqual(self.client.get("/identity/me/").status_code, 403)

    def test_no_person_is_denied_and_audited(self):
        self.assertFalse(self.can(None, "scholar.view", self.sch_d1))
        before = AuditEvent.objects.count()
        with self.assertRaises(PermissionDenied):
            require(None, "scholar.view", self.sch_d1)
        ev = AuditEvent.objects.latest("id")
        self.assertEqual((AuditEvent.objects.count(), ev.allowed, ev.action), (before + 1, False, "scholar.view"))

    def test_inactive_person_is_denied(self):
        p = self.person()
        self.grant(p, C.PHD_CELL_OPERATOR)
        self.assertTrue(self.can(p, "scholar.view", self.sch_d1))
        p.is_active = False
        p.save()
        self.assertFalse(self.can(p, "scholar.view", self.sch_d1))
        self.client.force_login(p.user)
        self.assertEqual(self.client.get("/identity/me/").status_code, 403)

    def test_unknown_action_denied(self):
        self.assertFalse(self.can(self.sysadmin, "anything.at.all", None))

    def test_superuser_and_staff_flags_grant_nothing(self):
        su = get_user_model().objects.create_superuser("root", password="x")
        p = Person.objects.create(full_name="Root", email="root@dypiu.ac.in", user=su)
        for action in ("scholar.view", "scholar.edit_record", "identity.person.manage", "audit.view"):
            self.assertFalse(self.can(p, action, self.sch_d1), action)

    def test_legacy_django_groups_grant_nothing(self):
        p = self.person()
        for role in ("dc", "vc", "dean_rd", "rd_office", "supervisor"):
            Group.objects.get_or_create(name=role)[0].user_set.add(p.user)
        self.assertFalse(self.can(p, "scholar.view", self.sch_d1))
        self.assertEqual(visible_scholars(p).count(), 0)


class CrossScholarTests(IdentityFixture):
    def test_scholar_sees_only_self(self):
        me = self.person(scholar=self.sch_d1)
        self.assertTrue(self.can(me, "scholar.view", self.sch_d1))
        self.assertFalse(self.can(me, "scholar.view", self.sch_d1b))  # same department
        self.assertFalse(self.can(me, "scholar.view", self.sch_d3))
        self.assertEqual(list(visible_scholars(me)), [self.sch_d1])

    def test_scholar_cannot_act_on_own_record(self):
        me = self.person(scholar=self.sch_d1)
        for action in ("scholar.edit_record", "research.supervisee.act", "supervision.assign"):
            self.assertFalse(self.can(me, action, self.sch_d1), action)

    def test_exam_evaluator_limited_to_assigned_scholar(self):
        ext = self.person(external=True)
        self.grant(ext, C.EXAM_EVALUATOR, S.SCHOLAR, scholar=self.sch_d3)
        self.assertTrue(self.can(ext, "scholar.view", self.sch_d3))
        self.assertFalse(self.can(ext, "scholar.view", self.sch_d1))
        self.assertEqual(list(visible_scholars(ext)), [self.sch_d3])


class CrossScopeTests(IdentityFixture):
    def test_department_admin(self):
        p = self.person()
        self.grant(p, C.DEPARTMENT_ADMIN, S.DEPARTMENT, department=self.d1)
        self.assertTrue(self.can(p, "scholar.view", self.sch_d1))
        self.assertFalse(self.can(p, "scholar.view", self.sch_d2))  # same school, other department
        self.assertFalse(self.can(p, "scholar.view", self.sch_d3))

    def test_school_admin(self):
        p = self.person()
        self.grant(p, C.SCHOOL_ADMIN, S.SCHOOL, school=self.s1)
        self.assertTrue(self.can(p, "scholar.view", self.sch_d2))
        self.assertFalse(self.can(p, "scholar.view", self.sch_d3))

    def test_committee_membership_scopes_dc_member(self):
        p = self.person(faculty_dept=self.d1)
        dc = Committee.objects.create(type="DC", name="DC S1", school=self.s1)
        CommitteeMembership.objects.create(committee=dc, faculty=p.faculty_profile)
        self.assertTrue(self.can(p, "scholar.view", self.sch_d2))
        self.assertFalse(self.can(p, "scholar.view", self.sch_d3))

    def test_institution_committee_covers_all_and_tenure_expiry_removes_it(self):
        p = self.person(faculty_dept=self.d1)
        dc = Committee.objects.create(type="DC", name="University DC", school=None)
        CommitteeMembership.objects.create(committee=dc, faculty=p.faculty_profile)
        self.assertTrue(self.can(p, "scholar.view", self.sch_d3))
        dc.tenure_end = TODAY - timedelta(days=1)
        dc.save()
        self.assertFalse(self.can(p, "scholar.view", self.sch_d3))

    def test_scoped_admin_cannot_edit_records(self):
        p = self.person()
        self.grant(p, C.SCHOOL_ADMIN, S.SCHOOL, school=self.s1)
        self.assertFalse(self.can(p, "scholar.edit_record", self.sch_d1))

    def test_queryset_scoping_matches_authorize_for_every_persona(self):
        personas = []
        a = self.person(); self.grant(a, C.DEPARTMENT_ADMIN, S.DEPARTMENT, department=self.d2); personas.append(a)
        b = self.person(); self.grant(b, C.SCHOOL_ADMIN, S.SCHOOL, school=self.s2); personas.append(b)
        c = self.person(); self.grant(c, C.DEAN_RND); personas.append(c)
        d = self.person(faculty_dept=self.d1); self.grant(d, C.SUPERVISOR, S.DEPARTMENT, department=self.d1)
        self.supervise(d, self.sch_d3); personas.append(d)
        e = self.person(scholar=self.sch_d1b); personas.append(e)
        personas.append(self.sysadmin)
        for p in personas:
            visible = set(visible_scholars(p))
            for s in Scholar.objects.all():
                self.assertEqual(s in visible, self.can(p, "scholar.view", s), (p, s))


class PrivilegeEscalationTests(IdentityFixture):
    def test_cannot_grant_to_self_even_as_system_admin(self):
        with self.assertRaises(PermissionDenied):
            services.grant_capability(self.sysadmin, self.sysadmin, C.DEAN_RND, S.INSTITUTION, basis="x")
        ev = AuditEvent.objects.latest("id")
        self.assertEqual((ev.action, ev.allowed), ("identity.capability.grant", False))

    def test_grant_matrix_enforced(self):
        acad = self.person(); self.grant(acad, C.ACADEMIC_ADMIN)
        dept = self.person(); self.grant(dept, C.DEPARTMENT_ADMIN, S.DEPARTMENT, department=self.d1)
        target = self.person()
        for cap in (C.SYSTEM_ADMIN, C.PHD_CELL_OPERATOR, C.DEAN_RND, C.VC_OPERATOR):
            with self.assertRaises(PermissionDenied, msg=cap):
                services.grant_capability(acad, target, cap, S.INSTITUTION, basis="x")
        with self.assertRaises(PermissionDenied):
            services.grant_capability(dept, target, C.DEPARTMENT_ADMIN, S.DEPARTMENT, department=self.d1, basis="x")
        g = services.grant_capability(acad, target, C.SCHOOL_ADMIN, S.SCHOOL, school=self.s1, basis="order 7")
        self.assertEqual(g.granted_by, acad)

    def test_scholar_and_faculty_cannot_grant(self):
        sch = self.person(scholar=self.sch_d1)
        fac = self.person(faculty_dept=self.d1)
        for actor in (sch, fac):
            with self.assertRaises(PermissionDenied):
                services.grant_capability(actor, self.person(), C.DEPARTMENT_ADMIN, S.DEPARTMENT,
                                          department=self.d1, basis="x")

    def test_derived_capabilities_cannot_be_granted(self):
        target = self.person()
        for cap in (C.SCHOLAR, C.FACULTY, C.DC_MEMBER, C.SDRC_MEMBER):
            with self.assertRaises(PermissionDenied, msg=cap):
                services.grant_capability(self.sysadmin, target, cap, S.INSTITUTION, basis="x")
        with self.assertRaises(IntegrityError), transaction.atomic():  # also blocked in the database
            CapabilityAssignment.objects.create(person=target, capability=C.DC_MEMBER, scope_type=S.INSTITUTION,
                                                valid_from=TODAY, basis="x")

    def test_scope_type_must_fit_capability(self):
        with self.assertRaises(ValidationError):
            services.grant_capability(self.sysadmin, self.person(), C.DEPARTMENT_ADMIN, S.INSTITUTION, basis="x")

    def test_duplicate_active_grant_rejected(self):
        p = self.person()
        services.grant_capability(self.sysadmin, p, C.DEAN_RND, S.INSTITUTION, basis="x")
        with self.assertRaises(ValidationError):
            services.grant_capability(self.sysadmin, p, C.DEAN_RND, S.INSTITUTION, basis="x")

    def test_revoked_expired_and_future_grants_have_no_effect(self):
        p = self.person()
        g = services.grant_capability(self.sysadmin, p, C.PHD_CELL_OPERATOR, S.INSTITUTION, basis="x")
        self.assertTrue(self.can(p, "scholar.view", self.sch_d1))
        services.revoke_capability(self.sysadmin, g, reason="left post")
        self.assertFalse(self.can(p, "scholar.view", self.sch_d1))
        q = self.person()
        CapabilityAssignment.objects.create(person=q, capability=C.PHD_CELL_OPERATOR, scope_type=S.INSTITUTION,
                                            valid_from=TODAY - timedelta(days=30),
                                            valid_to=TODAY - timedelta(days=1), basis="x")
        r = self.person()
        CapabilityAssignment.objects.create(person=r, capability=C.PHD_CELL_OPERATOR, scope_type=S.INSTITUTION,
                                            valid_from=TODAY + timedelta(days=1), basis="x")
        self.assertFalse(self.can(q, "scholar.view", self.sch_d1))
        self.assertFalse(self.can(r, "scholar.view", self.sch_d1))

    def test_last_system_admin_protected(self):
        g = self.sysadmin.capability_assignments.get(capability=C.SYSTEM_ADMIN)
        with self.assertRaises(PermissionDenied):
            services.revoke_capability(self.sysadmin, g, reason="x")
        acad = self.person(); self.grant(acad, C.ACADEMIC_ADMIN)
        with self.assertRaises(PermissionDenied):
            services.deactivate_person(acad, self.sysadmin, reason="x")
        with self.assertRaises(PermissionDenied):
            services.deactivate_person(self.sysadmin, self.sysadmin, reason="x")

    def test_academic_admin_cannot_take_over_system_admin_identity(self):
        acad = self.person(); self.grant(acad, C.ACADEMIC_ADMIN)
        bare = Person.objects.create(full_name="Sys Two", email="sys2@dypiu.ac.in")
        self.grant(bare, C.SYSTEM_ADMIN)
        own_login = get_user_model().objects.create_user("attacker", password="x")
        with self.assertRaises(PermissionDenied):
            services.link_user(acad, bare, own_login)

    def test_bootstrap_only_once(self):
        with self.assertRaises(PermissionDenied):
            services.bootstrap_system_admin(full_name="X", email="x@dypiu.ac.in", basis="again")
        ev = AuditEvent.objects.latest("id")
        self.assertEqual((ev.action, ev.allowed), ("identity.bootstrap", False))
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            call_command("bootstrap_system_admin", email="y@dypiu.ac.in", name="Y", basis="again", verbosity=0)

    def test_admin_site_cannot_create_or_edit_grants(self):
        su = get_user_model().objects.create_superuser("root2", password="x")
        self.client.force_login(su)
        self.assertEqual(self.client.get("/admin/identity/capabilityassignment/add/").status_code, 403)
        g = CapabilityAssignment.objects.first()
        resp = self.client.post(f"/admin/identity/capabilityassignment/{g.pk}/change/", {"capability": "VC_OPERATOR"})
        self.assertIn(resp.status_code, (403, 200))
        g.refresh_from_db()
        self.assertEqual(g.capability, C.SYSTEM_ADMIN)
        self.assertEqual(self.client.get("/admin/identity/person/add/").status_code, 403)

    def test_audit_trail_is_append_only_and_tamper_evident(self):
        services.grant_capability(self.sysadmin, self.person(), C.DEAN_RND, S.INSTITUTION, basis="x")
        ev = AuditEvent.objects.latest("id")
        with self.assertRaises(PermissionError):
            ev.save()
        with self.assertRaises(PermissionError):
            ev.delete()
        with self.assertRaises(PermissionError):
            AuditEvent.objects.update(allowed=False)
        with self.assertRaises(PermissionError):
            AuditEvent.objects.all().delete()
        self.assertEqual(audit.verify_chain(), (True, None))
        with connection.cursor() as cur:  # bypass the ORM, as an attacker with DB access would
            cur.execute(f"UPDATE {AuditEvent._meta.db_table} SET allowed = %s WHERE id = %s", [False, ev.id])
        self.assertEqual(audit.verify_chain(), (False, ev.id))


class SupervisorPermissionTests(IdentityFixture):
    def setUp(self):
        super().setUp()
        self.sup = self.person(faculty_dept=self.d1)
        self.grant(self.sup, C.SUPERVISOR, S.DEPARTMENT, department=self.d1)

    def test_supervisor_acts_only_for_own_approved_supervisees(self):
        self.supervise(self.sup, self.sch_d1)
        self.assertTrue(self.can(self.sup, "scholar.view", self.sch_d1))
        self.assertTrue(self.can(self.sup, "research.supervisee.act", self.sch_d1))
        self.assertFalse(self.can(self.sup, "scholar.view", self.sch_d1b))       # cross-scholar, same dept
        self.assertFalse(self.can(self.sup, "research.supervisee.act", self.sch_d1b))
        self.assertEqual(list(visible_scholars(self.sup)), [self.sch_d1])

    def test_pending_or_ended_assignment_gives_nothing(self):
        self.supervise(self.sup, self.sch_d1, approved=False)
        self.supervise(self.sup, self.sch_d1b, ended=True)
        self.assertFalse(self.can(self.sup, "research.supervisee.act", self.sch_d1))
        self.assertFalse(self.can(self.sup, "research.supervisee.act", self.sch_d1b))

    def test_relationship_without_capability_is_not_enough(self):
        other = self.person(faculty_dept=self.d1)  # faculty, no SUPERVISOR grant
        self.supervise(other, self.sch_d1b)
        self.assertFalse(self.can(other, "research.supervisee.act", self.sch_d1b))
        self.assertFalse(self.can(other, "scholar.view", self.sch_d1b))

    def test_relationship_not_department_decides_interdisciplinary_supervision(self):
        self.supervise(self.sup, self.sch_d3)  # scholar in another school
        self.assertTrue(self.can(self.sup, "research.supervisee.act", self.sch_d3))

    def test_co_supervisor_with_capability(self):
        co = self.person(external=True)
        self.grant(co, C.SUPERVISOR, S.INSTITUTION)
        self.supervise(co, self.sch_d2, kind="CO_SUPERVISOR")
        self.assertTrue(self.can(co, "scholar.view", self.sch_d2))

    def test_supervisor_requires_faculty_profile(self):
        not_faculty = self.person(scholar=self.sch_d2)
        with self.assertRaises(ValidationError):
            services.grant_capability(self.sysadmin, not_faculty, C.SUPERVISOR, S.DEPARTMENT,
                                      department=self.d2, basis="x")

    def test_tac_member_can_view_but_not_act_as_supervisor(self):
        tac = self.person(faculty_dept=self.d2)
        TACMembership.objects.create(scholar=self.sch_d1, faculty=tac.faculty_profile, kind="MEMBER",
                                     start_date=TODAY, approved_on=TODAY)
        self.assertTrue(self.can(tac, "scholar.view", self.sch_d1))
        self.assertFalse(self.can(tac, "research.supervisee.act", self.sch_d1))

    def test_supervisor_is_a_workspace_of_the_same_faculty_person(self):
        keys = [w["key"] for w in workspaces_of(self.sup)]
        self.assertEqual(keys, ["faculty", "supervisor"])
        plain = self.person(faculty_dept=self.d1)
        self.assertEqual([w["key"] for w in workspaces_of(plain)], ["faculty"])
        self.client.force_login(self.sup.user)
        body = self.client.get("/identity/me/").json()
        self.assertEqual(body["person"]["id"], self.sup.pk)
        self.assertIn({"key": "supervisor", "path": "/faculty/supervisor", "capability": "SUPERVISOR"},
                      body["workspaces"])


class AdministrativePermissionTests(IdentityFixture):
    def test_system_admin_manages_identity_but_not_academic_records(self):
        self.assertTrue(self.can(self.sysadmin, "identity.person.manage"))
        self.assertTrue(self.can(self.sysadmin, "audit.view"))
        self.assertFalse(self.can(self.sysadmin, "scholar.view", self.sch_d1))
        self.assertFalse(self.can(self.sysadmin, "scholar.edit_record", self.sch_d1))
        self.assertEqual(visible_scholars(self.sysadmin).count(), 0)

    def test_oversight_capabilities(self):
        phd = self.person(); self.grant(phd, C.PHD_CELL_OPERATOR)
        dean = self.person(); self.grant(dean, C.DEAN_RND)
        acad = self.person(); self.grant(acad, C.ACADEMIC_ADMIN)
        for p in (phd, acad):
            self.assertTrue(self.can(p, "scholar.edit_record", self.sch_d3))
        self.assertTrue(self.can(dean, "scholar.view", self.sch_d3))
        self.assertFalse(self.can(dean, "scholar.edit_record", self.sch_d3))
        self.assertFalse(self.can(phd, "audit.view"))

    def test_privileged_actions_audited_when_allowed(self):
        phd = self.person(); self.grant(phd, C.PHD_CELL_OPERATOR)
        require(phd, "scholar.edit_record", self.sch_d1)
        ev = AuditEvent.objects.latest("id")
        self.assertEqual((ev.action, ev.allowed, ev.capability_used, ev.actor_id),
                         ("scholar.edit_record", True, C.PHD_CELL_OPERATOR, phd.pk))
        n = AuditEvent.objects.count()
        require(phd, "scholar.view", self.sch_d1)      # non-privileged, allowed: not audited
        self.assertEqual(AuditEvent.objects.count(), n)

    def test_person_management_requires_capability(self):
        dept = self.person(); self.grant(dept, C.DEPARTMENT_ADMIN, S.DEPARTMENT, department=self.d1)
        with self.assertRaises(PermissionDenied):
            services.provision_person(dept, full_name="New", email="new@dypiu.ac.in")
        acad = self.person(); self.grant(acad, C.ACADEMIC_ADMIN)
        p = services.provision_person(acad, full_name="New", email="New@DYPIU.ac.in")
        self.assertEqual(p.email, "new@dypiu.ac.in")
        with self.assertRaises(ValidationError):  # one person per email (case-insensitive)
            services.provision_person(acad, full_name="Dup", email="NEW@dypiu.ac.in")

    def test_one_person_can_hold_faculty_and_scholar_profiles(self):
        acad = self.person(); self.grant(acad, C.ACADEMIC_ADMIN)
        p = services.provision_person(acad, full_name="Internal PT", email="ipt@dypiu.ac.in")
        fac = Faculty.objects.create(name="Internal PT", designation=Designation.ASSISTANT_PROFESSOR,
                                     department=self.d2, email="ipt@dypiu.ac.in")
        services.link_faculty_profile(acad, p, fac)
        services.link_scholar_profile(acad, p, self.sch_d2)
        self.assertEqual({w["key"] for w in workspaces_of(p)}, {"scholar", "faculty"})
        self.assertEqual(Person.objects.filter(email="ipt@dypiu.ac.in").count(), 1)

    def test_profile_email_mismatch_rejected(self):
        acad = self.person(); self.grant(acad, C.ACADEMIC_ADMIN)
        p = services.provision_person(acad, full_name="A", email="a@dypiu.ac.in")
        fac = Faculty.objects.create(name="B", designation=Designation.PROFESSOR, department=self.d1,
                                     email="b@dypiu.ac.in")
        with self.assertRaises(ValidationError):
            services.link_faculty_profile(acad, p, fac)

    def test_me_endpoint_lists_server_computed_capabilities(self):
        acad = self.person(); self.grant(acad, C.ACADEMIC_ADMIN)
        self.client.force_login(acad.user)
        body = self.client.get("/identity/me/").json()
        self.assertEqual([c["capability"] for c in body["capabilities"]], ["ACADEMIC_ADMIN"])
        self.assertEqual([w["key"] for w in body["workspaces"]], ["academic_admin"])
        self.assertEqual(len(effective_capabilities(acad)), 1)


GRANT_ACTION = "identity.capability.grant"


class ProvisionIdentityCommandTests(IdentityFixture):
    """manage.py provision_identity: a new login + Person + one institution-scoped
    grant, only through an active SYSTEM_ADMIN and the identity services."""

    PW = "a-long-dev-password"

    def run_cmd(self, *, actor="sys@dypiu.ac.in", username="acad.test", email="acad.test@dypiu.ac.in",
                capability="ACADEMIC_ADMIN", basis="Local dev provisioning", password=PW):
        from unittest import mock
        with mock.patch("identity.management.commands.provision_identity.getpass.getpass", return_value=password):
            call_command("provision_identity", actor_email=actor, username=username, email=email,
                         name="Academic Test", capability=capability, basis=basis, stdout=StringIO())

    def assert_nothing_created(self, users_before, persons_before, grants_before):
        self.assertEqual(get_user_model().objects.count(), users_before)
        self.assertEqual(Person.objects.count(), persons_before)
        self.assertEqual(CapabilityAssignment.objects.count(), grants_before)

    def counts(self):
        return get_user_model().objects.count(), Person.objects.count(), CapabilityAssignment.objects.count()

    def test_provisions_normal_login_person_and_academic_admin(self):
        self.run_cmd()
        user = get_user_model().objects.get(username="acad.test")
        self.assertFalse(user.is_staff or user.is_superuser)
        self.assertTrue(user.check_password(self.PW))
        person = Person.objects.get(email="acad.test@dypiu.ac.in")
        self.assertEqual((person.user_id, person.is_active), (user.pk, True))
        g = CapabilityAssignment.objects.get(person=person)
        self.assertEqual((g.capability, g.scope_type, g.granted_by_id, g.basis),
                         (C.ACADEMIC_ADMIN, S.INSTITUTION, self.sysadmin.pk, "Local dev provisioning"))
        self.assertIn("academic_admin", [w["key"] for w in workspaces_of(person)])
        # The login works through the real API sign-in.
        r = self.client.post("/api/auth/login/", {"username": "acad.test", "password": self.PW},
                             content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.assertIn("academic_admin", [w["key"] for w in r.json()["workspaces"]])

    def test_every_step_is_audited_with_the_sysadmin_as_actor(self):
        last = AuditEvent.objects.latest("id").id
        self.run_cmd()
        events = AuditEvent.objects.filter(id__gt=last)
        actions = [e.action for e in events if e.allowed]
        for action in ("identity.person.manage", "identity.person.create", "identity.person.link_user", GRANT_ACTION):
            self.assertIn(action, actions)
        grant_ev = events.get(action=GRANT_ACTION, allowed=True)
        self.assertEqual((grant_ev.actor_id, grant_ev.capability_used), (self.sysadmin.pk, C.SYSTEM_ADMIN))
        self.assertTrue(audit.verify_chain()[0])

    def test_actor_without_system_admin_is_refused_and_audited(self):
        acad = self.person(); self.grant(acad, C.ACADEMIC_ADMIN)
        n = self.counts()
        with self.assertRaises(CommandError):
            self.run_cmd(actor=acad.email)
        self.assert_nothing_created(*n)
        ev = AuditEvent.objects.latest("id")
        self.assertEqual((ev.action, ev.allowed, ev.actor_id), (GRANT_ACTION, False, acad.pk))

    def test_unknown_or_inactive_actor_is_refused(self):
        n = self.counts()
        with self.assertRaises(CommandError):
            self.run_cmd(actor="nobody@dypiu.ac.in")
        other = self.person(); self.grant(other, C.SYSTEM_ADMIN)
        other.is_active = False; other.save()
        with self.assertRaises(CommandError):
            self.run_cmd(actor=other.email)
        self.assertEqual(self.counts()[2], n[2] + 1)  # only the fixture grant above

    def test_revoked_system_admin_is_refused(self):
        other = self.person(); g = self.grant(other, C.SYSTEM_ADMIN)
        g.revoked_at = timezone.now(); g.save()
        n = self.counts()
        with self.assertRaises(CommandError):
            self.run_cmd(actor=other.email)
        self.assert_nothing_created(*n)

    def test_self_grant_is_refused(self):
        n = self.counts()
        with self.assertRaises(CommandError):
            self.run_cmd(email="sys@dypiu.ac.in")
        self.assert_nothing_created(*n)
        ev = AuditEvent.objects.latest("id")
        self.assertEqual((ev.action, ev.allowed), (GRANT_ACTION, False))
        self.assertIn("separation of duties", ev.reasons[0])

    def test_invalid_capabilities_are_refused(self):
        n = self.counts()
        for cap in ("NOT_A_CAPABILITY", "SCHOLAR", "FACULTY", "SDRC_MEMBER", "SYSTEM_ADMIN", "DEPARTMENT_ADMIN",
                    "EXAM_EVALUATOR"):
            with self.subTest(cap=cap), self.assertRaises(CommandError):
                self.run_cmd(capability=cap)
        self.assert_nothing_created(*n)

    def test_basis_is_required(self):
        n = self.counts()
        with self.assertRaises(CommandError):
            self.run_cmd(basis="   ")
        self.assert_nothing_created(*n)

    def test_duplicate_login_person_or_active_grant_is_refused(self):
        self.run_cmd()
        n = self.counts()
        with self.assertRaises(CommandError):  # same login
            self.run_cmd(email="other@dypiu.ac.in")
        with self.assertRaises(CommandError):  # same person email
            self.run_cmd(username="another")
        self.assert_nothing_created(*n)
        # The database still refuses a second active grant of the same capability.
        person = Person.objects.get(email="acad.test@dypiu.ac.in")
        with self.assertRaises(IntegrityError), transaction.atomic():
            CapabilityAssignment.objects.create(person=person, capability=C.ACADEMIC_ADMIN,
                                                scope_type=S.INSTITUTION, valid_from=TODAY, basis="dup")

    def test_weak_or_mismatched_password_creates_nothing(self):
        n = self.counts()
        with self.assertRaises(CommandError):
            self.run_cmd(password="short")
        from unittest import mock
        with mock.patch("identity.management.commands.provision_identity.getpass.getpass",
                        side_effect=[self.PW, self.PW + "x"]), self.assertRaises(CommandError):
            call_command("provision_identity", actor_email="sys@dypiu.ac.in", username="u", email="u@dypiu.ac.in",
                         name="U", capability="ACADEMIC_ADMIN", basis="b", verbosity=0)
        self.assert_nothing_created(*n)
