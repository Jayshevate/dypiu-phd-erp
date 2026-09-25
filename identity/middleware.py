from django.utils.functional import SimpleLazyObject

from .authz import person_of


class PersonMiddleware:
    """Attach `request.person`: the active Person linked to the authenticated
    login, or None. Authorization code must use request.person, never
    user.is_superuser / groups / client-supplied identifiers."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.person = SimpleLazyObject(lambda: person_of(getattr(request, "user", None)))
        return self.get_response(request)
