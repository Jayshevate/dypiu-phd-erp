"""Institutional setup, record creation and identity provisioning (Step A2).

Authorities under test: `institution.structure.manage` and `faculty.record.manage`
(ACADEMIC_ADMIN, provisional RD-38), `academic.structure.manage` (existing),
`scholar.edit_record` (existing) and `identity.person.manage` (existing)."""
import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import Department, Faculty, School, University
from coursework.models import AcademicYear, Course, Semester
from coursework.test_academic_api import API, ApiFixture
from coursework.academic.testing import TODAY
from identity.authz import workspaces_of
from identity.capabilities import Capability as C
from identity.models import AuditEvent, CapabilityAssignment, Person
from scholars.models import Scholar

PW = "Activation-Pass-2026"


class SetupFixture(ApiFixture):
    def scholar_payload(self, **kw):
        data = {"prn": "PRN-A2-001", "name": "New Scholar", "email": "new.scholar@dypiu.ac.in", "gender": "F",
                "category": "FT", "entry_qualification": "MTECH", "department_id": self.d1.pk,
                "admission_date": str(TODAY)}
        data.update(kw)
        return data

    def faculty_payload(self, **kw):
        data = {"name": "Dr New Faculty", "email": "new.faculty@dypiu.ac.in", "designation": "PROFESSOR",
                "department_id": self.d1.pk}
        data.update(kw)
        return data

    def last_audit(self):
        return AuditEvent.objects.latest("id")


class StructureTests(SetupFixture):
    def test_academic_admin_builds_university_school_department(self):
        r = self.post(self.acad, "setup/universities/", {"code": "DYPIU", "name": "DYPIU"})
        self.assertEqual(r.status_code, 201, r.content)
        r = self.post(self.acad, "setup/schools/", {"code": "SOE", "name": "School of Engineering",
                                                    "university_id": r.json()["id"]})
        self.assertEqual(r.status_code, 201, r.content)
        school_id = r.json()["id"]
        r = self.post(self.acad, "setup/departments/", {"school_id": school_id, "code": "CSE", "name": "CSE"})
        self.assertEqual(r.status_code, 201, r.content)
        ev = self.last_audit()
        self.assertEqual((ev.action, ev.allowed, ev.actor_id, ev.capability_used),
                         ("institution.structure.manage.create", True, self.acad.pk, C.ACADEMIC_ADMIN))
        self.assertEqual(Department.objects.get(code="CSE").school_id, school_id)

    def test_other_capabilities_cannot_create_structure(self):
        for person in (self.phd, self.cisr, self.coe, self.dean, self.vc, self.dept_admin, self.coord, self.p_sch1,
                       self.sysadmin):
            for url, data in (("setup/universities/", {"code": "X", "name": "X"}),
                              ("setup/schools/", {"code": "X", "name": "X"}),
                              ("setup/departments/", {"school_id": self.s1.pk, "code": "X", "name": "X"})):
                if person is self.sysadmin and person.user_id is None:
                    person.user = get_user_model().objects.create_user("sys-login")
                    person.save(update_fields=["user"])
                self.assertEqual(self.post(person, url, data).status_code, 403, (person.full_name, url))
        self.assertFalse(School.objects.filter(code="X").exists())
        self.assertFalse(Department.objects.filter(code="X").exists())
        ev = self.last_audit()
        self.assertEqual((ev.allowed, ev.action), (False, "institution.structure.manage"))

    def test_expired_or_revoked_academic_admin_is_denied(self):
        grant = CapabilityAssignment.objects.get(person=self.acad, capability=C.ACADEMIC_ADMIN)
        grant.valid_to = TODAY - timedelta(days=1)
        grant.valid_from = TODAY - timedelta(days=10)
        grant.save()
        self.assertEqual(self.post(self.acad, "setup/schools/", {"code": "E1", "name": "E"}).status_code, 403)
        grant.valid_to = None
        grant.revoked_at = timezone.now()
        grant.save()
        self.assertEqual(self.post(self.acad, "setup/schools/", {"code": "E2", "name": "E"}).status_code, 403)
        self.assertFalse(School.objects.filter(code__in=["E1", "E2"]).exists())

    def test_duplicate_codes_are_rejected(self):
        r = self.post(self.acad, "setup/schools/", {"code": "S1", "name": "Again"})
        self.assertEqual(r.status_code, 400)
        r = self.post(self.acad, "setup/departments/", {"school_id": self.s1.pk, "code": "D1", "name": "Again"})
        self.assertEqual(r.status_code, 400)

    def test_structure_lists_are_gated(self):
        self.assertEqual(self.get(self.acad, "setup/schools/").status_code, 200)
        self.assertTrue(self.get(self.acad, "setup/schools/").json()["can"]["create"])
        self.assertFalse(self.get(self.phd, "setup/schools/").json()["can"]["create"])
        for person in (self.p_sch1, self.coord, self.coe, self.vc):
            self.assertEqual(self.get(person, "setup/schools/").status_code, 403)


