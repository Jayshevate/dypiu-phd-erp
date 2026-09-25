from django.http import JsonResponse
from django.views.decorators.http import require_GET

from .authz import capability_summary, person_of, workspaces_of


@require_GET
def me(request):
    """Server-computed identity, capabilities, scopes and workspaces.
    The frontend may use this only to decide what to SHOW; every action is
    re-authorised on the server."""
    if not request.user.is_authenticated:
        return JsonResponse({"detail": "Authentication required"}, status=401)
    person = person_of(request.user)
    if person is None:
        return JsonResponse({"detail": "No active institutional identity is linked to this login"}, status=403)
    return JsonResponse({
        "person": {"id": person.pk, "full_name": person.full_name, "email": person.email},
        "capabilities": capability_summary(person),
        "workspaces": workspaces_of(person),
    })
