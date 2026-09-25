# Identity & authorization foundation

Implements Stage 3 of `FINAL_ARCHITECTURE.md` (in `dypiu-phd-erp-1/docs/`): **User → Person → Capabilities → Scope → Workspace**.

## Model

| Model | Purpose |
|---|---|
| `identity.Person` | One human. Optional `user` (login), `faculty_profile` (`core.Faculty`), `scholar_profile` (`scholars.Scholar`). One person may hold both profiles. Email is unique (case-insensitive). |
| `identity.CapabilityAssignment` | An explicit, scoped, time-bounded grant, with a basis, the granter and revocation fields. DB constraints: scope fields must match the scope type; derived capabilities can't be stored; no duplicate active grant. |
| `identity.AuditEvent` | Append-only, SHA-256 hash-chained record of privileged actions and every denial (`identity.audit.verify_chain()`). |

**Capabilities** (`identity/capabilities.py`):
- **Derived:** `SCHOLAR` from a scholar profile; `FACULTY` from an internal faculty profile; `DC_MEMBER` and `SDRC_MEMBER` from `core.CommitteeMembership` within the committee's tenure.
- **Granted:** all others.
- **`SUPERVISOR`** can only be granted to a person with a faculty profile. It appears as the `/faculty/supervisor` workspace.

## Authorization (`identity/authz.py`)

`authorize(person, action, resource) -> Decision` is **deny by default**. A policy rule matches when an effective capability:
- is currently valid, and
- **either** has a scope covering the resource (institution ⊃ school ⊃ department; committee → its school; single scholar),
- **or**, for relationship rules, the person has that relationship: `self`, `supervises` (an active, approved `SupervisorAssignment`) or `tac_member` (an active, approved `TACMembership`).

Other functions:
- `require()` raises `PermissionDenied`. It audits every denial, and also audits allowed privileged actions.
- `visible_scholars(person)` is the queryset equivalent of `scholar.view`. A test checks it agrees with `authorize` for every persona.

`is_superuser`, `is_staff` and Django auth Groups grant **nothing** in this layer.

| Action | Allowed by |
|---|---|
| `scholar.view` | SCHOLAR (self) · SUPERVISOR (supervises) · FACULTY (TAC member) · EXAM_EVALUATOR (that scholar) · DEPARTMENT_ADMIN / SCHOOL_ADMIN / DC_MEMBER / SDRC_MEMBER (scope) · PHD_CELL_OPERATOR, ACADEMIC_ADMIN, DEAN_RND, COE_OPERATOR, VC_OPERATOR |
| `scholar.edit_record` (privileged) | PHD_CELL_OPERATOR, ACADEMIC_ADMIN |
| `research.supervisee.act` | SUPERVISOR (supervises) |
| `supervision.assign` (privileged) | PHD_CELL_OPERATOR, DEPARTMENT_ADMIN (scope) |
| `identity.person.manage` (privileged) | SYSTEM_ADMIN, ACADEMIC_ADMIN |
| `audit.view` (privileged) | SYSTEM_ADMIN |

**Provisional (RD-38):** the action policies and the grant matrix (`GRANTABLE_BY`) are placeholders until DYPIU confirms the approving authorities. SYSTEM_ADMIN is technical: it manages identity but cannot view or edit academic records.

## Privileged operations (`identity/services.py`)

- `provision_person`, `link_user`, `link_faculty_profile`, `link_scholar_profile`, `deactivate_person`, `grant_capability`, `revoke_capability`.
- Guards:
  - no self-grant;
  - the grant matrix plus scope coverage;
  - profile email must match the person's email;
  - only a SYSTEM_ADMIN may modify a SYSTEM_ADMIN identity;
  - the last SYSTEM_ADMIN can't be revoked or deactivated;
  - you can't deactivate yourself.
- Checks and denial records run **before** the write transaction, so denials are never rolled back. Every audit event is also written to the `identity.security` logger.
- `python manage.py bootstrap_system_admin --email … --name … --basis …` creates the first SYSTEM_ADMIN. It is refused once one exists. This replaces the old client-side "seed admin email".
- The Django admin shows identity models **read-only**, even to superusers.

## Authentication foundation

- `identity.middleware.PersonMiddleware` sets `request.person`.
- `GET /identity/me/` returns the server-computed person, capabilities, workspaces and scope (department, school,
  committees, supervised scholars, active teaching assignments): 401 if anonymous, 403 if no active person is linked.
- REST (Step 5C): `POST /api/auth/login/` / `logout/` and `GET /api/auth/csrf/` (Django session + CSRF). Every
  `/api/...` view requires an active Person (`identity.api_base.HasActivePerson`); errors use one JSON shape
  (`identity.api_base.exception_handler`), and CSRF failures return JSON. See `docs/ACADEMIC_API.md`.
- Settings: `SECRET_KEY` is required when `DEBUG` is off; HttpOnly, SameSite=Lax session cookies; secure cookies, SSL redirect and HSTS in production.
- The identity provider (OIDC/SAML) is not yet connected (decision D-IdP).
