from django.http import JsonResponse
from django.views.decorators.http import require_GET

from .authz import person_of


@require_GET
def me(request):
    """Server-computed identity, capabilities, scopes, relationships and
    workspaces. The frontend may use this only to decide what to SHOW; every
    action is re-authorised on the server."""
    from .api import identity_payload

    if not request.user.is_authenticated:
        return JsonResponse({"code": "not_authenticated", "detail": "Authentication required"}, status=401)
    person = person_of(request.user)
    if person is None:
        return JsonResponse({"code": "no_identity",
                             "detail": "No active institutional identity is linked to this login"}, status=403)
    return JsonResponse(identity_payload(person))
