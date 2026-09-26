"""Server-side authorization.

    authorize(person, action, resource) -> Decision

Deny by default. An action is allowed when at least one of its policy rules
matches an EFFECTIVE capability of the person:

  * the capability is held (explicit grant, or derived from a profile /
    committee membership) and is currently valid;
  * scope: the capability's scope covers the resource, OR
  * relationship: for relationship rules (self, supervises, TAC member) the
    person has that relationship with the resource instead of a scope.

Django `is_superuser`, `is_staff` and auth Groups grant NOTHING here.
"""
from dataclasses import dataclass
from typing import Optional

from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.utils import timezone

from .capabilities import WORKSPACES, Capability, ScopeType

C = Capability


# --- Effective capabilities ------------------------------------------------------------

@dataclass(frozen=True)
class EffectiveCapability:
    capability: str
    scope_type: str
    school_id: Optional[int] = None
    department_id: Optional[int] = None
    committee_id: Optional[int] = None
    committee_school_id: Optional[int] = None
    scholar_id: Optional[int] = None
    source: str = ""


def person_of(user):
    """The active Person linked to a login, or None."""
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    person = getattr(user, "person", None)
    return person if person is not None and person.is_active else None


def effective_capabilities(person, on=None) -> list[EffectiveCapability]:
    if person is None or not person.is_active:
        return []
    on = on or timezone.localdate()
    caps: list[EffectiveCapability] = []

    if person.scholar_profile_id:
        caps.append(EffectiveCapability(C.SCHOLAR, ScopeType.SCHOLAR, scholar_id=person.scholar_profile_id,
                                        source="profile:scholar"))

    faculty = person.faculty_profile
    if faculty is not None:
        if not faculty.is_external and faculty.department_id:
            caps.append(EffectiveCapability(C.FACULTY, ScopeType.DEPARTMENT, department_id=faculty.department_id,
                                            source="profile:faculty"))
        from core.models import CommitteeMembership, CommitteeType
        memberships = CommitteeMembership.objects.filter(
            faculty=faculty, committee__type__in=[CommitteeType.DC, CommitteeType.SDRC],
        ).filter(Q(committee__tenure_start__isnull=True) | Q(committee__tenure_start__lte=on),
                 Q(committee__tenure_end__isnull=True) | Q(committee__tenure_end__gte=on)).select_related("committee")
        for m in memberships:
            cap = C.DC_MEMBER if m.committee.type == CommitteeType.DC else C.SDRC_MEMBER
            caps.append(EffectiveCapability(cap, ScopeType.COMMITTEE, committee_id=m.committee_id,
                                            committee_school_id=m.committee.school_id,
                                            source=f"committee:{m.committee_id}"))

    for a in person.capability_assignments.filter(revoked_at__isnull=True, valid_from__lte=on).filter(
            Q(valid_to__isnull=True) | Q(valid_to__gte=on)):
        caps.append(EffectiveCapability(a.capability, a.scope_type, school_id=a.school_id,
                                        department_id=a.department_id, scholar_id=a.scholar_id,
                                        source=f"grant:{a.pk}"))
    return caps


def workspaces_of(person) -> list[dict]:
    seen, out = set(), []
    for cap in effective_capabilities(person):
        key, path = WORKSPACES[cap.capability]
        if key not in seen:
            seen.add(key)
            out.append({"key": key, "path": path, "capability": cap.capability})
    return out


# --- Resource scope ----------------------------------------------------------------------

@dataclass(frozen=True)
class ResourceScope:
    school_id: Optional[int] = None
    department_id: Optional[int] = None
    scholar_id: Optional[int] = None
    institution_only: bool = False     # resource with no school/department (e.g. an institutional grant)


def _scholar_of(resource):
    """The scholar a resource belongs to: the Scholar itself, or any record
    exposing `owner_scholar` (enrollments, attempts, results, cases, ...)."""
    from scholars.models import Scholar

    if isinstance(resource, Scholar):
        return resource
    return getattr(resource, "owner_scholar", None)


def resource_scope(resource) -> ResourceScope:
    """Duck-typed so other bounded contexts need not register with identity:
    records expose `owner_scholar` (scholar-owned) or `scope_department`
    (organisation-owned; None means institution-level)."""
    from core.models import Department, Faculty, School
    from scholars.models import Scholar

    if resource is None:
        return ResourceScope(institution_only=True)
    if isinstance(resource, ResourceScope):
        return resource
    if not isinstance(resource, Scholar) and hasattr(resource, "owner_scholar"):
        owner = resource.owner_scholar
        return resource_scope(owner) if owner is not None else ResourceScope(institution_only=True)
    if hasattr(resource, "scope_department"):
        dept = resource.scope_department
        if dept is None:
            return ResourceScope(institution_only=True)
        return ResourceScope(school_id=dept.school_id, department_id=dept.pk)
    if isinstance(resource, Scholar):
        dept = resource.department
        return ResourceScope(school_id=dept.school_id, department_id=dept.pk, scholar_id=resource.pk)
    if isinstance(resource, Faculty):
        dept = resource.department
        return ResourceScope(school_id=dept.school_id if dept else None, department_id=resource.department_id,
                             institution_only=dept is None)
    if isinstance(resource, Department):
        return ResourceScope(school_id=resource.school_id, department_id=resource.pk)
    if isinstance(resource, School):
        return ResourceScope(school_id=resource.pk)
    raise TypeError(f"No scope resolver for {type(resource).__name__}")