class CalendarAndCatalogueTests(SetupFixture):
    def test_years_semesters_and_courses(self):
        r = self.post(self.cisr, "setup/academic-years/", {"code": "2030-31", "start_date": "2030-07-01",
                                                           "end_date": "2031-06-30"})
        self.assertEqual(r.status_code, 201, r.content)
        year = r.json()["id"]
        r = self.post(self.acad, "setup/semesters/", {"academic_year_id": year, "term": "JUL_DEC",
                                                      "name": "Monsoon 2030", "start_date": "2030-07-15",
                                                      "end_date": "2030-12-15"})
        self.assertEqual(r.status_code, 201, r.content)
        r = self.post(self.acad, "setup/semesters/", {"academic_year_id": year, "term": "JAN_JUN", "name": "Out",
                                                      "start_date": "2031-06-01", "end_date": "2031-08-01"})
        self.assertEqual(r.status_code, 400)  # outside the academic year
        r = self.post(self.acad, "setup/courses/", {"code": "SIS9901", "title": "New elective", "credits": 3,
                                                    "category": "ELECTIVE", "department_id": self.d1.pk})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(Course.objects.filter(code="SIS9901").exists())

    def test_calendar_and_catalogue_writes_denied_to_others(self):
        for person in (self.phd, self.coe, self.dept_admin, self.p_sch1, self.coord):
            self.assertEqual(self.post(person, "setup/academic-years/", {"code": "Z", "start_date": "2030-07-01",
                                                                         "end_date": "2031-06-30"}).status_code, 403)
            self.assertEqual(self.post(person, "setup/semesters/", {
                "academic_year_id": self.year.pk, "term": "JAN_JUN", "name": "Z",
                "start_date": str(TODAY), "end_date": str(TODAY + timedelta(days=5))}).status_code, 403)
            self.assertEqual(self.post(person, "setup/courses/", {"code": "Z1", "title": "Z", "credits": 1,
                                                                  "category": "ELECTIVE"}).status_code, 403)
        self.assertFalse(AcademicYear.objects.filter(code="Z").exists())
        self.assertFalse(Semester.objects.filter(name="Z").exists())


