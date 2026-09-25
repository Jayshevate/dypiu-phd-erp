# DYPIU PhD ERP — Claude Code Guardrails

## 1. PROJECT AUTHORITY

This repository is a production-oriented DYPIU PhD ERP.

Architecture:

React/TypeScript UI
        ↓
Django REST API
        ↓
Django domain/services/rules/workflows
        ↓
PostgreSQL

Django is the authoritative backend.

React is NEVER the source of truth for:
- authorization
- academic eligibility
- research eligibility
- milestone completion
- workflow approval
- grades
- results
- attendance eligibility
- examination eligibility
- transcript contents
- supervisor assignment
- regulatory decisions
- financial approval
- document approval

Never move authoritative business logic into React.

---

## 2. ANTI-VIBE CODING

Do not:
- invent requirements
- invent regulatory rules
- invent approval authorities
- invent academic values
- invent permissions
- create fake/mock production data
- create placeholder implementations
- create TODO implementations
- silently simplify domain rules
- silently change existing business behavior
- bypass authorization to make a feature work
- implement unresolved regulatory decisions as facts

If a requirement is unclear:
1. inspect existing documentation/code/tests
2. check the regulatory decision sheet
3. check the architecture documentation
4. check the relevant source material
5. if still unresolved, STOP and report the ambiguity

Never guess.

An operation that is intentionally BLOCKED because a regulatory parameter or
institutional decision remains unresolved is NOT a placeholder implementation.
Blocking the operation and reporting the exact decision required is the correct
behavior (see section 3).

---

## 3. REGULATORY SOURCE OF TRUTH

Official DYPIU PhD regulations and approved regulatory decisions have priority over:
- existing React behavior
- old Django behavior
- mock data
- assumptions
- convenience
- UI behavior
- previous implementation choices

The following documents must be consulted when relevant.

In the React repository (dypiu-phd-erp-1), not in this Django repository:

- dypiu-phd-erp-1/docs/REGULATORY_DECISION_SHEET.md
- dypiu-phd-erp-1/docs/FINAL_ARCHITECTURE.md
- dypiu-phd-erp-1/docs/REPOSITORY_AUDIT.md

In this Django repository:

- docs/IDENTITY_AUTHZ.md
- relevant academic/research documentation

If a rule is marked unresolved/configurable/ambiguous,
DO NOT convert it into a hard-coded rule.

If a required institutional decision is missing, BLOCK the operation and
report the exact decision required.

---

## 4. IDENTITY AND AUTHORIZATION

There is one human identity.

User
 → Person
 → capabilities
 → scoped authorization
 → workspace

Supervisor is a capability/workspace of Faculty.

Do NOT create a second human identity for Supervisor.

/identity/me/ is the authoritative identity source.

Authorization must be server-side.

Never trust:
- frontend roles
- frontend capability flags
- localStorage roles
- query parameters
- hidden UI controls
- client-selected identity
- client-selected scholar IDs
- browser-supplied person IDs

Every privileged Django API operation must enforce authorization.

Default authorization behavior is DENY.

Expired or revoked capabilities must immediately lose access.

---

## 5. SECURITY

Never weaken security to make tests or UI work.

Never:
- bypass authorization
- add permissive fallback permissions
- trust browser-provided role information
- allow cross-scholar access without authorization
- allow cross-department/school access without authorization
- expose privileged operations through unprotected endpoints
- use Firebase/Firestore as the ERP authority
- recreate browser-side administrative authority

When adding an endpoint, always ask:

1. Who can call it?
2. What resource can they access?
3. What scope applies?
4. What capability is required?
5. What audit event is required?
6. What happens if the caller manipulates the request?

---

## 6. DOMAIN ARCHITECTURE

Academic and Research are separate bounded contexts.

Do not merge them for convenience.

Academic:
- coursework
- courses
- sections
- faculty assignments
- attendance
- assessments
- examinations
- results
- transcripts
- electives
- semester registration

Research:
- supervisor
- co-supervisor
- TAC
- proposal
- progress
- synopsis
- thesis
- examiners
- viva
- DPEP
- degree recommendation
- research milestones

Academic events may affect research eligibility through the
server-side event/rule system.

Academic events must NOT directly mark research milestones complete.

---

## 7. MILESTONE ARCHITECTURE

Use:

Event
 → Rule Evaluation
 → Eligibility
 → Workflow
 → Milestone State

Never:

UI action
 → milestone completed

Preserve official milestone numbering and stable IDs. The 42-milestone
catalogue and the status of internal sub-milestones 9.1 and 24.1 remain
subject to the applicable regulatory confirmation/decision sheet and must not
be treated as settled until confirmed.

Never renumber official milestone numbers.

Do not invent new official milestones.

---

## 8. WORKFLOW

Workflow decisions must be server-authoritative.

Do not implement workflow approval as:
- frontend state
- localStorage
- mock approval
- client-only status change

Every approval must have:
- authorized actor
- valid capability
- correct scope
- valid workflow state
- transition validation
- audit trail

---

## 9. DATABASE

PostgreSQL is the production database.

