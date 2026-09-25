"""Academic office APIs: R&D Cell (verification), COE (exams, hall tickets,
ratification, transcripts), PhD / R&D Cell (fee clearance, semester
registration, enrollment), Academic Admin / CISR (rules, structure).
Every mutation goes through the domain service, which authorizes it."""
from django.db.models import Q
from django.utils import timezone
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from core.models import Faculty
from coursework.academic import config as config_svc, examination, exam_admin, records, results
from coursework.academic import semester as semester_svc, structure
from coursework.academic.common import authorize_or_deny
from coursework.academic.enrollment import enroll
from coursework.models import (AcademicRuleParameter, Course, CourseOffering, CourseResult, Exam, ExamCycle,
                               ExamRegistration, FacultySubjectAssignment, FeeClearance, HallTicket,
                               ParameterChangeRequest, Section, Semester,
                               SemesterRegistration)
from scholars.models import Scholar

from .common import (AcademicView, ActionSerializer, body, can, course_data, dt, expect_status, load, offering_data,
                     paginate, person_name, require_any_holder, result_data, scholar_brief, semester_data)
from .scholar import current_semester, enrollment_data, status_summary, transcript_payload


# --- results chain ------------------------------------------------------------------------------

STAGES = {"verify": ("PREPARED", "academic.result.verify"), "ratify": ("VERIFIED", "academic.result.ratify")}


class ResultQueueView(AcademicView):
    """Results awaiting this person's step. Only results the person may act on are listed."""

    def get(self, request):
        stage = request.query_params.get("stage", "verify")
        if stage not in STAGES:
            stage = "verify"
        status, action = STAGES[stage]
        q = request.query_params.get("q", "").strip()
        qs = (CourseResult.objects.filter(status=status)
              .select_related("attempt__scholar__department", "attempt__course", "prepared_by", "verified_by",
                              "ratified_by").order_by("prepared_at"))
        if q:
            qs = qs.filter(Q(attempt__scholar__name__icontains=q) | Q(attempt__scholar__prn__icontains=q)
                           | Q(attempt__course__code__icontains=q))
        visible = [r for r in qs if can(self.person, action, r)]
        page = paginate(request, visible)
        page["results"] = [result_data(r, self.person, with_marks=True) for r in page["results"]]
        page["stage"] = stage
        return Response(page)


class ResultDetailView(AcademicView):
    def get(self, request, pk):
        r = load(CourseResult, pk, select=["attempt__scholar__department", "attempt__course"])
        authorize_or_deny(self.person, "academic.record.view", r, request=request)
        if self.person.scholar_profile_id == r.attempt.scholar_id and r.status != "RATIFIED":
            from django.http import Http404
            raise Http404
        return Response(result_data(r, self.person, with_marks=True))


class ResultTransitionView(AcademicView):
    transition = None

    class RatifyIn(ActionSerializer):
        acknowledge_provisional = serializers.BooleanField(required=False, default=False)

    class ReturnIn(ActionSerializer):
        reason = serializers.CharField(max_length=255)

    def post(self, request, pk):
        r = load(CourseResult, pk, select=["attempt"])
        expect_status(r, request)
        if self.transition == "verify":
            data = body(ActionSerializer, request)
            r = results.verify_result(self.person, r, remarks=data["remarks"], request=request)
        elif self.transition == "ratify":
            data = body(self.RatifyIn, request)
            r = results.ratify_result(self.person, r, remarks=data["remarks"],
                                      acknowledge_provisional=data["acknowledge_provisional"], request=request)
        else:
            data = body(self.ReturnIn, request)
            r = results.return_result(self.person, r, reason=data["reason"], request=request)
        return Response(result_data(r, self.person, with_marks=True))


# --- scholars (staff view) ------------------------------------------------------------------------

class ScholarListView(AcademicView):
    def get(self, request):
        q = request.query_params.get("q", "").strip()
        qs = Scholar.objects.select_related("department").order_by("name")
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(prn__icontains=q))
        visible = [s for s in qs if can(self.person, "academic.record.view", s)]
        page = paginate(request, visible)
        page["results"] = [scholar_brief(s) for s in page["results"]]
        return Response(page)