class RecordTests(SetupFixture):
    def test_faculty_record_created_by_academic_admin_only(self):
        self.assertEqual(self.post(self.acad, "setup/faculty/", self.faculty_payload()).status_code, 201)
        for person in (self.phd, self.dept_admin, self.coe, self.coord, self.p_sch1):
            r = self.post(person, "setup/faculty/", self.faculty_payload(email=f"x{person.pk}@dypiu.ac.in"))
            self.assertEqual(r.status_code, 403)
        self.assertEqual(Faculty.objects.filter(email__startswith="x").count(), 0)

    def test_faculty_record_validation(self):
        self.post(self.acad, "setup/faculty/", self.faculty_payload())
        dup = self.post(self.acad, "setup/faculty/", self.faculty_payload(email="NEW.Faculty@dypiu.ac.in"))
        self.assertEqual(dup.status_code, 400)
        no_dept = self.post(self.acad, "setup/faculty/", self.faculty_payload(email="z@dypiu.ac.in",
                                                                             department_id=None))
        self.assertEqual(no_dept.status_code, 400)
        external = self.post(self.acad, "setup/faculty/", self.faculty_payload(
            email="ext@elsewhere.edu", department_id=None, is_external=True, affiliation="Other University",
            designation="OTHER"))
        self.assertEqual(external.status_code, 201, external.content)

    def test_scholar_record_by_phd_cell_and_academic_admin(self):
        self.assertEqual(self.post(self.phd, "setup/scholars/", self.scholar_payload()).status_code, 201)
        r = self.post(self.acad, "setup/scholars/", self.scholar_payload(prn="PRN-A2-002",
                                                                        email="second@dypiu.ac.in"))
        self.assertEqual(r.status_code, 201, r.content)
        for person in (self.dept_admin, self.coe, self.rnd, self.coord, self.p_sch1, self.dean):
            r = self.post(person, "setup/scholars/", self.scholar_payload(prn=f"N{person.pk}",
                                                                         email=f"n{person.pk}@dypiu.ac.in"))
            self.assertEqual(r.status_code, 403, person.full_name)
        self.assertFalse(Scholar.objects.filter(prn__startswith="N").exists())

    def test_scholar_record_validation(self):
        self.post(self.phd, "setup/scholars/", self.scholar_payload())
        for payload in (self.scholar_payload(email="other@dypiu.ac.in", prn="prn-a2-001"),  # PRN, any case
                        self.scholar_payload(prn="PRN-B"),                                 # e-mail reused
                        self.scholar_payload(prn="PRN-C", email="c@dypiu.ac.in", category="PHD"),
                        self.scholar_payload(prn="PRN-D", email="d@dypiu.ac.in", department_id=999999)):
            r = self.post(self.phd, "setup/scholars/", payload)
            self.assertIn(r.status_code, (400, 404), payload)
        self.assertEqual(Scholar.objects.filter(prn__icontains="PRN-").count(), 1)

    def test_scholar_list_is_gated(self):
        self.assertEqual(self.get(self.phd, "setup/scholars/").status_code, 200)
        body = self.get(self.acad, "setup/scholars/", q=self.sch1.prn).json()
        self.assertEqual([s["prn"] for s in body["results"]], [self.sch1.prn])
        self.assertEqual(body["results"][0]["identity"]["login"], "NO_LOGIN")
        for person in (self.coe, self.dept_admin, self.p_sch1, self.coord):
            self.assertEqual(self.get(person, "setup/scholars/").status_code, 403)
        self.assertEqual(self.get(self.phd, "setup/faculty/").status_code, 403)


