"""Faculty / Course Coordinator / SDRC workspace API.

A faculty member sees only offerings they are actively assigned to (expired or
revoked assignments drop out immediately, because every check reads the
assignment's validity on each request). Instructors see only their sections;
the Course Coordinator sees the whole offering. SDRC sees activity work of
scholars within its committee's school."""
from django.http import FileResponse
from django.utils import timezone
from rest_framework import serializers
from rest_framework.response import Response

from coursework.academic import activities, attendance as attendance_svc, examination, marks as marks_svc, results
from coursework.academic.common import authorize_or_deny
from coursework.academic.config import RuleContext
from coursework.academic.policies import coordinates_offering
from coursework.models import (ActivitySubmission, Assessment, AttendanceRecord, AttendanceSession, Course,
                               CourseAttempt, CourseOffering, CourseResult, FacultySubjectAssignment, MarkEntry,
                               ScholarCourseEnrollment, Section)
from identity.api_base import Conflict

from .common import (AcademicView, body, can, course_data, dec, dt, load, mark_data, offering_data, result_data,
                     scholar_brief)
from .scholar import submission_data


def active_assignments(person):
    if not person.faculty_profile_id:
        return FacultySubjectAssignment.objects.none()
    today = timezone.localdate()
    return (FacultySubjectAssignment.objects.filter(faculty_id=person.faculty_profile_id, revoked_at__isnull=True,
                                                    valid_from__lte=today).exclude(valid_to__lt=today)
            .select_related("offering__course__department", "offering__semester__academic_year", "section"))


def teaching_view(request, offering):
    """Authorize reading an offering and return the sections the person may see
    (None = all sections)."""
    authorize_or_deny(request.person, "academic.offering.view", offering, request=request)
    if coordinates_offering(request.person, offering) or not request.person.faculty_profile_id:
        return None
    own = set(active_assignments(request.person).filter(offering=offering, role="INSTRUCTOR")
              .values_list("section_id", flat=True))
    if not own:
        return None        # governance office (scope-based view of the whole offering)
    return own


def assessment_data(a):
    return {"id": a.pk, "name": a.name, "kind": a.kind, "weight": dec(a.weight), "max_marks": dec(a.max_marks),
            "sequence": a.sequence, "reweighting_reference": a.reweighting_reference}


class AssignmentsView(AcademicView):
    def get(self, request):
        rows = {}
        for a in active_assignments(self.person):
            row = rows.setdefault(a.offering_id, {"offering": offering_data(a.offering), "roles": [], "sections": [],
                                                  "enrolled": a.offering.enrollments.exclude(status="WITHDRAWN").count(),
                                                  "assessments_defined": a.offering.assessments.exists()})
            if a.role not in row["roles"]:
                row["roles"].append(a.role)
            if a.section_id:
                row["sections"].append({"id": a.section_id, "code": a.section.code})
        return Response({"results": list(rows.values())})


class OfferingView(AcademicView):
    def get(self, request, pk):
        offering = load(CourseOffering, pk, select=["course", "semester__academic_year"])
        sections = teaching_view(request, offering)
        return Response({
            "offering": offering_data(offering),
            "is_coordinator": coordinates_offering(self.person, offering),
            "sections": [{"id": s.pk, "code": s.code, "capacity": s.capacity,
                          "enrolled": s.enrollments.exclude(status="WITHDRAWN").count()}
                         for s in offering.sections.order_by("code") if sections is None or s.pk in sections],
            "assessments": [assessment_data(a) for a in offering.assessments.order_by("sequence")],
            "faculty": [{"name": a.faculty.name, "role": a.role, "section": a.section.code if a.section_id else None,
                         "valid_to": dt(a.valid_to)}
                        for a in offering.faculty_assignments.filter(revoked_at__isnull=True).select_related(
                            "faculty", "section")],
        })


class RosterView(AcademicView):
    def get(self, request, pk):
        offering = load(CourseOffering, pk)
        sections = teaching_view(request, offering)
        qs = offering.enrollments.select_related("scholar__department", "section").order_by("scholar__name")
        if sections is not None:
            qs = qs.filter(section_id__in=sections)
        q = request.query_params.get("q", "").strip().lower()
        out = []
        for e in qs:
            if q and q not in e.scholar.name.lower() and q not in e.scholar.prn.lower():
                continue
            s = attendance_svc.attendance_summary(e) if offering.course.category != Course.Category.ACTIVITY else None
            out.append({"enrollment_id": e.pk, "scholar": scholar_brief(e.scholar), "status": e.status,
                        "section": e.section.code if e.section_id else None,
                        "attendance": ({"held": s["sessions_held"], "attended": s["sessions_attended"],
                                        "percent": dec(s["percent"])} if s else None)})
        return Response({"results": out})