def covers(cap: EffectiveCapability, target: ResourceScope) -> bool:
    if cap.scope_type == ScopeType.INSTITUTION:
        return True
    if target.institution_only:
        return False
    if cap.scope_type == ScopeType.SCHOOL:
        return target.school_id is not None and cap.school_id == target.school_id
    if cap.scope_type == ScopeType.DEPARTMENT:
        return target.department_id is not None and cap.department_id == target.department_id
    if cap.scope_type == ScopeType.COMMITTEE:
        # An institution-level committee (no school) covers all schools.
        return cap.committee_school_id is None or cap.committee_school_id == target.school_id
    if cap.scope_type == ScopeType.SCHOLAR:
        return target.scholar_id is not None and cap.scholar_id == target.scholar_id
    return False


# --- Relationships ---------------------------------------------------------------------

def _supervises(person, resource) -> bool:
    from supervision.models import SupervisorAssignment
    scholar = _scholar_of(resource)
    return scholar is not None and bool(person.faculty_profile_id) and SupervisorAssignment.objects.filter(
        scholar=scholar, faculty_id=person.faculty_profile_id, end_date__isnull=True, approved_on__isnull=False,
    ).exists()


def _tac_member(person, resource) -> bool:
    from supervision.models import TACMembership
    scholar = _scholar_of(resource)
    return scholar is not None and bool(person.faculty_profile_id) and TACMembership.objects.filter(
        scholar=scholar, faculty_id=person.faculty_profile_id, end_date__isnull=True, approved_on__isnull=False,
    ).exists()


def _self(person, resource) -> bool:
    scholar = _scholar_of(resource)
    return scholar is not None and person.scholar_profile_id is not None and person.scholar_profile_id == scholar.pk


RELATIONSHIPS = {"self": _self, "supervises": _supervises, "tac_member": _tac_member}


def register_relationship(name: str, check) -> None:
    """Let a bounded context add a relationship check `check(person, resource) -> bool`."""
    if name in RELATIONSHIPS and RELATIONSHIPS[name] is not check:
        raise ValueError(f"relationship {name!r} already registered")
    RELATIONSHIPS[name] = check


def register_policies(policies: dict) -> None:
    for action, policy in policies.items():
        if action in POLICIES and POLICIES[action] != policy:
            raise ValueError(f"policy {action!r} already registered")
        POLICIES[action] = policy


# --- Policies --------------------------------------------------------------------------

@dataclass(frozen=True)
class Rule:
    capability: str
    relationship: Optional[str] = None   # when set, the relationship replaces the scope check


@dataclass(frozen=True)
class Policy:
    rules: tuple
    privileged: bool = False             # audit allowed AND denied attempts
    description: str = ""


# PROVISIONAL (RD-38): the approval authorities behind these actions are
# pending DYPIU confirmation. Relationship rules are institutional facts
# (a supervisor acts only for their own scholars) and are not provisional.
OVERSIGHT = (C.PHD_CELL_OPERATOR, C.ACADEMIC_ADMIN, C.DEAN_RND, C.COE_OPERATOR, C.VC_OPERATOR)

POLICIES: dict[str, Policy] = {
    "scholar.view": Policy(rules=(
        Rule(C.SCHOLAR, "self"),
        Rule(C.SUPERVISOR, "supervises"),
        Rule(C.FACULTY, "tac_member"),
        Rule(C.EXAM_EVALUATOR),                  # scoped to a single scholar
        Rule(C.DEPARTMENT_ADMIN), Rule(C.SCHOOL_ADMIN), Rule(C.DC_MEMBER), Rule(C.SDRC_MEMBER),
        *(Rule(c) for c in OVERSIGHT),
    ), description="Read a scholar's record"),
    "scholar.edit_record": Policy(rules=(Rule(C.PHD_CELL_OPERATOR), Rule(C.ACADEMIC_ADMIN)),
                                  privileged=True, description="Edit a scholar's administrative record"),
    "research.supervisee.act": Policy(rules=(Rule(C.SUPERVISOR, "supervises"),),
                                      description="Act as the scholar's supervisor (endorse, forward)"),
    "supervision.assign": Policy(rules=(Rule(C.PHD_CELL_OPERATOR), Rule(C.DEPARTMENT_ADMIN)),
                                 privileged=True, description="Propose a supervisor allocation"),
    "identity.person.manage": Policy(rules=(Rule(C.SYSTEM_ADMIN), Rule(C.ACADEMIC_ADMIN)), privileged=True,
                                     description="Create persons and link profiles"),
    "audit.view": Policy(rules=(Rule(C.SYSTEM_ADMIN),), privileged=True, description="Read the audit trail"),
    # PROVISIONAL (RD-38): no regulation names who creates the institutional structure or faculty
    # records. ACADEMIC_ADMIN holds both until DYPIU confirms (decision recorded in Step A2).
    "institution.structure.manage": Policy(rules=(Rule(C.ACADEMIC_ADMIN),), privileged=True,
                                           description="Create universities, schools and departments"),
    "faculty.record.manage": Policy(rules=(Rule(C.ACADEMIC_ADMIN),), privileged=True,
                                    description="Create faculty records"),
}


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reasons: tuple = ()
    capability: str = ""
    source: str = ""

    def __bool__(self):
        return self.allowed


