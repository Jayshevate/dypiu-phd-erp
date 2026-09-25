"""Approval workflows: third-attempt cases (Dean R&D → VC), elective proposals
(supervisor recommendation → configured authority), department/school
elective lists (staged chain), question-paper setters (DC → COE receipt).

Where the approving authority is a regulatory parameter the `can` flags are
computed from the same parameter the service uses; if it is UNRESOLVED the
flag is false and `blocked_reason` explains why."""
from django.utils import timezone
from rest_framework import serializers
from rest_framework.response import Response

from core.models import Department, Faculty, School
from coursework.academic import elective_lists, electives, exam_admin, third_attempt
from coursework.academic.config import RuleContext
from coursework.academic.elective_lists import _scope as list_scope
from coursework.academic.third_attempt import _step_policy
from coursework.models import (Course, ElectiveList, ElectiveProposal, Exam, QuestionPaperSetterAppointment,
                               Semester, ThirdAttemptCase)
from identity.authz import evaluate_policy
from identity.models import Person

from .common import AcademicView, body, can, can_policy, course_data, dt, expect_status, load, paginate
from .scholar import case_data, elective_list_data, proposal_data


# --- third attempt ------------------------------------------------------------------------------

def _step_allowed(person, step, case):
    action, policy = _step_policy(step)
    return evaluate_policy(person, action, policy, case).allowed


class ThirdAttemptCasesView(AcademicView):
    def get(self, request):
        status = request.query_params.get("status", "")
        qs = ThirdAttemptCase.objects.select_related("scholar__department", "enrollment__offering__course").order_by(
            "-submitted_at")
        if status:
            qs = qs.filter(status=status)
        mentor_rule = RuleContext()("third_attempt.mentor_required")
        out = []
        for c in qs:
            reviewer, decider = _step_allowed(self.person, 0, c), _step_allowed(self.person, 1, c)
            if not (reviewer or decider or can(self.person, "academic.record.view", c)):
                continue
            row = case_data(c)
            row["can"] = {
                "review": reviewer and c.status == "SUBMITTED" and c.submitted_by_id != self.person.pk,
                "decide": decider and c.status in ("DEAN_RECOMMENDED", "DEAN_NOT_RECOMMENDED")
                and self.person.pk not in (c.submitted_by_id, c.dean_by_id),
            }
            row["approval_blocked_reason"] = (
                "Whether a faculty mentor must be assigned is unresolved (third_attempt.mentor_required); "
                "approval requires an institutional decision. Rejection is still possible."
                if row["can"]["decide"] and mentor_rule.unresolved else None)
            row["mentor_required"] = mentor_rule.value
            out.append(row)
        return Response(paginate(request, out))


class ThirdAttemptReviewView(AcademicView):
    class In(serializers.Serializer):
        recommend = serializers.BooleanField()
        remarks = serializers.CharField(max_length=4000)
        expected_status = serializers.CharField(required=False, allow_blank=True)

    def post(self, request, pk):
        case = load(ThirdAttemptCase, pk)
        expect_status(case, request)
        data = body(self.In, request)
        case = third_attempt.dean_review(self.person, case, recommend=data["recommend"], remarks=data["remarks"],
                                         request=request)
        return Response(case_data(case))


class ThirdAttemptDecideView(AcademicView):
    class In(serializers.Serializer):
        approve = serializers.BooleanField()
        remarks = serializers.CharField(max_length=4000)
        mentor_faculty_id = serializers.IntegerField(required=False, allow_null=True)
        expected_status = serializers.CharField(required=False, allow_blank=True)

    def post(self, request, pk):
        case = load(ThirdAttemptCase, pk)
        expect_status(case, request)
        data = body(self.In, request)
        mentor = load(Faculty, data["mentor_faculty_id"]) if data.get("mentor_faculty_id") else None
        case = third_attempt.vc_decide(self.person, case, approve=data["approve"], remarks=data["remarks"],
                                       mentor=mentor, request=request)
        return Response(case_data(case))


# --- elective proposals -------------------------------------------------------------------------