class ScholarRecordView(AcademicView):
    def get(self, request, pk):
        scholar = load(Scholar, pk, select=["department"])
        authorize_or_deny(self.person, "academic.record.view", scholar, request=request)
        today = timezone.localdate()
        sems = Semester.objects.filter(end_date__gte=today).select_related("academic_year").order_by("start_date")
        return Response({
            "scholar": scholar_brief(scholar),
            "profile": {**records.academic_profile(scholar), "registration_date": dt(scholar.registration_date)},
            "summary": status_summary(scholar),
            "enrollments": [enrollment_data(e, self.person) for e in scholar.course_enrollments.select_related(
                "offering__course", "offering__semester__academic_year", "section").order_by("-enrolled_at")],
            "semesters": [{"semester": semester_data(s),
                           "fee_clearance": _fee(scholar, s),
                           "registered": SemesterRegistration.objects.filter(scholar=scholar, semester=s,
                                                                             status="REGISTERED").exists()}
                          for s in sems],
            "can": {"record_fee_clearance": can(self.person, "academic.fee_clearance.record", scholar),
                    "register_semester": can(self.person, "academic.semester.register.manage", scholar),
                    "enroll": can(self.person, "academic.enrollment.manage", scholar),
                    "issue_transcript": can(self.person, "academic.transcript.issue", scholar),
                    "view_transcript": can(self.person, "academic.transcript.view", scholar)},
        })


def _fee(scholar, semester):
    fee = FeeClearance.objects.filter(scholar=scholar, semester=semester).first()
    return {"cleared": fee.cleared, "reference": fee.reference, "recorded_at": dt(fee.recorded_at)} if fee else None


class FeeClearanceView(AcademicView):
    class In(serializers.Serializer):
        semester_id = serializers.IntegerField()
        cleared = serializers.BooleanField()
        reference = serializers.CharField(max_length=120)

    def post(self, request, pk):
        data = body(self.In, request)
        row = semester_svc.record_fee_clearance(self.person, load(Scholar, pk), load(Semester, data["semester_id"]),
                                                cleared=data["cleared"], reference=data["reference"], request=request)
        return Response({"cleared": row.cleared, "reference": row.reference})


class ManageSemesterRegistrationView(AcademicView):
    class In(serializers.Serializer):
        semester_id = serializers.IntegerField()

    def post(self, request, pk):
        data = body(self.In, request)
        reg = semester_svc.register_semester(self.person, load(Scholar, pk), load(Semester, data["semester_id"]),
                                             request=request)
        return Response({"id": reg.pk, "status": reg.status}, status=201)


class EnrollView(AcademicView):
    """Enrollment by the scholar (own record: `/me/enrollments/`) or an office (`/scholars/<id>/enrollments/`)."""

    class In(serializers.Serializer):
        offering_id = serializers.IntegerField()
        section_id = serializers.IntegerField(required=False, allow_null=True)

    def post(self, request, pk=None):
        scholar = load(Scholar, pk) if pk is not None else self.person.scholar_profile
        if scholar is None:
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied("This workspace is for scholars")
        data = body(self.In, request)
        section = load(Section, data["section_id"]) if data.get("section_id") else None
        e = enroll(self.person, scholar, load(CourseOffering, data["offering_id"]), section=section, request=request)
        return Response({"id": e.pk, "status": e.status}, status=201)


class AvailableOfferingsView(AcademicView):
    """Open offerings in semesters the scholar is registered for (the service still validates enrollment)."""

    def get(self, request):
        scholar = self.person.scholar_profile
        if scholar is None:
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied("This workspace is for scholars")
        sems = SemesterRegistration.objects.filter(scholar=scholar, status="REGISTERED").values("semester_id")
        enrolled = scholar.course_enrollments.exclude(status="WITHDRAWN").values("offering_id")
        offerings = (CourseOffering.objects.filter(semester_id__in=sems, status="OPEN").exclude(pk__in=enrolled)
                     .select_related("course__department", "semester__academic_year").order_by("course__code"))
        return Response({"results": [{**offering_data(o), "sections": [{"id": s.pk, "code": s.code}
                                                                        for s in o.sections.order_by("code")]}
                                     for o in offerings]})