def authorize(person, action: str, resource=None, *, on=None) -> Decision:
    policy = POLICIES.get(action)
    if policy is None:
        return Decision(False, (f"unknown action '{action}'",))
    return evaluate_policy(person, action, policy, resource, on=on)


def evaluate_policy(person, action: str, policy: Policy, resource=None, *, on=None) -> Decision:
    """Evaluate an explicit policy (used for rules whose authority is configured
    at runtime, e.g. a regulatory parameter naming the approving capability)."""
    if person is None:
        return Decision(False, ("no institutional identity",))
    if not person.is_active:
        return Decision(False, ("identity is inactive",))

    caps = effective_capabilities(person, on)
    if not caps:
        return Decision(False, ("no effective capabilities",))
    target = resource_scope(resource)

    for rule in policy.rules:
        for cap in caps:
            if cap.capability != rule.capability:
                continue
            if rule.relationship:
                check = RELATIONSHIPS[rule.relationship]
                if resource is not None and check(person, resource):
                    return Decision(True, (), cap.capability, cap.source)
            elif covers(cap, target):
                return Decision(True, (), cap.capability, cap.source)

    held = sorted({str(c.capability) for c in caps})
    return Decision(False, (f"none of the capabilities {held} permit '{action}' on this resource",))


def require(person, action: str, resource=None, *, request=None) -> Decision:
    """authorize() + raise PermissionDenied. Denials are always audited; allowed
    attempts are audited for privileged actions."""
    from . import audit

    decision = authorize(person, action, resource)
    policy = POLICIES.get(action)
    if not decision.allowed or (policy and policy.privileged):
        audit.record(action=action, allowed=decision.allowed, actor=person,
                     resource=resource if hasattr(resource, "_meta") else None,
                     capability_used=decision.capability, reasons=decision.reasons, request=request)
    if not decision.allowed:
        raise PermissionDenied("; ".join(decision.reasons))
    return decision


# --- Queryset scoping ------------------------------------------------------------------

def visible_scholars(person, on=None):
    """Scholars `person` may view. Must agree with authorize(person, 'scholar.view', s)."""
    from scholars.models import Scholar
    from supervision.models import SupervisorAssignment, TACMembership

    none = Scholar.objects.none()
    if person is None or not person.is_active:
        return none
    q = Q(pk__in=[])
    for cap in effective_capabilities(person, on):
        rules = [r for r in POLICIES["scholar.view"].rules if r.capability == cap.capability]
        for rule in rules:
            if rule.relationship == "self":
                q |= Q(pk=person.scholar_profile_id)
            elif rule.relationship == "supervises":
                q |= Q(pk__in=SupervisorAssignment.objects.filter(
                    faculty_id=person.faculty_profile_id, end_date__isnull=True, approved_on__isnull=False,
                ).values("scholar_id"))
            elif rule.relationship == "tac_member":
                q |= Q(pk__in=TACMembership.objects.filter(
                    faculty_id=person.faculty_profile_id, end_date__isnull=True, approved_on__isnull=False,
                ).values("scholar_id"))
            elif cap.scope_type == ScopeType.INSTITUTION:
                return Scholar.objects.all()
            elif cap.scope_type == ScopeType.SCHOOL:
                q |= Q(department__school_id=cap.school_id)
            elif cap.scope_type == ScopeType.DEPARTMENT:
                q |= Q(department_id=cap.department_id)
            elif cap.scope_type == ScopeType.COMMITTEE:
                if cap.committee_school_id is None:
                    return Scholar.objects.all()
                q |= Q(department__school_id=cap.committee_school_id)
            elif cap.scope_type == ScopeType.SCHOLAR:
                q |= Q(pk=cap.scholar_id)
    return Scholar.objects.filter(q).distinct()


def capability_summary(person) -> list[dict]:
    return [{"capability": c.capability, "scope_type": c.scope_type,
             "scope_id": c.school_id or c.department_id or c.committee_id or c.scholar_id, "source": c.source}
            for c in effective_capabilities(person)]


def holds(person, capability: str) -> bool:
    return any(c.capability == capability for c in effective_capabilities(person))