def _decide_state(person, proposal, ctx):
    authority = ctx("elective.approval_authority")
    needs_rec = ctx("elective.supervisor_recommendation_required")
    if authority.unresolved:
        return False, "The approving authority is unresolved (elective.approval_authority); requires institutional decision"
    allowed = can_policy(person, authority.value, proposal)
    if not allowed:
        return False, None
    if needs_rec.unresolved:
        return False, ("Whether a supervisor recommendation is required is unresolved "
                       "(elective.supervisor_recommendation_required); requires institutional decision")
    open_states = ["SUBMITTED", "SUPERVISOR_RECOMMENDED", "SUPERVISOR_NOT_RECOMMENDED"]
    if needs_rec.value is True:
        open_states = open_states[1:]
    return (proposal.status in open_states and person.pk not in (proposal.submitted_by_id, proposal.supervisor_by_id),
            None)


class ElectiveProposalsView(AcademicView):
    def get(self, request):
        ctx = RuleContext()
        qs = ElectiveProposal.objects.select_related("scholar__department", "semester", "course").order_by(
            "-submitted_at")
        if request.query_params.get("status"):
            qs = qs.filter(status=request.query_params["status"])
        authority = ctx("elective.approval_authority")
        out = []
        for p in qs:
            recommend = can(self.person, "academic.elective.recommend", p)
            decider = (not authority.unresolved) and can_policy(self.person, authority.value, p)
            if not (recommend or decider or can(self.person, "academic.record.view", p)):
                continue
            can_decide, blocked = _decide_state(self.person, p, ctx)
            row = proposal_data(p)
            row["can"] = {"recommend": recommend and p.status == "SUBMITTED", "decide": can_decide}
            row["decision_blocked_reason"] = blocked
            out.append(row)
        return Response({**paginate(request, out),
                         "rules": {k: {"value": ctx(k).value, "status": ctx(k).status}
                                   for k in ("elective.approval_authority",
                                             "elective.supervisor_recommendation_required")}})


class ElectiveRecommendView(AcademicView):
    class In(serializers.Serializer):
        recommend = serializers.BooleanField()
        remarks = serializers.CharField(max_length=4000, required=False, allow_blank=True, default="")
        expected_status = serializers.CharField(required=False, allow_blank=True)

    def post(self, request, pk):
        p = load(ElectiveProposal, pk)
        expect_status(p, request)
        data = body(self.In, request)
        p = electives.supervisor_recommend(self.person, p, recommend=data["recommend"], remarks=data["remarks"],
                                           request=request)
        return Response(proposal_data(p))


class ElectiveDecideView(AcademicView):
    class In(serializers.Serializer):
        approve = serializers.BooleanField()
        remarks = serializers.CharField(max_length=4000, required=False, allow_blank=True, default="")
        expected_status = serializers.CharField(required=False, allow_blank=True)

    def post(self, request, pk):
        p = load(ElectiveProposal, pk)
        expect_status(p, request)
        data = body(self.In, request)
        p = electives.decide(self.person, p, approve=data["approve"], remarks=data["remarks"], request=request)
        return Response(proposal_data(p))


# --- elective lists ---------------------------------------------------------------------------

class ElectiveListsView(AcademicView):
    class Item(serializers.Serializer):
        course_id = serializers.IntegerField()
        intake_capacity = serializers.IntegerField(required=False, allow_null=True, min_value=1)

    class In(serializers.Serializer):
        semester_id = serializers.IntegerField()
        department_id = serializers.IntegerField(required=False, allow_null=True)
        school_id = serializers.IntegerField(required=False, allow_null=True)
        items = serializers.ListField(child=serializers.DictField(), allow_empty=False)

    def get(self, request):
        ctx_chain = RuleContext()("elective.list_approval_chain")
        out = []
        for lst in ElectiveList.objects.select_related("semester__academic_year", "department", "school").order_by(
                "-prepared_at"):
            scope = list_scope(lst)
            preparer = can(self.person, "academic.elective_list.prepare", scope)
            chain = lst.approval_chain or (ctx_chain.value or [])
            approver = any(can_policy(self.person, [c], scope) for c in chain)
            if not (preparer or approver):
                continue
            step_cap = lst.approval_chain[lst.current_step] if lst.status == "SUBMITTED" else None
            row = elective_list_data(lst)
            row["can"] = {
                "submit": preparer and lst.status in ("DRAFT", "RETURNED"),
                "decide": bool(step_cap) and can_policy(self.person, [step_cap], scope)
                and lst.prepared_by_id != self.person.pk
                and not lst.decisions.filter(actor=self.person).exists(),
            }
            out.append(row)
        return Response({"results": out, "chain": {"value": ctx_chain.value, "status": ctx_chain.status}})

    def post(self, request):
        data = body(self.In, request)
        items = []
        for raw in data["items"]:
            item = self.Item(data=raw)
            item.is_valid(raise_exception=True)
            items.append((load(Course, item.validated_data["course_id"]), item.validated_data.get("intake_capacity")))
        lst = elective_lists.prepare_list(
            self.person, semester=load(Semester, data["semester_id"]), items=items,
            department=load(Department, data["department_id"]) if data.get("department_id") else None,
            school=load(School, data["school_id"]) if data.get("school_id") else None, request=request)
        return Response(elective_list_data(lst), status=201)


