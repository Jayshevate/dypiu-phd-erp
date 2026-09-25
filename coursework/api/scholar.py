"""Scholar workspace API. Every endpoint acts on the scholar linked to the
signed-in Person; there is no scholar id in these URLs, so a scholar cannot
address another scholar's record. Staff views of a scholar live in
`records.py` and are authorized per request."""
from django.utils import timezone
from rest_framework import serializers
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.response import Response

from coursework.academic import activities, electives, examination, exam_admin, records, semester as semester_svc
from coursework.academic import third_attempt
from coursework.academic.attendance import attendance_summary
from coursework.academic.common import authorize_or_deny, deny
from coursework.academic.config import RuleContext
from coursework.academic.results import visible_result
from coursework.models import (ActivitySubmission, AttendanceRecord, Course, CourseAttempt, ElectiveList,
                               ElectiveProposal, Exam, ExamRegistration, FeeClearance, HallTicket,
                               ScholarCourseEnrollment, Semester, SemesterRegistration, ThirdAttemptCase)

from .common import (AcademicView, body, course_data, dec, dt, faculty_names, load, offering_data, result_data,
                     semester_data)


def my_scholar(request):
    scholar = request.person.scholar_profile
    if scholar is None:
        deny(request.person, "academic.scholar_workspace", "This workspace is for scholars", request=request)
    return scholar


def current_semester(on=None):
    on = on or timezone.localdate()
    return (Semester.objects.filter(start_date__lte=on, end_date__gte=on).order_by("start_date").first()
            or Semester.objects.filter(start_date__gt=on).order_by("start_date").first())


def status_summary(scholar):
    ctx = RuleContext()
    status = records.coursework_status(scholar, ctx)
    standing = records.academic_standing(scholar, ctx)
    return {
        "coursework_complete": status["complete"],
        "credits_required": status["required_credits"],
        "credits_earned": status["credits_earned"],
        "credits_remaining": max(status["required_credits"] - status["credits_earned"], 0),
        "electives_required": records.academic_profile(scholar, ctx)["electives_required"],
        "electives_passed": status["electives_passed"],
        "gpa": dec(status["gpa"]),
        "reasons": status["reasons"],
        "provisional": status["provisional"],
        "standing": standing["standing"],
        "standing_courses": {code: {**v, "window_end": dt(v["window_end"])} for code, v in standing["courses"].items()},
    }


def enrollment_data(e, person, *, with_result=True):
    data = {"id": e.pk, "status": e.status, "offering": offering_data(e.offering),
            "section": e.section.code if e.section_id else None,
            "faculty": faculty_names(e.offering, e.section), "attendance": _attendance(e)}
    if with_result:
        attempt = e.attempts.exclude(status=CourseAttempt.Status.CANCELLED).order_by("-attempt_no").first()
        result = visible_result(person, attempt) if attempt else None
        data["latest_attempt"] = ({"attempt_no": attempt.attempt_no, "status": attempt.status} if attempt else None)
        data["result"] = ({"grade": result.grade, "outcome": result.outcome, "status": result.status}
                          if result else None)
    return data


def _attendance(e):
    if e.offering.course.category == Course.Category.ACTIVITY:
        return None
    s = attendance_summary(e)
    return {"sessions_held": s["sessions_held"], "sessions_attended": s["sessions_attended"],
            "percent": dec(s["percent"]), "physical_only": s["physical_only"]}


def _enrollments(scholar):
    return (ScholarCourseEnrollment.objects.filter(scholar=scholar)
            .select_related("offering__course__department", "offering__semester__academic_year", "section")
            .order_by("-offering__semester__start_date", "offering__course__code"))