class SessionsView(AcademicView):
    class In(serializers.Serializer):
        date = serializers.DateField()
        start_time = serializers.TimeField()
        end_time = serializers.TimeField()
        topic = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")

    def get(self, request, pk):
        section = load(Section, pk, select=["offering"])
        authorize_or_deny(self.person, "academic.attendance.record", section, request=request)
        sessions = section.attendance_sessions.order_by("-date", "-start_time")
        return Response({"results": [{"id": s.pk, "date": dt(s.date), "start_time": str(s.start_time),
                                      "end_time": str(s.end_time), "topic": s.topic,
                                      "recorded": s.records.count()} for s in sessions],
                         "can_record": True})

    def post(self, request, pk):
        section = load(Section, pk)
        data = body(self.In, request)
        s = attendance_svc.create_session(self.person, section, request=request, **data)
        return Response({"id": s.pk}, status=201)


class SessionRecordsView(AcademicView):
    class In(serializers.Serializer):
        enrollment_id = serializers.IntegerField()
        status = serializers.ChoiceField(choices=AttendanceRecord.Status.choices)
        mode = serializers.CharField(required=False, allow_blank=True, default="")
        reason = serializers.CharField(required=False, allow_blank=True, default="", max_length=255)
        expected_updated_at = serializers.CharField(required=False, allow_blank=True)

    def get(self, request, pk):
        session = load(AttendanceSession, pk, select=["section__offering"])
        authorize_or_deny(self.person, "academic.attendance.record", session, request=request)
        records = {r.enrollment_id: r for r in session.records.all()}
        enrollments = session.section.offering.enrollments.exclude(status="WITHDRAWN").filter(
            section=session.section).select_related("scholar__department").order_by("scholar__name")
        return Response({
            "session": {"id": session.pk, "date": dt(session.date), "start_time": str(session.start_time),
                        "end_time": str(session.end_time), "topic": session.topic},
            "modes": AttendanceRecord.Mode.values,
            "results": [{"enrollment_id": e.pk, "scholar": scholar_brief(e.scholar),
                         "status": getattr(records.get(e.pk), "status", None),
                         "mode": getattr(records.get(e.pk), "mode", ""),
                         "updated_at": dt(getattr(records.get(e.pk), "updated_at", None))} for e in enrollments]})

    def post(self, request, pk):
        session = load(AttendanceSession, pk)
        data = body(self.In, request)
        enrollment = load(ScholarCourseEnrollment, data["enrollment_id"])
        existing = AttendanceRecord.objects.filter(session=session, enrollment=enrollment).first()
        if existing and data.get("expected_updated_at") and dt(existing.updated_at) != data["expected_updated_at"]:
            raise Conflict("Attendance for this scholar was changed by someone else. Reload and try again.")
        r = attendance_svc.record_attendance(self.person, session, enrollment, status=data["status"],
                                             mode=data["mode"], reason=data["reason"], request=request)
        return Response({"id": r.pk, "status": r.status, "mode": r.mode, "updated_at": dt(r.updated_at)})


def _attempts(offering, sections):
    qs = (CourseAttempt.objects.filter(enrollment__offering=offering)
          .exclude(status__in=[CourseAttempt.Status.CANCELLED, CourseAttempt.Status.LEGACY])
          .select_related("scholar__department", "enrollment__section", "course").order_by("scholar__name"))
    if sections is not None:
        qs = qs.filter(enrollment__section_id__in=sections)
    return qs


class MarksView(AcademicView):
    """Marks grid for the attempts under evaluation in an offering."""

    def get(self, request, pk):
        offering = load(CourseOffering, pk, select=["course"])
        sections = teaching_view(request, offering)
        assessments = list(offering.assessments.order_by("sequence"))
        attempts = list(_attempts(offering, sections))
        entries = {(m.attempt_id, m.assessment_id): m
                   for m in MarkEntry.objects.filter(attempt__in=attempts)}
        probe = attempts[0] if attempts else offering
        return Response({
            "assessments": [{**assessment_data(a),
                             "can_enter": can(self.person, marks_svc.ACTION_BY_KIND[a.kind], probe)}
                            for a in assessments],
            "results": [{"attempt_id": a.pk, "attempt_no": a.attempt_no, "status": a.status,
                         "scholar": scholar_brief(a.scholar),
                         "section": a.enrollment.section.code if a.enrollment and a.enrollment.section_id else None,
                         "locked": marks_svc.marks_locked(a),
                         "marks": {str(asmt.pk): mark_data(entries[(a.pk, asmt.pk)])
                                   for asmt in assessments if (a.pk, asmt.pk) in entries}}
                        for a in attempts],
        })


class MarkEntryView(AcademicView):
    class In(serializers.Serializer):
        assessment_id = serializers.IntegerField()
        attempt_id = serializers.IntegerField()
        marks = serializers.DecimalField(max_digits=6, decimal_places=2, required=False, allow_null=True)
        absent = serializers.BooleanField(required=False, default=False)
        cleared = serializers.BooleanField(required=False, allow_null=True, default=None)
        reason = serializers.CharField(required=False, allow_blank=True, default="", max_length=255)
        expected_updated_at = serializers.CharField(required=False, allow_blank=True)

    def post(self, request):
        data = body(self.In, request)
        assessment = load(Assessment, data["assessment_id"])
        attempt = load(CourseAttempt, data["attempt_id"], select=["enrollment"])
        existing = MarkEntry.objects.filter(assessment=assessment, attempt=attempt).first()
        if existing and data.get("expected_updated_at") and dt(existing.updated_at) != data["expected_updated_at"]:
            raise Conflict("These marks were changed by someone else. Reload and try again.")
        entry = marks_svc.enter_mark(self.person, assessment, attempt, marks=data.get("marks"), absent=data["absent"],
                                     cleared=data["cleared"], reason=data["reason"], request=request)
        return Response(mark_data(entry))


