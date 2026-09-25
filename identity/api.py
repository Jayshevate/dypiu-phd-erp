"""REST foundation shared by every API (Step 5C).

* Authentication: Django session (CSRF enforced by DRF SessionAuthentication).
  The identity provider (OIDC/SAML, decision D-IdP) is not yet connected, so
  `login` accepts Django credentials; it creates no second identity — the
  login must already be linked to an active `identity.Person`.
* Permission: every endpoint needs an authenticated login linked to an active
  Person (`HasActivePerson`). Each view then re-authorizes the specific action
  with `identity.authz` / the domain services. Nothing the client sends
  (role, scholar id, capability) is trusted as identity.
* Errors: one JSON shape `{"code", "detail", "errors"?}`; never a traceback.
"""
import logging

from django.contrib.auth import authenticate, login, logout
from django.core.exceptions import ValidationError as DjangoValidationError
from django.middleware.csrf import get_token
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .api_base import Conflict, HasActivePerson, exception_handler  # noqa: F401  (re-exported)
from .authz import capability_summary, person_of, workspaces_of

log = logging.getLogger("identity.security")


# --- identity ------------------------------------------------------------------------------------

def relationships_of(person) -> dict:
    """Organisational scope and relationships the server derived for this person."""
    from django.utils import timezone

    from core.models import CommitteeMembership
    from supervision.models import SupervisorAssignment

    out = {"scholar_id": person.scholar_profile_id, "faculty_id": person.faculty_profile_id,
           "department_id": None, "school_id": None, "committees": [], "supervises_scholar_ids": [],
           "teaching": []}
    profile = person.scholar_profile or person.faculty_profile
    dept = getattr(profile, "department", None)
    if dept is not None:
        out["department_id"], out["school_id"] = dept.pk, dept.school_id
    if person.faculty_profile_id:
        from coursework.models import FacultySubjectAssignment
        today = timezone.localdate()
        out["committees"] = [{"id": m.committee_id, "type": m.committee.type, "name": m.committee.name,
                              "school_id": m.committee.school_id}
                             for m in CommitteeMembership.objects.filter(faculty_id=person.faculty_profile_id)
                             .select_related("committee")]
        out["supervises_scholar_ids"] = list(SupervisorAssignment.objects.filter(
            faculty_id=person.faculty_profile_id, end_date__isnull=True, approved_on__isnull=False)
            .values_list("scholar_id", flat=True))
        out["teaching"] = [{"assignment_id": a.pk, "offering_id": a.offering_id, "section_id": a.section_id,
                            "role": a.role}
                           for a in FacultySubjectAssignment.objects.filter(
                               faculty_id=person.faculty_profile_id, revoked_at__isnull=True,
                               valid_from__lte=today).exclude(valid_to__lt=today)]
    return out


def identity_payload(person) -> dict:
    return {
        "person": {"id": person.pk, "full_name": person.full_name, "email": person.email},
        "capabilities": capability_summary(person),
        "workspaces": workspaces_of(person),
        "scope": relationships_of(person),
    }


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfView(APIView):
    """Sets the CSRF cookie so the SPA can send X-CSRFToken on mutations."""
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        return Response({"csrf_token": get_token(request)})


@method_decorator(csrf_protect, name="dispatch")
class LoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        username = str(request.data.get("username", "")).strip()
        password = str(request.data.get("password", ""))
        if not username or not password:
            raise DjangoValidationError("Username and password are required")
        user = authenticate(request, username=username, password=password)
        person = person_of(user) if user is not None else None
        if user is None or person is None:
            log.warning("login failed for %r", username)
            return Response({"code": "not_authenticated",
                             "detail": "Sign-in failed: wrong credentials or no active institutional identity."},
                            status=401)
        login(request, user)
        return Response(identity_payload(person))


class LogoutView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        logout(request)
        return Response(status=204)