class DashboardView(AcademicView):
    def get(self, request):
        scholar = my_scholar(request)
        authorize_or_deny(self.person, "academic.record.view", scholar, request=request)
        today = timezone.localdate()
        sem = current_semester(today)
        registered = sem is not None and SemesterRegistration.objects.filter(
            scholar=scholar, semester=sem, status="REGISTERED").exists()
        enrollments = [e for e in _enrollments(scholar) if e.status != "WITHDRAWN"]
        upcoming = (ExamRegistration.objects.filter(enrollment__scholar=scholar, status="REGISTERED",
                                                    exam__scheduled_on__gte=today)
                    .select_related("exam__course", "exam__cycle").order_by("exam__scheduled_on"))
        summary = status_summary(scholar)
        pending = []
        if sem is not None and not registered and sem.registration_opens and \
                sem.registration_opens <= today <= sem.registration_closes:
            pending.append({"kind": "SEMESTER_REGISTRATION", "text": f"Register for {sem.name}",
                            "link": "semester"})
        for e in enrollments:
            if e.status != "ENROLLED":
                continue
            open_exams = Exam.objects.filter(course=e.offering.course, cycle__registration_opens__lte=today,
                                             cycle__registration_closes__gte=today)
            for exam in open_exams:
                if not ExamRegistration.objects.filter(enrollment=e, exam=exam, status="REGISTERED").exists():
                    pending.append({"kind": "EXAM_REGISTRATION", "link": "exams",
                                    "text": f"Exam registration open for {exam.course.code} ({exam.cycle.name})"})
        for code, row in summary["standing_courses"].items():
            if row["standing"] == "THIRD_ATTEMPT_REQUIRED":
                pending.append({"kind": "THIRD_ATTEMPT", "link": "third-attempt",
                                "text": f"{code}: a further attempt needs an approved third-attempt case"})
            elif row["standing"] == "DECISION_REQUIRED":
                pending.append({"kind": "DECISION_REQUIRED", "link": "results",
                                "text": f"{code}: standing awaits an institutional rule decision"})
        return Response({
            "profile": {**records.academic_profile(scholar),
                        "registration_date": dt(scholar.registration_date)},
            "summary": summary,
            "current_semester": semester_data(sem) if sem else None,
            "semester_registered": registered,
            "enrollments": [enrollment_data(e, self.person, with_result=False) for e in enrollments],
            "upcoming_exams": [{"registration_id": r.pk, "course": r.exam.course.code, "title": r.exam.course.title,
                                "date": dt(r.exam.scheduled_on), "start_time": str(r.exam.start_time),
                                "end_time": str(r.exam.end_time), "venue": r.exam.venue, "attempt_no": r.attempt_no}
                               for r in upcoming],
            "pending_actions": pending,
        })


class SemesterRegistrationView(AcademicView):
    class In(serializers.Serializer):
        semester_id = serializers.IntegerField()

    def get(self, request):
        scholar = my_scholar(request)
        authorize_or_deny(self.person, "academic.record.view", scholar, request=request)
        today = timezone.localdate()
        ctx = RuleContext()
        rows = []
        for sem in Semester.objects.filter(end_date__gte=today).select_related("academic_year").order_by("start_date"):
            reg = SemesterRegistration.objects.filter(scholar=scholar, semester=sem).first()
            fee = FeeClearance.objects.filter(scholar=scholar, semester=sem).first()
            window_open = bool(sem.registration_opens and sem.registration_opens <= today <= sem.registration_closes)
            rows.append({"semester": semester_data(sem), "window_open": window_open,
                         "registration": ({"status": reg.status, "registered_at": dt(reg.registered_at)}
                                          if reg else None),
                         "fee_clearance": ({"cleared": fee.cleared, "reference": fee.reference} if fee else None)})
        return Response({"semesters": rows,
                         "requires_fee_clearance": ctx("semester_registration.requires_fee_clearance").value,
                         "rule_status": ctx("semester_registration.requires_fee_clearance").status})

    def post(self, request):
        scholar = my_scholar(request)
        data = body(self.In, request)
        reg = semester_svc.register_semester(self.person, scholar, load(Semester, data["semester_id"]),
                                             request=request)
        return Response({"id": reg.pk, "status": reg.status}, status=201)


class CoursesView(AcademicView):
    def get(self, request):
        scholar = my_scholar(request)
        authorize_or_deny(self.person, "academic.record.view", scholar, request=request)
        return Response({"results": [enrollment_data(e, self.person) for e in _enrollments(scholar)]})


class AttendanceView(AcademicView):
    def get(self, request):
        scholar = my_scholar(request)
        authorize_or_deny(self.person, "academic.record.view", scholar, request=request)
        out = []
        for e in _enrollments(scholar):
            if e.offering.course.category == Course.Category.ACTIVITY:
                continue
            sessions = AttendanceRecord.objects.filter(enrollment=e).select_related("session").order_by(
                "-session__date")
            out.append({"enrollment_id": e.pk, "course": course_data(e.offering.course),
                        "semester": e.offering.semester.name, "summary": _attendance(e),
                        "sessions": [{"date": dt(r.session.date), "start_time": str(r.session.start_time),
                                      "topic": r.session.topic, "status": r.status, "mode": r.mode}
                                     for r in sessions]})
        ctx = RuleContext()
        threshold = ctx("attendance.min_percent")
        return Response({"results": out, "rule": {
            "min_percent": threshold.value.get(scholar.mode), "status": threshold.status,
            "basis": ctx("attendance.basis").value, "basis_status": ctx("attendance.basis").status}})