class ProvisioningTests(SetupFixture):
    def new_faculty(self, **kw):
        r = self.post(self.acad, "setup/faculty/", self.faculty_payload(**kw))
        self.assertEqual(r.status_code, 201, r.content)
        return Faculty.objects.get(pk=r.json()["id"])

    def provision(self, person, record, kind="faculty", **extra):
        return self.post(person, "setup/people/provision/", {"kind": kind, "record_id": record.pk, **extra})

    def test_provisioned_faculty_gets_derived_workspace_and_pending_login(self):
        fac = self.new_faculty()
        grants_before = CapabilityAssignment.objects.count()
        # A browser-supplied capability is ignored: nothing is granted, FACULTY is derived.
        r = self.provision(self.acad, fac, capability="SYSTEM_ADMIN", role="ACADEMIC_ADMIN")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["login"], "PENDING_ACTIVATION")
        person = Person.objects.get(faculty_profile=fac)
        self.assertEqual(person.email, "new.faculty@dypiu.ac.in")
        self.assertFalse(person.user.has_usable_password())
        self.assertEqual(CapabilityAssignment.objects.count(), grants_before)
        self.assertEqual([w["key"] for w in workspaces_of(person)], ["faculty"])
        actions = list(AuditEvent.objects.filter(allowed=True, actor=self.acad).values_list("action", flat=True))
        for a in ("identity.person.create", "identity.person.link_faculty", "identity.person.link_user"):
            self.assertIn(a, actions)

    def test_provision_scholar_without_login(self):
        r = self.post(self.phd, "setup/scholars/", self.scholar_payload())
        scholar = Scholar.objects.get(pk=r.json()["id"])
        r = self.provision(self.acad, scholar, kind="scholar", create_login=False)
        self.assertEqual((r.status_code, r.json()["login"]), (201, "NO_LOGIN"))
        self.assertEqual([w["key"] for w in workspaces_of(Person.objects.get(scholar_profile=scholar))], ["scholar"])

    def test_only_person_managers_may_provision(self):
        fac = self.new_faculty()
        for person in (self.phd, self.coe, self.dept_admin, self.coord, self.p_sch1, self.dean):
            self.assertEqual(self.provision(person, fac).status_code, 403)
        self.assertFalse(Person.objects.filter(faculty_profile=fac).exists())
        self.assertEqual(get_user_model().objects.filter(username="new.faculty@dypiu.ac.in").count(), 0)

    def test_never_merges_or_duplicates_people(self):
        fac = self.new_faculty()
        self.assertEqual(self.provision(self.acad, fac).status_code, 201)
        again = self.provision(self.acad, fac)
        self.assertEqual(again.status_code, 400)  # already linked
        # A scholar record whose e-mail already belongs to a person is not merged into that person.
        scholar = Scholar.objects.create(prn="PX1", name="Clash", email="new.faculty@dypiu.ac.in", gender="M",
                                         category="FT", entry_qualification="MTECH", department=self.d1,
                                         admission_date=TODAY)
        clash = self.provision(self.acad, scholar, kind="scholar")
        self.assertEqual(clash.status_code, 400)
        self.assertIn("already exists", clash.json()["detail"])
        self.assertIsNone(Person.objects.get(email="new.faculty@dypiu.ac.in").scholar_profile_id)
        # A record without an e-mail cannot be provisioned.
        bare = Faculty.objects.create(name="No Mail", designation="PROFESSOR", department=self.d1)
        self.assertEqual(self.provision(self.acad, bare).status_code, 400)
        self.assertEqual(Person.objects.filter(full_name="No Mail").count(), 0)


