"""Privileged-action audit trail (append-only, hash-chained).

Every event is also written to the `identity.security` logger, so a denial
remains on record even if a caller's enclosing transaction rolls back."""
import logging

from django.db import transaction
from django.utils import timezone

from .models import AuditEvent

GENESIS = "0" * 64
security_log = logging.getLogger("identity.security")


def _client_ip(request):
    if request is None:
        return None
    return request.META.get("REMOTE_ADDR")


@transaction.atomic
def record(*, action: str, allowed: bool, actor=None, resource=None, capability_used: str = "",
           reasons=(), before=None, after=None, request=None) -> AuditEvent:
    last = AuditEvent.objects.select_for_update().order_by("-id").first()
    event = AuditEvent(
        actor=actor,
        actor_user=getattr(request, "user", None) if request and request.user.is_authenticated
        else (actor.user if actor is not None else None),
        capability_used=capability_used or "",
        action=action,
        resource_type=resource._meta.label if resource is not None else "",
        resource_id=str(resource.pk) if resource is not None else "",
        allowed=allowed,
        reasons=list(reasons),
        before=before,
        after=after,
        ip_address=_client_ip(request),
        request_id=(request.META.get("HTTP_X_REQUEST_ID", "") if request else ""),
        created_at=timezone.now(),
        prev_hash=last.hash if last else GENESIS,
    )
    event.hash = event.compute_hash()
    event.save()
    security_log.log(logging.INFO if allowed else logging.WARNING,
                     "audit action=%s allowed=%s actor=%s resource=%s:%s reasons=%s",
                     action, allowed, getattr(actor, "pk", None), event.resource_type, event.resource_id,
                     list(reasons))
    return event


def verify_chain() -> tuple[bool, int | None]:
    """(ok, id of the first broken event). Detects edits made outside the ORM."""
    prev = GENESIS
    for event in AuditEvent.objects.order_by("id").iterator():
        if event.prev_hash != prev or event.compute_hash() != event.hash:
            return False, event.id
        prev = event.hash
    return True, None