class ResultsView(AcademicView):
    """Ratified results only (visible_result hides every earlier stage from a scholar)."""

    def get(self, request):
        scholar = my_scholar(request)
        out = []
        attempts = (CourseAttempt.objects.filter(scholar=scholar).exclude(status=CourseAttempt.Status.CANCELLED)
                    .exclude(status=CourseAttempt.Status.LEGACY).select_related("course", "scholar__department")
                    .order_by("course__code", "attempt_no"))
        for a in attempts:
            r = visible_result(self.person, a, request=request)
            out.append({"attempt_id": a.pk, "attempt_no": a.attempt_no, "course": course_data(a.course),
                        "result": result_data(r, with_marks=True) if r else None})
        return Response({"results": out, "summary": status_summary(scholar)})


class ExamsView(AcademicView):
    def get(self, request):
        scholar = my_scholar(request)
        authorize_or_deny(self.person, "academic.record.view", scholar, request=request)
        today = timezone.localdate()
        available = []
        for e in _enrollments(scholar):
            if e.status != "ENROLLED" or e.offering.course.category == Course.Category.ACTIVITY:
                continue
            for exam in Exam.objects.filter(course=e.offering.course, cycle__registration_closes__gte=today) \
                    .select_related("cycle", "course"):
                reg = ExamRegistration.objects.filter(enrollment=e, exam=exam, status="REGISTERED").first()
                elig = None if reg else examination.evaluate_eligibility(e, exam, persist=False)
                available.append({
                    "enrollment_id": e.pk, "exam": _exam_data(exam),
                    "window_open": exam.cycle.registration_opens <= today <= exam.cycle.registration_closes,
                    "registered": reg is not None,
                    "eligibility": ({"eligible": elig.eligible, "attempt_no": elig.attempt_no,
                                     "reasons": elig.reasons, "warnings": elig.warnings} if elig else None)})
        regs = (ExamRegistration.objects.filter(enrollment__scholar=scholar).select_related("exam__cycle", "exam__course")
                .order_by("-exam__scheduled_on"))
        registrations = []
        for r in regs:
            ticket = r.hall_tickets.filter(status=HallTicket.Status.ISSUED).first()
            registrations.append({"id": r.pk, "status": r.status, "attempt_no": r.attempt_no, "exam": _exam_data(r.exam),
                                  "registered_at": dt(r.registered_at),
                                  "hall_ticket": ({"id": ticket.pk, "number": ticket.number} if ticket else None)})
        return Response({"available": available, "registrations": registrations})


def _exam_data(exam):
    return {"id": exam.pk, "course": course_data(exam.course), "cycle": exam.cycle.name,
            "date": dt(exam.scheduled_on), "start_time": str(exam.start_time), "end_time": str(exam.end_time),
            "venue": exam.venue, "registration_opens": dt(exam.cycle.registration_opens),
            "registration_closes": dt(exam.cycle.registration_closes)}


class ExamRegistrationView(AcademicView):
    class In(serializers.Serializer):
        enrollment_id = serializers.IntegerField()
        exam_id = serializers.IntegerField()

    def post(self, request):
        my_scholar(request)
        data = body(self.In, request)
        reg = examination.register_for_exam(self.person, load(ScholarCourseEnrollment, data["enrollment_id"]),
                                            load(Exam, data["exam_id"]), request=request)
        return Response({"id": reg.pk, "status": reg.status, "attempt_no": reg.attempt_no}, status=201)


class HallTicketView(AcademicView):
    def get(self, request, pk):
        doc = exam_admin.hall_ticket_document(self.person, load(HallTicket, pk), request=request)
        doc["issued_at"] = dt(doc["issued_at"])
        doc["exam"] = {**doc["exam"], "date": dt(doc["exam"]["date"]), "start_time": str(doc["exam"]["start_time"]),
                       "end_time": str(doc["exam"]["end_time"])}
        return Response(doc)


def proposal_data(p):
    return {"id": p.pk, "semester": p.semester.name, "course": course_data(p.course) if p.course_id else None,
            "proposed_code": p.proposed_code, "proposed_title": p.proposed_title, "credits": p.credits,
            "justification": p.justification, "research_relevance": p.research_relevance, "status": p.status,
            "supervisor_remarks": p.supervisor_remarks, "decision_remarks": p.decision_remarks,
            "decided_as": p.decided_as, "submitted_at": dt(p.submitted_at),
            "scholar": {"id": p.scholar_id, "prn": p.scholar.prn, "name": p.scholar.name},
            "events": [{"action": ev.action, "capability": ev.capability, "remarks": ev.remarks, "at": dt(ev.at)}
                       for ev in p.events.order_by("at")]}