class ScholarTranscriptView(AcademicView):
    def get(self, request, pk):
        return Response(transcript_payload(self.person, load(Scholar, pk), request))


class IssueTranscriptView(AcademicView):
    def post(self, request, pk):
        issue = records.issue_transcript(self.person, load(Scholar, pk), request=request)
        return Response({"version": issue.version, "verification_code": issue.verification_code,
                         "issued_at": dt(issue.issued_at), "provisional": issue.provisional}, status=201)


class VerifyTranscriptView(AcademicView):
    """Public verification by code: reveals only PRN, version and validity."""
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, code):
        v = records.verify_transcript(code)
        if v.get("issued_at"):
            v["issued_at"] = dt(v["issued_at"])
        return Response(v)


# --- examinations (COE) ------------------------------------------------------------------------

def cycle_data(c, person=None):
    data = {"id": c.pk, "name": c.name, "semester": semester_data(c.semester),
            "registration_opens": dt(c.registration_opens), "registration_closes": dt(c.registration_closes),
            "exam_start": dt(c.exam_start), "exam_end": dt(c.exam_end),
            "exams": [{"id": e.pk, "course": course_data(e.course), "date": dt(e.scheduled_on),
                       "start_time": str(e.start_time), "end_time": str(e.end_time), "venue": e.venue,
                       "registrations": e.registrations.filter(status="REGISTERED").count()}
                      for e in c.exams.select_related("course").order_by("scheduled_on")]}
    return data


class ExamCyclesView(AcademicView):
    class In(serializers.Serializer):
        semester_id = serializers.IntegerField()
        name = serializers.CharField(max_length=80)
        registration_opens = serializers.DateField()
        registration_closes = serializers.DateField()
        exam_start = serializers.DateField()
        exam_end = serializers.DateField()

    def get(self, request):
        cycles = ExamCycle.objects.select_related("semester__academic_year").order_by("-exam_start")
        return Response({"results": [cycle_data(c) for c in cycles],
                         "can_manage": can(self.person, "academic.exam.manage", None)})

    def post(self, request):
        data = body(self.In, request)
        sem = load(Semester, data.pop("semester_id"))
        c = examination.create_exam_cycle(self.person, semester=sem, request=request, **data)
        return Response(cycle_data(c), status=201)


class ExamsCreateView(AcademicView):
    class In(serializers.Serializer):
        cycle_id = serializers.IntegerField()
        course_id = serializers.IntegerField()
        scheduled_on = serializers.DateField()
        start_time = serializers.TimeField()
        end_time = serializers.TimeField()
        venue = serializers.CharField(max_length=120, required=False, allow_blank=True, default="")

    def post(self, request):
        data = body(self.In, request)
        exam = examination.create_exam(self.person, cycle=load(ExamCycle, data.pop("cycle_id")),
                                       course=load(Course, data.pop("course_id")), request=request, **data)
        return Response({"id": exam.pk}, status=201)


class ExamRegistrationsView(AcademicView):
    def get(self, request, pk):
        exam = load(Exam, pk, select=["course", "cycle"])
        authorize_or_deny(self.person, ["academic.exam.manage", "academic.exam.register.manage"], exam,
                          request=request)
        regs = (exam.registrations.select_related("enrollment__scholar__department", "eligibility")
                .order_by("enrollment__scholar__name"))
        out = []
        for r in regs:
            ticket = r.hall_tickets.filter(status=HallTicket.Status.ISSUED).first()
            out.append({"id": r.pk, "status": r.status, "attempt_no": r.attempt_no,
                        "scholar": scholar_brief(r.enrollment.scholar), "registered_at": dt(r.registered_at),
                        "eligibility": {"eligible": r.eligibility.eligible, "reasons": r.eligibility.reasons,
                                        "warnings": r.eligibility.warnings,
                                        "evaluated_at": dt(r.eligibility.evaluated_at)},
                        "hall_ticket": ({"id": ticket.pk, "number": ticket.number, "issued_at": dt(ticket.issued_at)}
                                        if ticket else None)})
        return Response({"exam": {"id": exam.pk, "course": course_data(exam.course), "cycle": exam.cycle.name,
                                  "date": dt(exam.scheduled_on)},
                         "results": out,
                         "can_issue_hall_tickets": can(self.person, "academic.hall_ticket.issue", exam)})


