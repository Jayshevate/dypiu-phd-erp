"""Privileged identity operations. Every function authorises the actor
server-side and writes an audit event, whether it is allowed or denied.
There is no other supported way to create persons or capability grants
(the Django admin is read-only for these models).

Authorization checks, and the audit records of denials, run BEFORE the write
transaction opens, so a denial is never rolled back with the refused write.
Callers must not wrap these functions in their own transaction if they need
the denial record to persist (the security log keeps a copy regardless)."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from . import audit
from .authz import ResourceScope, authorize, covers, effective_capabilities, holds, require
from .capabilities import DERIVED_CAPABILITIES, GRANTABLE_BY, Capability, ScopeType
from .models import CapabilityAssignment, Person


def _snapshot(obj, fields):
    return {f: (str(getattr(obj, f)) if getattr(obj, f) is not None else None) for f in fields}


def _deny(action, actor, reasons, resource=None, request=None):
    audit.record(action=action, allowed=False, actor=actor, resource=resource, reasons=reasons, request=request)
    raise PermissionDenied("; ".join(reasons))


def _protect_system_admin(actor, target: Person, action, request):
    """Only a SYSTEM_ADMIN may alter a person who is a SYSTEM_ADMIN (prevents
    account takeover of the most privileged identities)."""
    if holds(target, Capability.SYSTEM_ADMIN) and not holds(actor, Capability.SYSTEM_ADMIN):
        _deny(action, actor, ["only a SYSTEM_ADMIN may modify a SYSTEM_ADMIN identity"], target, request)


# --- Persons & profiles ---------------------------------------------------------------

def provision_person(actor, *, full_name, email, user=None, request=None) -> Person:
    require(actor, "identity.person.manage", None, request=request)
    person = Person(full_name=full_name, email=email.strip().lower(), user=user)
    person.full_clean()
    with transaction.atomic():
        person.save()
        audit.record(action="identity.person.create", allowed=True, actor=actor, resource=person,
                     request=request, after=_snapshot(person, ["full_name", "email", "user_id"]))
    return person


def link_user(actor, person: Person, user, *, request=None) -> Person:
    require(actor, "identity.person.manage", None, request=request)
    _protect_system_admin(actor, person, "identity.person.link_user", request)
    if person.user_id and person.user_id != user.pk:
        raise ValidationError("Person already has a login; account re-linking needs a separate verified process")
    if Person.objects.filter(user=user).exclude(pk=person.pk).exists():
        raise ValidationError("This login is already linked to another person")
    person.user = user
    with transaction.atomic():
        person.save(update_fields=["user"])
        audit.record(action="identity.person.link_user", allowed=True, actor=actor, resource=person,
                     request=request, after={"user_id": user.pk})
    return person


def _check_profile_email(person, profile):
    email = (getattr(profile, "email", "") or "").strip().lower()
    if email and email != person.email:
        raise ValidationError(f"Profile email {email} does not match person email {person.email}")


def link_faculty_profile(actor, person: Person, faculty, *, request=None) -> Person:
    require(actor, "identity.person.manage", None, request=request)
    _protect_system_admin(actor, person, "identity.person.link_faculty", request)
    _check_profile_email(person, faculty)
    if person.faculty_profile_id and person.faculty_profile_id != faculty.pk:
        raise ValidationError("Person already has a different faculty profile")
    person.faculty_profile = faculty
    person.full_clean()
    with transaction.atomic():
        person.save(update_fields=["faculty_profile"])
        audit.record(action="identity.person.link_faculty", allowed=True, actor=actor, resource=person,
                     request=request, after={"faculty_id": faculty.pk})
    return person


def link_scholar_profile(actor, person: Person, scholar, *, request=None) -> Person:
    require(actor, "identity.person.manage", None, request=request)
    _protect_system_admin(actor, person, "identity.person.link_scholar", request)
    _check_profile_email(person, scholar)
    if person.scholar_profile_id and person.scholar_profile_id != scholar.pk:
        raise ValidationError("Person already has a different scholar profile")
    person.scholar_profile = scholar
    person.full_clean()
    with transaction.atomic():
        person.save(update_fields=["scholar_profile"])
        audit.record(action="identity.person.link_scholar", allowed=True, actor=actor, resource=person,
                     request=request, after={"scholar_id": scholar.pk})
    return person


def deactivate_person(actor, person: Person, *, reason: str, request=None) -> Person:
    require(actor, "identity.person.manage", None, request=request)
    if actor is not None and actor.pk == person.pk:
        _deny("identity.person.deactivate", actor, ["cannot deactivate your own identity"], person, request)
    _protect_system_admin(actor, person, "identity.person.deactivate", request)
    if holds(person, Capability.SYSTEM_ADMIN) and _active_system_admin_count(exclude=person) == 0:
        _deny("identity.person.deactivate", actor, ["cannot deactivate the last SYSTEM_ADMIN"], person, request)
    person.is_active = False
    with transaction.atomic():
        person.save(update_fields=["is_active"])
        audit.record(action="identity.person.deactivate", allowed=True, actor=actor, resource=person,
                     request=request, reasons=[reason], after={"is_active": False})
    return person


# --- Capability grants --------------------------------------------------------------

def _target_scope(scope_type, school=None, department=None, scholar=None) -> ResourceScope:
    if scope_type == ScopeType.INSTITUTION:
        return ResourceScope(institution_only=True)
    if scope_type == ScopeType.SCHOOL:
        return ResourceScope(school_id=school.pk)
    if scope_type == ScopeType.DEPARTMENT:
        return ResourceScope(school_id=department.school_id, department_id=department.pk)
    if scope_type == ScopeType.SCHOLAR:
        return ResourceScope(school_id=scholar.department.school_id, department_id=scholar.department_id,
                             scholar_id=scholar.pk)
    raise ValidationError(f"Unsupported scope type {scope_type}")


def _granting_authority(actor, capability, target: ResourceScope) -> tuple[bool, list, str]:
    if actor is None or not actor.is_active:
        return False, ["no active institutional identity"], ""
    allowed_by = GRANTABLE_BY.get(capability, set())
    for cap in effective_capabilities(actor):
        if cap.capability in allowed_by and covers(cap, target):
            return True, [], cap.capability
    return False, [f"{capability} may be granted only by {sorted(str(c) for c in allowed_by)} with a covering scope"], ""


def _active_system_admin_count(exclude=None) -> int:
    today = timezone.localdate()
    qs = CapabilityAssignment.objects.filter(capability=Capability.SYSTEM_ADMIN, revoked_at__isnull=True,
                                             valid_from__lte=today, person__is_active=True)
    qs = qs.exclude(valid_to__lt=today)
    if exclude is not None:
        qs = qs.exclude(person=exclude)
    return qs.values("person").distinct().count()


def grant_capability(actor, person: Person, capability: str, scope_type: str, *, basis: str,
                     school=None, department=None, scholar=None, valid_from=None, valid_to=None,
                     request=None) -> CapabilityAssignment:
    action = "identity.capability.grant"
    if capability in DERIVED_CAPABILITIES:
        _deny(action, actor, [f"{capability} is derived and cannot be granted"], person, request)
    if actor is not None and actor.pk == person.pk:
        _deny(action, actor, ["separation of duties: you cannot grant a capability to yourself"], person, request)
    if not basis.strip():
        raise ValidationError("A basis (order number / approval reference) is required")

    target = _target_scope(scope_type, school, department, scholar)
    ok, reasons, used = _granting_authority(actor, capability, target)
    if not ok:
        _deny(action, actor, reasons, person, request)

    grant = CapabilityAssignment(person=person, capability=capability, scope_type=scope_type, school=school,
                                 department=department, scholar=scholar,
                                 valid_from=valid_from or timezone.localdate(), valid_to=valid_to,
                                 basis=basis, granted_by=actor)
    grant.full_clean()
    with transaction.atomic():
        grant.save()
        audit.record(action=action, allowed=True, actor=actor, resource=grant, capability_used=used,
                     request=request,
                     after={"person_id": person.pk, "capability": capability, "scope": grant.scope_label,
                            "valid_from": str(grant.valid_from), "valid_to": str(grant.valid_to), "basis": basis})
    return grant


def revoke_capability(actor, grant: CapabilityAssignment, *, reason: str, request=None) -> CapabilityAssignment:
    action = "identity.capability.revoke"
    if grant.revoked_at is not None:
        raise ValidationError("Grant is already revoked")
    target = _target_scope(grant.scope_type, grant.school, grant.department, grant.scholar)
    ok, reasons, used = _granting_authority(actor, grant.capability, target)
    if not ok:
        _deny(action, actor, reasons, grant, request)
    if grant.capability == Capability.SYSTEM_ADMIN and _active_system_admin_count(exclude=grant.person) == 0:
        _deny(action, actor, ["cannot revoke the last SYSTEM_ADMIN"], grant, request)
    with transaction.atomic():
        grant = CapabilityAssignment.objects.select_for_update().get(pk=grant.pk)
        if grant.revoked_at is not None:
            raise ValidationError("Grant is already revoked")
        grant.revoked_at = timezone.now()
        grant.revoked_by = actor
        grant.revocation_reason = reason
        grant.save(update_fields=["revoked_at", "revoked_by", "revocation_reason"])
        audit.record(action=action, allowed=True, actor=actor, resource=grant, capability_used=used,
                     request=request, reasons=[reason], after={"revoked_at": str(grant.revoked_at)})
    return grant


# --- Bootstrap (server-side replacement for the client "seed admin email") -----------

def bootstrap_system_admin(*, full_name: str, email: str, user=None, basis: str) -> CapabilityAssignment:
    """Create the FIRST SYSTEM_ADMIN. Refused once any active SYSTEM_ADMIN exists.
    Run only from the server command line (management command)."""
    if _active_system_admin_count() > 0:
        audit.record(action="identity.bootstrap", allowed=False, reasons=["a SYSTEM_ADMIN already exists"])
        raise PermissionDenied("A SYSTEM_ADMIN already exists; use grant_capability")
    email = email.strip().lower()
    with transaction.atomic():
        person = Person.objects.filter(email__iexact=email).first()
        if person is None:
            person = Person(full_name=full_name, email=email, user=user)
            person.full_clean()
            person.save()
        grant = CapabilityAssignment(person=person, capability=Capability.SYSTEM_ADMIN,
                                     scope_type=ScopeType.INSTITUTION, valid_from=timezone.localdate(),
                                     basis=basis, granted_by=None)
        grant.full_clean()
        grant.save()
        audit.record(action="identity.bootstrap", allowed=True, resource=grant, capability_used="",
                     after={"person_id": person.pk, "email": email, "basis": basis})
    return grant


def check(actor, action, resource=None):
    """Convenience for callers that only need a boolean."""
    return authorize(actor, action, resource).allowed