def elective_list_data(lst):
    return {"id": lst.pk, "semester": semester_data(lst.semester), "status": lst.status,
            "owner": lst.department.name if lst.department_id else lst.school.name,
            "owner_type": "DEPARTMENT" if lst.department_id else "SCHOOL",
            "current_step": lst.current_step, "approval_chain": lst.approval_chain,
            "items": [{"course": course_data(i.course), "intake_capacity": i.intake_capacity}
                      for i in lst.items.select_related("course")],
            "decisions": [{"step": d.step, "capability": d.capability, "approved": d.approved, "remarks": d.remarks,
                           "at": dt(d.at)} for d in lst.decisions.order_by("at")]}


class ElectivesView(AcademicView):
    class In(serializers.Serializer):
        semester_id = serializers.IntegerField()
        course_id = serializers.IntegerField()
        justification = serializers.CharField(max_length=4000)
        research_relevance = serializers.CharField(max_length=4000, required=False, allow_blank=True, default="")

    def get(self, request):
        scholar = my_scholar(request)
        authorize_or_deny(self.person, "academic.record.view", scholar, request=request)
        today = timezone.localdate()
        lists = (ElectiveList.objects.filter(status=ElectiveList.Status.APPROVED, semester__end_date__gte=today)
                 .filter(department=scholar.department) |
                 ElectiveList.objects.filter(status=ElectiveList.Status.APPROVED, semester__end_date__gte=today,
                                             school_id=scholar.department.school_id))
        ctx = RuleContext()
        category = records.academic_profile(scholar, ctx)["coursework_category"]
        return Response({
            "coursework_category": category,
            "electives_required": ctx("coursework.electives_required").value[category],
            "electives_required_status": ctx("coursework.electives_required").status,
            "lists": [{**elective_list_data(lst),
                       "items": [i for i in elective_list_data(lst)["items"]]}
                      for lst in lists.select_related("semester__academic_year", "department", "school").distinct()],
            "proposals": [proposal_data(p) for p in ElectiveProposal.objects.filter(scholar=scholar)
                          .select_related("semester", "course", "scholar").order_by("-submitted_at")],
            "rules": {k: {"value": ctx(k).value, "status": ctx(k).status}
                      for k in ("elective.approval_authority", "elective.supervisor_recommendation_required")},
        })

    def post(self, request):
        scholar = my_scholar(request)
        data = body(self.In, request)
        p = electives.propose(self.person, scholar, semester=load(Semester, data["semester_id"]),
                              course=load(Course, data["course_id"]), justification=data["justification"],
                              research_relevance=data["research_relevance"], request=request)
        return Response(proposal_data(p), status=201)


def submission_data(s):
    return {"id": s.pk, "artefact": s.artefact, "status": s.status, "description": s.description,
            "has_document": bool(s.document), "submitted_at": dt(s.submitted_at),
            "reviewed_at": dt(s.reviewed_at), "review_remarks": s.review_remarks}


class ActivitiesView(AcademicView):
    parser_classes = [MultiPartParser, JSONParser]

    class In(serializers.Serializer):
        enrollment_id = serializers.IntegerField()
        artefact = serializers.ChoiceField(choices=ActivitySubmission.Artefact.choices)
        description = serializers.CharField(max_length=4000, required=False, allow_blank=True, default="")
        document = serializers.FileField(required=False, allow_null=True)

    def get(self, request):
        scholar = my_scholar(request)
        authorize_or_deny(self.person, "academic.record.view", scholar, request=request)
        required = RuleContext()("activity.required_artefacts").value
        out = []
        for e in _enrollments(scholar):
            course = e.offering.course
            if course.category != Course.Category.ACTIVITY:
                continue
            attempt = e.attempts.order_by("-attempt_no").first()
            result = visible_result(self.person, attempt) if attempt else None
            out.append({"enrollment_id": e.pk, "course": course_data(course), "semester": e.offering.semester.name,
                        "status": e.status, "required_artefacts": required.get(course.activity_type, []),
                        "submissions": [submission_data(s) for s in e.activity_submissions.order_by("submitted_at")],
                        "evaluation_open": attempt is not None,
                        "result": ({"grade": result.grade, "outcome": result.outcome} if result else None)})
        return Response({"results": out})

    def post(self, request):
        my_scholar(request)
        data = body(self.In, request)
        sub = activities.submit_artefact(self.person, load(ScholarCourseEnrollment, data["enrollment_id"]),
                                         artefact=data["artefact"], description=data["description"],
                                         document=data.get("document"), request=request)
        return Response(submission_data(sub), status=201)