class IssueHallTicketView(AcademicView):
    def post(self, request, pk):
        t = exam_admin.issue_hall_ticket(self.person, load(ExamRegistration, pk, select=["eligibility", "exam"]),
                                         request=request)
        return Response({"id": t.pk, "number": t.number, "verification_code": t.verification_code}, status=201)


class RevokeHallTicketView(AcademicView):
    class In(serializers.Serializer):
        reason = serializers.CharField(max_length=255)

    def post(self, request, pk):
        data = body(self.In, request)
        t = exam_admin.revoke_hall_ticket(self.person, load(HallTicket, pk), reason=data["reason"], request=request)
        return Response({"id": t.pk, "status": t.status})


# --- rules & configuration (maker-checker) ----------------------------------------------------------

class RulesView(AcademicView):
    """The regulatory rule register as the server applies it today."""

    def get(self, request):
        today = timezone.localdate()
        latest = {}
        for row in (AcademicRuleParameter.objects.filter(effective_from__lte=today)
                    .order_by("key", "-effective_from", "-version")):
            latest.setdefault(row.key, row)
        pending = ParameterChangeRequest.objects.filter(state="PENDING").select_related("proposed_by")
        return Response({
            "results": [{"key": r.key, "value": r.value, "status": r.status, "version": r.version,
                         "source": r.source, "reference": r.reference, "effective_from": dt(r.effective_from),
                         "approved_by": person_name(r.approved_by)} for r in latest.values()],
            "pending_changes": [{"id": c.pk, "key": c.key, "value": c.value, "status": c.status, "source": c.source,
                                 "reason": c.reason, "reference": c.reference, "proposed_by": person_name(c.proposed_by),
                                 "proposed_at": dt(c.proposed_at),
                                 "can_decide": can(self.person, "academic.config.approve")
                                 and c.proposed_by_id != self.person.pk} for c in pending],
            "can_propose": can(self.person, "academic.config.change"),
        })


class ProposeChangeView(AcademicView):
    class In(serializers.Serializer):
        key = serializers.CharField(max_length=100)
        value = serializers.JSONField(required=False, allow_null=True)
        status = serializers.CharField(max_length=12)
        source = serializers.CharField(max_length=255)
        reason = serializers.CharField(max_length=255)
        reference = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")

    def post(self, request):
        data = body(self.In, request)
        c = config_svc.propose_change(self.person, data["key"], value=data.get("value"), status=data["status"],
                                      source=data["source"], reason=data["reason"], reference=data["reference"],
                                      request=request)
        return Response({"id": c.pk, "state": c.state}, status=201)


class DecideChangeView(AcademicView):
    class In(serializers.Serializer):
        approve = serializers.BooleanField()
        remarks = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
        expected_status = serializers.CharField(required=False, allow_blank=True)

    def post(self, request, pk):
        change = load(ParameterChangeRequest, pk)
        expect_status(change, request, field="state")
        data = body(self.In, request)
        c = config_svc.decide_change(self.person, change, approve=data["approve"], remarks=data["remarks"],
                                     request=request)
        return Response({"id": c.pk, "state": c.state})


# --- structure (Academic Admin / CISR) ----------------------------------------------------------

class CatalogueView(AcademicView):
    """Course catalogue and semesters: institutional reference data."""

    def get(self, request):
        return Response({
            "courses": [course_data(c) for c in Course.objects.filter(is_active=True).select_related("department")],
            "semesters": [semester_data(s) for s in Semester.objects.select_related("academic_year")
                          .order_by("-start_date")],
            "current_semester_id": getattr(current_semester(), "pk", None),
        })


