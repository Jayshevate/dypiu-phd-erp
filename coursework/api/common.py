"""Shared helpers for the Academic REST API.

Views are thin: they load objects, call `coursework.academic.*` services (which
authorize, validate and write atomically) and present the result. Reads are
authorized here with `identity.authz`. `can` flags tell the UI which controls to
SHOW; they are advisory only, since every action is re-authorized by the service."""
from decimal import Decimal

from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.views import APIView

from coursework.academic.common import authorize_or_deny
from identity.api_base import Conflict
from identity.authz import Policy, Rule, authorize, evaluate_policy


class AcademicView(APIView):
    """Base view: authenticated login linked to an active Person (settings)."""

    @property
    def person(self):
        return self.request.person


def can(person, action, resource=None) -> bool:
    return authorize(person, action, resource).allowed


def can_policy(person, capabilities, resource=None) -> bool:
    policy = Policy(rules=tuple(Rule(c) for c in capabilities))
    return evaluate_policy(person, "academic.ui.check", policy, resource).allowed


def require_any_holder(request, actions):
    """Gate a list endpoint: the person must hold, somewhere, a capability named
    by one of `actions`' policies (each row is then filtered with `can`)."""
    from coursework.academic.common import deny
    from identity.authz import POLICIES, holds
    caps = {rule.capability for a in actions for rule in POLICIES[a].rules if rule.relationship is None}
    if not any(holds(request.person, c) for c in caps):
        deny(request.person, actions[0], "No capability for this workspace", request=request)


def require_view(request, action, resource):
    """Authorize a read; denials are audited like any other."""
    authorize_or_deny(request.person, action, resource, request=request)


def load(model, pk, **related):
    qs = model.objects.all()
    if related.get("select"):
        qs = qs.select_related(*related["select"])
    return get_object_or_404(qs, pk=pk)


def expect_status(obj, request, field="status"):
    """Optimistic concurrency: the client sends the status it saw; a mismatch is a 409."""
    expected = request.data.get("expected_status")
    if expected is not None and str(getattr(obj, field)) != str(expected):
        raise Conflict(f"This record is now {getattr(obj, field)} (you saw {expected}). Reload and try again.")


def paginate(request, items, *, default_size=25, max_size=100) -> dict:
    """Page a list/queryset: ?page=1&page_size=25."""
    try:
        size = max(1, min(int(request.query_params.get("page_size", default_size)), max_size))
        page = max(1, int(request.query_params.get("page", 1)))
    except ValueError:
        size, page = default_size, 1
    total = items.count() if hasattr(items, "count") and not isinstance(items, list) else len(items)
    start = (page - 1) * size
    return {"count": total, "page": page, "page_size": size, "results": list(items[start:start + size])}


def dec(value):
    return None if value is None else str(value) if isinstance(value, Decimal) else value


def dt(value):
    return value.isoformat() if value else None


def person_name(person):
    return person.full_name if person is not None else None


# --- presenters ---------------------------------------------------------------------------------

def semester_data(s):
    return {"id": s.pk, "name": s.name, "term": s.term, "academic_year": s.academic_year.code,
            "start_date": dt(s.start_date), "end_date": dt(s.end_date),
            "registration_opens": dt(s.registration_opens), "registration_closes": dt(s.registration_closes)}


def course_data(c):
    return {"id": c.pk, "code": c.code, "title": c.title, "credits": c.credits, "category": c.category,
            "activity_type": c.activity_type, "evaluation_body": c.evaluation_body,
            "department": c.department.name if c.department_id else None}


def faculty_names(offering, section=None):
    from django.utils import timezone
    today = timezone.localdate()
    qs = offering.faculty_assignments.filter(revoked_at__isnull=True, valid_from__lte=today).exclude(
        valid_to__lt=today).select_related("faculty")
    out = []
    for a in qs:
        if a.role == "INSTRUCTOR" and section is not None and a.section_id != section.pk:
            continue
        out.append({"name": a.faculty.name, "role": a.role, "section": a.section.code if a.section_id else None})
    return out


def offering_data(o):
    return {"id": o.pk, "course": course_data(o.course), "semester": semester_data(o.semester), "status": o.status,
            "capacity": o.capacity}


def scholar_brief(s):
    return {"id": s.pk, "prn": s.prn, "name": s.name, "department": s.department.name if s.department_id else None,
            "mode": s.mode, "status": s.status}


def mark_data(m):
    return {"id": m.pk, "assessment_id": m.assessment_id, "marks": dec(m.marks), "is_absent": m.is_absent,
            "cleared": m.cleared, "updated_at": dt(m.updated_at)}


def result_data(r, person=None, *, with_marks=False):
    a = r.attempt
    data = {
        "id": r.pk, "attempt_id": a.pk, "attempt_no": a.attempt_no, "status": r.status, "is_current": r.is_current,
        "scholar": scholar_brief(a.scholar), "course": course_data(a.course),
        "total_marks": dec(r.total_marks), "grade": r.grade, "grade_point": dec(r.grade_point),
        "outcome": r.outcome, "reasons": r.reasons, "is_provisional": r.is_provisional,
        "rule_versions": r.rule_versions, "supersedes_id": r.supersedes_id,
        "prepared_by": person_name(r.prepared_by), "prepared_as": r.prepared_as, "prepared_at": dt(r.prepared_at),
        "verified_by": person_name(r.verified_by), "verified_at": dt(r.verified_at),
        "ratified_by": person_name(r.ratified_by), "ratified_at": dt(r.ratified_at),
        "events": [{"action": e.action, "actor": person_name(e.actor), "capability": e.capability,
                    "remarks": e.remarks, "at": dt(e.at)} for e in r.events.select_related("actor").order_by("at")],
    }
    if with_marks:
        data["marks"] = [{"assessment": m.assessment.name, "kind": m.assessment.kind, "weight": dec(m.assessment.weight),
                          "max_marks": dec(m.assessment.max_marks), "marks": dec(m.marks), "is_absent": m.is_absent,
                          "cleared": m.cleared}
                         for m in a.mark_entries.select_related("assessment").order_by("assessment__sequence")]
    if person is not None:
        pending_prepare = r.status == "PREPARED"
        data["can"] = {
            "verify": pending_prepare and can(person, "academic.result.verify", r) and r.prepared_by_id != person.pk,
            "return": r.status in ("PREPARED", "VERIFIED") and can(person, "academic.result.return", r),
            "ratify": r.status == "VERIFIED" and can(person, "academic.result.ratify", r)
            and person.pk not in (r.prepared_by_id, r.verified_by_id),
        }
    return data


class ActionSerializer(serializers.Serializer):
    remarks = serializers.CharField(required=False, allow_blank=True, default="", max_length=2000)
    expected_status = serializers.CharField(required=False, allow_blank=True)


def body(serializer_class, request):
    s = serializer_class(data=request.data)
    s.is_valid(raise_exception=True)
    return s.validated_data
