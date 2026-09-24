from datetime import date

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, render

from core.roles import Role, user_has_role

from .models import Scholar

# Roles that may see every scholar.
OVERSIGHT_ROLES = (Role.DC, Role.SDRC, Role.DEAN_RD, Role.VC, Role.COE, Role.REGISTRAR, Role.RD_OFFICE)


def visible_scholars(user):
    if any(user_has_role(user, r) for r in OVERSIGHT_ROLES):
        return Scholar.objects.all()
    faculty = getattr(user, "faculty", None)
    qs = Scholar.objects.none()
    if faculty:
        qs = Scholar.objects.filter(supervisor_assignments__faculty=faculty, supervisor_assignments__end_date__isnull=True) | \
             Scholar.objects.filter(tac_memberships__faculty=faculty, tac_memberships__end_date__isnull=True)
    if hasattr(user, "scholar"):
        qs = qs | Scholar.objects.filter(pk=user.scholar.pk)
    return qs.distinct()


@login_required
def dashboard(request):
    scholars = visible_scholars(request.user).select_related("department")
    if hasattr(request.user, "scholar") and scholars.count() == 1:
        return scholar_detail(request, request.user.scholar.prn)
    today = date.today()
    from deadlines.models import Deadline
    overdue = (Deadline.objects.filter(scholar__in=scholars, met=False, due_date__lt=today)
               .select_related("scholar").order_by("due_date")[:50])
    upcoming = (Deadline.objects.filter(scholar__in=scholars, met=False, due_date__gte=today)
                .select_related("scholar").order_by("due_date")[:50])
    return render(request, "scholars/dashboard.html",
                  {"scholars": scholars, "overdue": overdue, "upcoming": upcoming, "today": today})


@login_required
def scholar_detail(request, prn):
    scholar = get_object_or_404(Scholar, prn=prn)
    if not visible_scholars(request.user).filter(pk=scholar.pk).exists():
        raise PermissionDenied
    from lifecycle.engine import evaluate_gate
    today = date.today()
    return render(request, "scholars/detail.html", {
        "scholar": scholar,
        "gate": evaluate_gate(scholar, today),
        "deadlines": [(d, d.state(today)) for d in scholar.deadlines.all()],
        "approvals": scholar.approval_requests.select_related("chain")[:20],
        "transitions": scholar.transitions.all(),
        "today": today,
    })