class GovernanceOfferingsView(AcademicView):
    class In(serializers.Serializer):
        course_id = serializers.IntegerField()
        semester_id = serializers.IntegerField()
        capacity = serializers.IntegerField(required=False, allow_null=True, min_value=1)

    def get(self, request):
        require_any_holder(request, ["academic.structure.manage", "academic.faculty_assignment.manage"])
        qs = CourseOffering.objects.select_related("course__department", "semester__academic_year").order_by(
            "-semester__start_date", "course__code")
        if request.query_params.get("semester"):
            qs = qs.filter(semester_id=request.query_params["semester"])
        today = timezone.localdate()
        out = []
        for o in qs:
            can_structure = can(self.person, "academic.structure.manage", o)
            can_assign = can(self.person, "academic.faculty_assignment.manage", o)
            if not (can_structure or can_assign):
                continue
            out.append({**offering_data(o),
                        "sections": [{"id": s.pk, "code": s.code} for s in o.sections.order_by("code")],
                        "assessments": o.assessments.count(),
                        "enrolled": o.enrollments.exclude(status="WITHDRAWN").count(),
                        "assignments": [{"id": a.pk, "faculty": a.faculty.name, "role": a.role,
                                         "section": a.section.code if a.section_id else None,
                                         "valid_to": dt(a.valid_to)}
                                        for a in o.faculty_assignments.filter(revoked_at__isnull=True)
                                        .exclude(valid_to__lt=today).select_related("faculty", "section")],
                        "can": {"structure": can_structure, "assign": can_assign}})
        return Response({"results": out})

    def post(self, request):
        data = body(self.In, request)
        o = structure.create_offering(self.person, course=load(Course, data["course_id"]),
                                      semester=load(Semester, data["semester_id"]), capacity=data.get("capacity"),
                                      request=request)
        return Response(offering_data(o), status=201)


class SectionCreateView(AcademicView):
    class In(serializers.Serializer):
        code = serializers.CharField(max_length=10)
        capacity = serializers.IntegerField(required=False, allow_null=True, min_value=1)

    def post(self, request, pk):
        data = body(self.In, request)
        s = structure.create_section(self.person, offering=load(CourseOffering, pk), code=data["code"],
                                     capacity=data.get("capacity"), request=request)
        return Response({"id": s.pk, "code": s.code}, status=201)


class DefineAssessmentsView(AcademicView):
    """Default scheme (taught course) or the configured activity scheme; the
    service blocks while the governing parameter is UNRESOLVED."""

    def post(self, request, pk):
        offering = load(CourseOffering, pk, select=["course"])
        if offering.course.category == Course.Category.ACTIVITY:
            created = structure.define_activity_assessments(self.person, offering, request=request)
        else:
            created = structure.define_default_assessments(self.person, offering, request=request)
        return Response({"created": len(created)}, status=201)


class AssignFacultyView(AcademicView):
    class In(serializers.Serializer):
        faculty_id = serializers.IntegerField()
        role = serializers.ChoiceField(choices=FacultySubjectAssignment.Role.choices)
        section_id = serializers.IntegerField(required=False, allow_null=True)
        basis = serializers.CharField(max_length=255)
        valid_to = serializers.DateField(required=False, allow_null=True)

    def post(self, request, pk):
        data = body(self.In, request)
        offering = load(CourseOffering, pk)
        a = structure.assign_faculty(self.person, faculty=load(Faculty, data["faculty_id"]), offering=offering,
                                     role=data["role"], basis=data["basis"],
                                     section=load(Section, data["section_id"]) if data.get("section_id") else None,
                                     valid_to=data.get("valid_to"), request=request)
        return Response({"id": a.pk}, status=201)


class RevokeAssignmentView(AcademicView):
    class In(serializers.Serializer):
        reason = serializers.CharField(max_length=255)

    def post(self, request, pk):
        data = body(self.In, request)
        structure.revoke_assignment(self.person, load(FacultySubjectAssignment, pk), reason=data["reason"],
                                    request=request)
        return Response(status=204)


class FacultyDirectoryView(AcademicView):
    def get(self, request):
        require_any_holder(request, ["academic.faculty_assignment.manage", "academic.structure.manage"])
        return Response({"results": [{"id": f.pk, "name": f.name, "department": f.department.name
                                      if f.department_id else None, "is_external": f.is_external}
                                     for f in Faculty.objects.select_related("department").order_by("name")]})