class OfferingResultsView(AcademicView):
    def get(self, request, pk):
        offering = load(CourseOffering, pk)
        sections = teaching_view(request, offering)
        out = []
        for a in _attempts(offering, sections):
            r = results.current_result(a)
            out.append({"attempt_id": a.pk, "attempt_no": a.attempt_no, "attempt_status": a.status,
                        "scholar": scholar_brief(a.scholar),
                        "result": result_data(r, self.person) if r else None,
                        "can_prepare": (r is None or r.status == CourseResult.Status.RETURNED)
                        and a.status == CourseAttempt.Status.SCHEDULED
                        and can(self.person, "academic.result.prepare.course", a)})
        return Response({"results": out})


class PrepareResultView(AcademicView):
    def post(self, request, pk):
        attempt = load(CourseAttempt, pk, select=["course", "enrollment"])
        r = results.prepare_result(self.person, attempt, request=request)
        return Response(result_data(r, self.person, with_marks=True), status=201)


# --- SDRC --------------------------------------------------------------------------------------

class SdrcActivitiesView(AcademicView):
    """Activity enrollments this person may review (SDRC of the scholar's school)."""

    def get(self, request):
        status_filter = request.query_params.get("status", "")
        qs = (ScholarCourseEnrollment.objects.filter(offering__course__category=Course.Category.ACTIVITY)
              .exclude(status="WITHDRAWN")
              .select_related("scholar__department", "offering__course", "offering__semester__academic_year")
              .order_by("offering__course__code", "scholar__name"))
        required = RuleContext()("activity.required_artefacts").value
        out = []
        for e in qs:
            if not can(self.person, "academic.activity.review", e):
                continue
            subs = list(e.activity_submissions.order_by("submitted_at"))
            if status_filter == "PENDING" and not any(s.status == "SUBMITTED" for s in subs):
                continue
            attempt = e.attempts.exclude(status=CourseAttempt.Status.CANCELLED).order_by("-attempt_no").first()
            r = results.current_result(attempt) if attempt else None
            asmt = e.offering.assessments.filter(kind=Assessment.Kind.ACTIVITY).first()
            mark = MarkEntry.objects.filter(attempt=attempt, assessment=asmt).first() if attempt and asmt else None
            out.append({"enrollment_id": e.pk, "scholar": scholar_brief(e.scholar), "course": course_data(e.offering.course),
                        "semester": e.offering.semester.name,
                        "required_artefacts": required.get(e.offering.course.activity_type, []),
                        "submissions": [submission_data(s) for s in subs],
                        "attempt": ({"id": attempt.pk, "status": attempt.status, "attempt_no": attempt.attempt_no}
                                    if attempt else None),
                        "assessment": assessment_data(asmt) if asmt else None,
                        "mark": mark_data(mark) if mark else None,
                        "result": result_data(r, self.person) if r else None})
        return Response({"results": out})


class ReviewSubmissionView(AcademicView):
    class In(serializers.Serializer):
        accept = serializers.BooleanField()
        remarks = serializers.CharField(required=False, allow_blank=True, default="", max_length=4000)
        expected_status = serializers.CharField(required=False, allow_blank=True)

    def post(self, request, pk):
        sub = load(ActivitySubmission, pk, select=["enrollment__scholar"])
        data = body(self.In, request)
        if data.get("expected_status") and sub.status != data["expected_status"]:
            raise Conflict(f"This submission is now {sub.status}. Reload and try again.")
        sub = activities.review_artefact(self.person, sub, accept=data["accept"], remarks=data["remarks"],
                                         request=request)
        return Response(submission_data(sub))


class SubmissionDocumentView(AcademicView):
    def get(self, request, pk):
        sub = load(ActivitySubmission, pk, select=["enrollment__scholar"])
        authorize_or_deny(self.person, ["academic.activity.review", "academic.record.view"], sub, request=request)
        if not sub.document:
            from django.http import Http404
            raise Http404
        return FileResponse(sub.document.open("rb"), as_attachment=True,
                            filename=sub.document.name.rsplit("/", 1)[-1])


class OpenEvaluationView(AcademicView):
    def post(self, request, pk):
        enrollment = load(ScholarCourseEnrollment, pk, select=["offering__course", "scholar"])
        attempt = examination.open_activity_evaluation(self.person, enrollment, request=request)
        return Response({"attempt_id": attempt.pk, "attempt_no": attempt.attempt_no}, status=201)