class ActivationTests(SetupFixture):
    def pending_person(self):
        r = self.post(self.acad, "setup/faculty/", self.faculty_payload())
        fac = Faculty.objects.get(pk=r.json()["id"])
        self.post(self.acad, "setup/people/provision/", {"kind": "faculty", "record_id": fac.pk})
        return Person.objects.get(faculty_profile=fac)

    def link(self, actor, person):
        return self.post(actor, f"setup/people/{person.pk}/activation/")

    def activate(self, uid, token, password=PW, client=None):
        c = client or APIClient()
        return c.post("/api/auth/activate/", {"uid": uid, "token": token, "password": password}, format="json")

    def parts(self, response):
        path = response.json()["path"]
        query = dict(p.split("=", 1) for p in path.split("?", 1)[1].split("&"))
        return query["uid"], query["token"]

    def test_full_activation_then_sign_in(self):
        person = self.pending_person()
        r = self.link(self.acad, person)
        self.assertEqual(r.status_code, 200, r.content)
        uid, token = self.parts(r)
        self.assertNotIn(token, json.dumps(list(AuditEvent.objects.values("after", "reasons"))))
        self.assertEqual(self.activate(uid, token, password="short").status_code, 400)
        r = self.activate(uid, token)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["username"], "new.faculty@dypiu.ac.in")
        login = APIClient().post("/api/auth/login/", {"username": "new.faculty@dypiu.ac.in", "password": PW},
                                 format="json")
        self.assertEqual(login.status_code, 200)
        self.assertEqual([w["key"] for w in login.json()["workspaces"]], ["faculty"])
        ev = AuditEvent.objects.filter(action="identity.login.activate").latest("id")
        self.assertEqual((ev.allowed, ev.actor_id), (True, person.pk))

    def test_link_is_single_use_and_cannot_reset_an_active_password(self):
        person = self.pending_person()
        uid, token = self.parts(self.link(self.acad, person))
        self.assertEqual(self.activate(uid, token).status_code, 200)
        self.assertEqual(self.activate(uid, token, password="Another-Pass-2026").status_code, 403)
        self.assertEqual(self.link(self.acad, person).status_code, 400)  # already activated
        person.user.refresh_from_db()
        self.assertTrue(person.user.check_password(PW))

    def test_login_that_has_signed_in_cannot_be_activated(self):
        """E.g. an identity-provider account without a local password is never taken over by activation."""
        person = self.pending_person()
        person.user.last_login = timezone.now()
        person.user.save(update_fields=["last_login"])
        self.assertEqual(self.link(self.acad, person).status_code, 400)

    def test_tampered_or_foreign_tokens_fail(self):
        a = self.pending_person()
        uid, token = self.parts(self.link(self.acad, a))
        self.assertEqual(self.activate(uid, token[:-2] + "zz").status_code, 403)
        self.assertEqual(self.activate("MTIzNDU2", token).status_code, 403)
        ev = self.last_audit()
        self.assertEqual((ev.action, ev.allowed), ("identity.login.activate", False))

    def test_activation_requires_csrf(self):
        person = self.pending_person()
        uid, token = self.parts(self.link(self.acad, person))
        r = self.activate(uid, token, client=APIClient(enforce_csrf_checks=True))
        self.assertEqual(r.status_code, 403)
        person.user.refresh_from_db()
        self.assertFalse(person.user.has_usable_password())

    def test_only_person_managers_issue_links_and_never_for_a_system_admin(self):
        person = self.pending_person()
        for actor in (self.phd, self.coe, self.p_sch1, self.coord, self.dept_admin):
            self.assertEqual(self.link(actor, person).status_code, 403)
        sysadmin2 = Person.objects.create(full_name="Sys Two", email="sys2@dypiu.ac.in")
        CapabilityAssignment.objects.create(person=sysadmin2, capability=C.SYSTEM_ADMIN, scope_type="INSTITUTION",
                                            valid_from=TODAY, basis="fixture", granted_by=self.sysadmin)
        user = get_user_model().objects.create_user("sys2@dypiu.ac.in")
        user.set_unusable_password()
        user.save()
        sysadmin2.user = user
        sysadmin2.save()
        self.assertEqual(self.link(self.acad, sysadmin2).status_code, 403)  # account takeover prevented


class SetupStatusTests(SetupFixture):
    def test_status_reports_steps_and_permissions(self):
        r = self.get(self.acad, "setup/status/")
        self.assertEqual(r.status_code, 200)
        steps = {s["key"]: s for s in r.json()["steps"]}
        self.assertEqual(steps["university"]["count"], 0)
        self.assertEqual(steps["schools"]["count"], 2)
        self.assertTrue(steps["schools"]["can_manage"])
        self.assertEqual(steps["departments"]["blocked_by"], [])
        phd = {s["key"]: s for s in self.get(self.phd, "setup/status/").json()["steps"]}
        self.assertFalse(phd["schools"]["can_manage"])
        self.assertTrue(phd["scholars"]["can_manage"])
        for person in (self.p_sch1, self.coord, self.vc):
            self.assertEqual(self.get(person, "setup/status/").status_code, 403)

    def test_university_list_starts_empty(self):
        self.assertEqual(University.objects.count(), 0)
        self.assertEqual(self.get(self.acad, "setup/universities/").json()["results"], [])