class ElectiveListSubmitView(AcademicView):
    def post(self, request, pk):
        lst = load(ElectiveList, pk)
        expect_status(lst, request)
        return Response(elective_list_data(elective_lists.submit_list(self.person, lst, request=request)))


class ElectiveListDecideView(AcademicView):
    class In(serializers.Serializer):
        approve = serializers.BooleanField()
        remarks = serializers.CharField(max_length=4000, required=False, allow_blank=True, default="")
        expected_status = serializers.CharField(required=False, allow_blank=True)
        expected_step = serializers.IntegerField(required=False)

    def post(self, request, pk):
        lst = load(ElectiveList, pk)
        expect_status(lst, request)
        data = body(self.In, request)
        if data.get("expected_step") is not None and data["expected_step"] != lst.current_step:
            from identity.api_base import Conflict
            raise Conflict("This list moved to another approval step. Reload and try again.")
        lst = elective_lists.decide_list(self.person, lst, approve=data["approve"], remarks=data["remarks"],
                                         request=request)
        return Response(elective_list_data(lst))


# --- question-paper setters ---------------------------------------------------------------------

def appointment_data(a):
    return {"id": a.pk, "setter": a.setter.full_name, "status": a.status, "basis": a.basis,
            "appointed_as": a.appointed_as, "appointed_at": dt(a.appointed_at), "received_at": dt(a.received_at)}


class QuestionPapersView(AcademicView):
    """Upcoming exams with their setter appointments, for the DC (appoint) and COE (record receipt)."""

    def get(self, request):
        authority = RuleContext()("question_paper.setter_appointed_by")
        today = timezone.localdate()
        out = []
        for exam in Exam.objects.filter(scheduled_on__gte=today).select_related("course__department", "cycle").order_by(
                "scheduled_on"):
            appoint = (not authority.unresolved) and can_policy(self.person, authority.value, exam)
            receive = can(self.person, "academic.question_paper.receive", exam)
            if not (appoint or receive):
                continue
            out.append({"exam": {"id": exam.pk, "course": course_data(exam.course), "cycle": exam.cycle.name,
                                 "date": dt(exam.scheduled_on)},
                        "appointments": [{**appointment_data(a), "can_receive": receive and a.status == "APPOINTED"}
                                         for a in exam.setter_appointments.select_related("setter").order_by(
                                             "appointed_at")],
                        "can_appoint": appoint})
        candidates = [{"person_id": p.pk, "name": p.full_name,
                       "department": p.faculty_profile.department.name if p.faculty_profile.department_id else None,
                       "is_external": p.faculty_profile.is_external}
                      for p in Person.objects.filter(is_active=True, faculty_profile__isnull=False)
                      .select_related("faculty_profile__department").order_by("full_name")] \
            if any(r["can_appoint"] for r in out) else []
        return Response({"results": out, "candidates": candidates,
                         "authority": {"value": authority.value, "status": authority.status}})


class AppointSetterView(AcademicView):
    class In(serializers.Serializer):
        setter_person_id = serializers.IntegerField()
        basis = serializers.CharField(max_length=255)

    def post(self, request, pk):
        data = body(self.In, request)
        a = exam_admin.appoint_question_paper_setter(self.person, load(Exam, pk), load(Person, data["setter_person_id"]),
                                                     basis=data["basis"], request=request)
        return Response(appointment_data(a), status=201)


class ReceivePaperView(AcademicView):
    def post(self, request, pk):
        a = load(QuestionPaperSetterAppointment, pk, select=["exam__course"])
        expect_status(a, request)
        return Response(appointment_data(exam_admin.record_question_paper_received(self.person, a, request=request)))