def case_data(c):
    return {"id": c.pk, "status": c.status, "course": course_data(c.enrollment.offering.course),
            "scholar": {"id": c.scholar_id, "prn": c.scholar.prn, "name": c.scholar.name},
            "attempts_used": c.attempts_used, "previous_attempts": c.previous_attempts, "reason": c.reason,
            "submitted_at": dt(c.submitted_at),
            "dean": {"at": dt(c.dean_at), "remarks": c.dean_remarks} if c.dean_at else None,
            "vc": {"at": dt(c.vc_at), "remarks": c.vc_remarks} if c.vc_at else None,
            "events": [{"action": e.action, "capability": e.capability, "remarks": e.remarks, "at": dt(e.at)}
                       for e in c.events.order_by("at")]}


class ThirdAttemptView(AcademicView):
    class In(serializers.Serializer):
        enrollment_id = serializers.IntegerField()
        reason = serializers.CharField(max_length=4000)

    def get(self, request):
        scholar = my_scholar(request)
        authorize_or_deny(self.person, "academic.record.view", scholar, request=request)
        standing = records.academic_standing(scholar)
        eligible = []
        for code, row in standing["courses"].items():
            if row["standing"] == "THIRD_ATTEMPT_REQUIRED":
                e = (ScholarCourseEnrollment.objects.filter(scholar=scholar, offering__course__code=code)
                     .exclude(status="WITHDRAWN").order_by("-enrolled_at").first())
                if e:
                    eligible.append({"enrollment_id": e.pk, "course_code": code,
                                     "counted_failures": row["counted_failures"]})
        cases = ThirdAttemptCase.objects.filter(scholar=scholar).select_related(
            "enrollment__offering__course", "scholar").order_by("-submitted_at")
        return Response({"standing": standing["standing"], "eligible": eligible,
                         "cases": [case_data(c) for c in cases]})

    def post(self, request):
        my_scholar(request)
        data = body(self.In, request)
        case = third_attempt.submit_case(self.person, load(ScholarCourseEnrollment, data["enrollment_id"]),
                                         reason=data["reason"], request=request)
        return Response(case_data(case), status=201)


def transcript_payload(person, scholar, request):
    t = records.transcript(person, scholar, request=request)
    t["generated_at"] = dt(t["generated_at"])
    current = t["content_hash"]
    t["issues"] = [{"version": i.version, "issued_at": dt(i.issued_at), "provisional": i.provisional,
                    "verification_code": i.verification_code, "matches_current_records": i.content_hash == current}
                   for i in scholar.transcript_issues.order_by("-version")]
    return t


class TranscriptView(AcademicView):
    def get(self, request):
        return Response(transcript_payload(self.person, my_scholar(request), request))


class RevaluationView(AcademicView):
    """Revaluation is governed by UNRESOLVED parameters; the service blocks every
    request until DYPIU configures them. The UI shows the rule status."""

    class In(serializers.Serializer):
        result_id = serializers.IntegerField()
        reason = serializers.CharField(max_length=4000)

    def get(self, request):
        from coursework.models import RevaluationCase
        scholar = my_scholar(request)
        authorize_or_deny(self.person, "academic.record.view", scholar, request=request)
        ctx = RuleContext()
        keys = ("revaluation.enabled", "revaluation.request_window_days", "revaluation.reviewer")
        return Response({
            "rules": {k: {"value": ctx(k).value, "status": ctx(k).status} for k in keys},
            "available": not any(ctx(k).unresolved for k in keys) and bool(ctx("revaluation.enabled").value),
            "cases": [{"id": c.pk, "status": c.status, "course": c.original_result.attempt.course.code,
                       "requested_at": dt(c.requested_at), "review_remarks": c.review_remarks}
                      for c in RevaluationCase.objects.filter(original_result__attempt__scholar=scholar)
                      .select_related("original_result__attempt__course").order_by("-requested_at")]})

    def post(self, request):
        from coursework.academic import revaluation
        from coursework.models import CourseResult
        my_scholar(request)
        data = body(self.In, request)
        case = revaluation.request_revaluation(self.person, load(CourseResult, data["result_id"]),
                                               reason=data["reason"], request=request)
        return Response({"id": case.pk, "status": case.status}, status=201)
