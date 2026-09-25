"""DRF foundation (no DRF views imported here, so settings can reference it):
the active-Person permission, the Conflict exception and the error handler."""
import logging

from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.permissions import BasePermission
from rest_framework.response import Response

from .authz import person_of

log = logging.getLogger("identity.security")


class Conflict(exceptions.APIException):
    """The record changed since the client last read it (stale data)."""
    status_code = status.HTTP_409_CONFLICT
    default_code = "conflict"
    default_detail = "This record was changed by someone else. Reload and try again."


class HasActivePerson(BasePermission):
    message = "No active institutional identity is linked to this login"

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            raise exceptions.NotAuthenticated()
        request.person = person_of(request.user)
        return request.person is not None


def _messages(exc: DjangoValidationError) -> list[str]:
    if hasattr(exc, "message_dict"):
        return [f"{k}: {m}" if k != "__all__" else m for k, msgs in exc.message_dict.items() for m in msgs]
    return list(exc.messages)


def exception_handler(exc, context):
    """Map domain exceptions to HTTP; hide internals."""
    if isinstance(exc, exceptions.NotAuthenticated) or isinstance(exc, exceptions.AuthenticationFailed):
        return Response({"code": "not_authenticated", "detail": "Your session has expired or you are not signed in."},
                        status=401)
    if isinstance(exc, (PermissionDenied, exceptions.PermissionDenied)):
        detail = str(exc.detail) if isinstance(exc, exceptions.PermissionDenied) else str(exc)
        return Response({"code": "permission_denied", "detail": detail or "You are not permitted to do this."},
                        status=403)
    if isinstance(exc, DjangoValidationError):
        errors = _messages(exc)
        return Response({"code": "validation_error", "detail": "; ".join(errors), "errors": errors}, status=400)
    if isinstance(exc, exceptions.ValidationError):
        detail = exc.detail
        if isinstance(detail, dict):
            errors = [f"{k}: {' '.join(map(str, v)) if isinstance(v, list) else v}" for k, v in detail.items()]
        else:
            errors = [str(d) for d in (detail if isinstance(detail, list) else [detail])]
        return Response({"code": "validation_error", "detail": "; ".join(errors), "errors": errors}, status=400)
    if isinstance(exc, (Http404, ObjectDoesNotExist, exceptions.NotFound)):
        return Response({"code": "not_found", "detail": "Not found."}, status=404)
    if isinstance(exc, IntegrityError):
        return Response({"code": "conflict", "detail": "The change conflicts with the current records."},
                        status=409)
    if isinstance(exc, exceptions.APIException):
        return Response({"code": exc.default_code if hasattr(exc, "default_code") else "error",
                         "detail": str(exc.detail)}, status=exc.status_code)
    log.exception("Unhandled API error in %s", context.get("view").__class__.__name__)
    return Response({"code": "server_error", "detail": "An unexpected error occurred. It has been logged."},
                    status=500)


def csrf_failure(request, reason=""):
    """JSON (not an HTML page) when the CSRF check fails, so API clients can
    show a clear message. Never echoes internals."""
    from django.http import JsonResponse
    log.warning("csrf failure on %s: %s", request.path, reason)
    return JsonResponse({"code": "csrf_failed",
                         "detail": "Security check failed. Reload the page and try again."}, status=403)