Database constraints should protect important invariants wherever practical.

Do not rely exclusively on:
- frontend validation
- serializer validation
- UI restrictions

When modifying models:
- create migrations
- test migration from zero
- test migration forward
- test migration rollback/reapply where practical
- run relevant tests

Do not manually modify production database state.

---

## 10. LEGACY CODE

Before removing or replacing existing functionality:

1. identify all imports/usages
2. identify URL references
3. identify tests
4. identify migrations/data dependencies
5. identify admin registrations
6. identify API consumers
7. identify React consumers

Do not delete a file merely because it appears unused.

First prove it is obsolete.

If obsolete code is removed, remove its references and tests safely.

---

## 11. DEPENDENCIES

Do not introduce a new dependency unless:
- the existing stack cannot reasonably provide the capability, and
- the dependency is justified.

Before adding one:
- check whether it already exists
- check package requirements
- check compatibility
- explain why it is needed

Do not replace working libraries merely for preference.

---

## 12. TESTING

Every implementation phase must include verification.

At minimum, run the relevant tests after implementation.

For significant changes, run:

- Django system checks
- relevant Django tests
- full Django test suite when practical
- migration checks
- PostgreSQL tests
- API authorization tests
- React TypeScript check
- React production build

For authorization changes, include negative tests.

Examples:

Scholar A cannot access Scholar B.

Faculty A cannot access Faculty B's restricted resources.

Expired capability is denied.

Revoked capability is denied.

Cross-school access is denied unless authorized.

Browser role manipulation does not grant access.

Unauthorized approval is denied.

---

## 13. FAILURE HANDLING

A failing test is a blocker, but do NOT automatically revert the work.

Instead:

1. inspect the failure
2. determine whether the implementation or test is incorrect
3. fix the root cause
4. rerun the relevant tests
5. report the result

Never hide failures by:
- skipping tests
- weakening assertions
- deleting tests
- disabling security
- changing expected behavior without justification

---

## 14. REACT RULES

React must consume Django APIs.

Do not use:
- localStorage as authoritative ERP state
- browser-side identity as authoritative identity
- browser-side approval
- browser-side academic calculations that determine official results
- mock production data

Use the server identity endpoint.

Handle:
- loading
- empty states
- errors
- permission denied
- validation errors
- conflicts
- successful operations

Preserve the existing UI design unless the task explicitly requires redesign.

---

## 15. API RULES

Every new API must define:

- URL
- HTTP method
- authentication
- required capability
- resource scope
- serializer/input validation
- business-rule validation
- audit requirements
- success response
- error responses
- tests

Do not expose backend operations merely because they exist.

Only expose operations supported by the domain authorization model.

---

## 16. AUDITABILITY

Important privileged operations must be auditable.

Do not silently mutate:
- grades
- results
- supervisor assignments
- TAC assignments
- approvals
- milestone states
- regulatory configuration
- transcripts
- examiner decisions

Where the domain requires history, preserve immutable/versioned records.

---

## 17. CONFIGURATION

Regulatory values that are legitimately configurable must be represented as
server-side configuration.

Do not hard-code unresolved values.

Do not allow ordinary users to modify regulatory configuration.

Configuration changes must follow the appropriate approval workflow.

---

## 18. PHASE DISCIPLINE

Work on ONE implementation phase at a time.

Before starting:
- inspect current repository state
- inspect existing implementation
- identify dependencies
- identify affected files
- state the exact scope

During implementation:
- stay inside the requested scope
- do not redesign unrelated modules
- do not start future phases
- do not rewrite working systems unnecessarily

At the end:
- run verification
- report files changed
- report tests
- report known gaps
- STOP

Do not automatically continue to the next phase.

---

## 19. GIT SAFETY

Run git status before AND after implementation.

Before implementation:

    git status

Never silently discard user work.

Do not run:

    git reset --hard
    git clean -fd

unless explicitly instructed.

Do not commit or push unless explicitly instructed.

At the end:

    git status

Report:
- modified files
- untracked files
- generated files
- migrations
- unexpected changes

---

## 20. TEMPORARY FILES

Do not leave:
- scratch scripts
- debug files
- temporary exports
- generated test artifacts
- unused components
- abandoned API modules

However, DO NOT delete anything merely because it looks unused.

First verify references and explain the deletion.

---

## 21. NO UNAUTHORIZED SCOPE EXPANSION

If asked to implement Academic:

Do not implement Research.

If asked to implement Research:

Do not redesign Academic.

If asked to implement authorization:

Do not redesign the entire UI.

If asked to fix a bug:

Do not refactor unrelated modules.

If a dependency is genuinely required, explain it before expanding scope.

---

## 22. REQUIRED FINAL REPORT

Every implementation task must end with:

### Changed
Files and major changes.

### Verified
Tests/checks/builds executed and results.

### Security
Authorization/security tests performed.

### Regulatory
Rules used and unresolved decisions encountered.

### Remaining
Known gaps or blocked functionality.

### Git
Current git status.

Then STOP.

Do not commit or push unless explicitly instructed.
