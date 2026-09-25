"""Authorization and audit helpers shared by academic services.

Pattern for every mutation:
  1. authorize_or_deny(...) BEFORE any transaction (denials are audited and never rolled back)
  2. validate
  3. with transaction.atomic(): write + audit_ok(...)
"""
from django.core.exceptions import PermissionDenied

from identity import audit
from identity.authz import Decision, Policy, authorize, evaluate_policy


def _resource_for_audit(resource):
    return resource if hasattr(resource, "_meta") else None


def authorize_or_deny(actor, actions, resource=None, *, request=None) -> Decision:
    """Allow if ANY of `actions` is allowed. Otherwise audit the denial and raise."""
    actions = [actions] if isinstance(actions, str) else list(actions)
    reasons = []
    for action in actions:
        decision = authorize(actor, action, resource)
        if decision.allowed:
            return decision
        reasons.extend(decision.reasons)
    audit.record(action=actions[0], allowed=False, actor=actor, resource=_resource_for_audit(resource),
                 reasons=reasons, request=request)
    raise PermissionDenied("; ".join(reasons))


def authorize_policy_or_deny(actor, action: str, policy: Policy, resource=None, *, request=None) -> Decision:
    decision = evaluate_policy(actor, action, policy, resource)
    if not decision.allowed:
        audit.record(action=action, allowed=False, actor=actor, resource=_resource_for_audit(resource),
                     reasons=decision.reasons, request=request)
        raise PermissionDenied("; ".join(decision.reasons))
    return decision


def deny(actor, action, reason, resource=None, *, request=None):
    """Business-rule denial that is authorization-relevant (e.g. separation of duties)."""
    audit.record(action=action, allowed=False, actor=actor, resource=_resource_for_audit(resource),
                 reasons=[reason], request=request)
    raise PermissionDenied(reason)


def audit_ok(actor, action, resource, decision: Decision, *, before=None, after=None, request=None, reasons=()):
    return audit.record(action=action, allowed=True, actor=actor, resource=_resource_for_audit(resource),
                        capability_used=decision.capability, before=before, after=after, reasons=list(reasons),
                        request=request)


def coursework_category(scholar, ctx) -> str:
    """Scholar's coursework category (I/II/III) from the configured mapping (RD-43)."""
    mapping = ctx("coursework.category_of_entry_qualification").value
    category = mapping.get(scholar.entry_qualification)
    if category is None:
        from django.core.exceptions import ValidationError
        raise ValidationError(f"No coursework category configured for {scholar.entry_qualification!r} (RD-43)")
    return category
