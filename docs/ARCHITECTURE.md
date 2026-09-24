# Architecture

This document answers the six design questions in the build brief. It
describes the code as it is in this repository, not a future plan.

## 0. Layers

```
phd_rules/     pure Python: every regulation number (policy.py) and rule
               (grading, durations, calendar). No Django; unit-tested alone.
core/          people (School, Department, Faculty), committees, RBAC roles,
               configurable approval chains
scholars/      admissions (cycle, application), Scholar, extensions, leave
supervision/   supervisor / co-supervisor assignment, TAC
coursework/    catalogue, rubrics, attempts, grading, completion
lifecycle/     milestone records + the phase state machine (engine.py)
deadlines/     dual-clock engine, calendar events, notifications
finance/       teaching assistantship, conference grants
documents/     form templates (appendices) and frozen generated documents
```

Domain logic lives in each app's `services.py` (writes) and
`lifecycle/engine.py` (gates). The Django admin is the back office for the
R&D office; `/scholars/` is a role-aware dashboard.

## 1. Data model

Relationships and constraints worth knowing about:

| Concern | How it is modelled |
|---|---|
| Supervisor ↔ scholar (M:N, capped) | `SupervisorAssignment(scholar, faculty, kind, start, end, approved_on)`. Partial unique indexes: one open `SUPERVISOR` per scholar; no duplicate open pair. The cap is checked in `propose_supervisor` under `SELECT … FOR UPDATE` on the faculty row. Pending and approved assignments both count, so a seat can't be promised twice. |
| Designation caps | `policy.SUPERVISOR_CAPACITY` (4 / 6 / 8); `Faculty.capacity_override` for a recorded exception. |
| TAC | `TACMembership(scholar, faculty, kind=SUPERVISOR/MEMBER)`, created in one batch linked to a `TAC_FORMATION` approval. The rules (2 members, no co-supervisor, interdisciplinary, member unique across one supervisor's scholars) live in `check_tac_members`. |
| Examiner panel | `ExaminerNomination` (≥ 8 per thesis) → VC selection flags 3 as `selected` → one `ExaminerReport` each. A replacement marks the old one `replaced` and adds a new report. |
| History | Assignments and TAC seats are never deleted; they get an `end_date`. Phase changes are appended to `PhaseTransition`. Approvals keep every `ApprovalDecision`. |
| Extensions | `ExtensionGrant(kind, status)`; the programme ceiling and deadlines are derived from **granted** rows, so they are never stored twice. |

## 2. State machine

`scholars.Phase` is the 10-phase main line; `scholars.Status` holds the
terminal/side states (`ACTIVE`, `AWARDED`, `CANCELLED`, `WITHDRAWN`, `ON_BREAK`).

- `GATES[phase](scholar)` returns a list of **reasons** the phase cannot be
  left. An empty list means the gate is open. The dashboard shows these reasons.
- `advance()` locks the scholar row, re-evaluates the gate, increments the
  phase and logs a `PhaseTransition`. `advance_all()` repeats this while
  gates stay open, because work overlaps (supervisor/TAC is usually done
  during coursework).
- `terminate()` handles cancellation and withdrawal. It is called automatically
  by `record_proposal_outcome` (2 failed proposals) and `decide_dc_review`
  (DC decides to cancel).
- **Aggregate gates** such as "≥2 of 3 examiners commend" are gate functions
  over child rows, not approvals.
- **Multi-level approvals** are data: `ApprovalChain → ApprovalStep(role,
  own_scholar_only)`. A request is started with `start_approval(code, …)`
  and walked with `decide()`. Each step checks the approver's role, and for
  `own_scholar_only` steps also that the approver is that scholar's own
  supervisor. Domain objects react through `@on_decision(code)` callbacks
  inside the same transaction.
- **Overrides**: the VC third coursework attempt is an approved
  `COURSEWORK_THIRD_ATTEMPT` request passed to `register_attempt`. Extensions
  are `ExtensionGrant`s with their own chains. Who may approve what is changed
  in the admin, not in code.
- **Category-dependent chains**: `resolve_chain(code, scholar)` prefers a
  chain whose `scholar_category` matches (e.g. a `LEAVE` chain for
  `PT_EXTERNAL`) and falls back to the default chain.

## 3. Dual-clock deadline engine (`deadlines/engine.py`)

| Clock | Source | Stored as |
|---|---|---|
| Global | `phd_rules.calendar_rules.events_for_year` (vacancies, RPET weekends in the 20–25 window, exams, registration cut-offs, TAC report cut-offs) | `AcademicEvent`. Created if missing, never overwritten, and has a `confirmed` flag for the R&D office. |
| Per scholar | `scholar_deadlines()` derives each deadline from that scholar's dates: registration, supervisor approval, coursework pass, synopsis clearance, examiner dispatch, last report, fellowship start. Granted extensions shift the dates, and every date is clamped to the programme ceiling. | `Deadline(scholar, key, due_date, met)` with a stable `key` (e.g. `PROGRESS:3`, `EXAMINER:17`). Recomputation upserts rows and drops unmet rows that no longer apply. |

`python manage.py run_deadline_engine` (daily cron) syncs both clocks and
sends tiered reminders: 30, 7 and 1 days before, plus once when overdue.
Overdue reminders also go to the R&D office. A unique constraint on
`(recipient, deadline|event, tier)` makes the job idempotent. Progress
reports are generated six months ahead of "today", so the table never grows
without bound.

## 4. Tech stack

| Choice | Why |
|---|---|
| Python 3.11 + Django 5.2 | The admin gives the R&D office a working back office for ~35 entity types on day one. Built-in auth, groups, migrations and transactions cover the rest. |
| PostgreSQL (prod), SQLite (dev/tests) | Partial unique indexes and `SELECT … FOR UPDATE` are needed for the capacity rules. Set `POSTGRES_DB` etc. to switch. |
| Cron / systemd timer → `run_deadline_engine` | One idempotent daily job, so no Celery is needed at this scale (hundreds of scholars). Add Celery or RQ only for e-mail volume. |
| Server-rendered templates | Low-bandwidth, no build step. Add Django REST Framework later if a mobile app or SPA is needed. |
| SSO (future) | Map the university IdP (Google Workspace / Azure AD) to Django users; roles stay Django groups. |

## 5. Phased build plan

| Release | Scope | Status in this repo |
|---|---|---|
| **MVP-1: register & track** | Scholar records, admissions scoring, supervisor/TAC with caps, approval chains, deadline engine, dashboards | ✅ implemented |
| **MVP-2: coursework** | Catalogue, attempts, grading, rubrics, ethics gate, completion | ✅ implemented (rubric weights to be entered) |
| **MVP-3: progress → thesis** | Proposal, progress/warnings, DC review, synopsis, thesis, examiners, viva, award | ✅ domain + admin; scholar-facing submission forms pending |
| **MVP-4: money & time** | TA, grants, leave, extensions | ✅ domain + admin |
| **Next** | Forms library with official appendix wording; e-mail delivery of notifications; scholar self-service upload forms; approval inbox UI; reports (per-school load, overdue by phase); import of existing scholars from spreadsheets; SSO | ⏳ |

Recommended order for go-live: load faculty and current scholars first (a CSV
importer is next), then turn on the deadline engine. That gives value from the
first week, because overdue items surface before any new workflow is adopted.

## 6. Hard-to-model regulations and how they are handled

| Edge case | Handling |
|---|---|
| Rolling vs fixed deadlines | Two separate clocks (above). The per-scholar clock is derived data, recomputed daily, never hand-edited. |
| Category-dependent flows | `Category` on the scholar; category-specific approval chains; admission gate needs a sponsorship/NOC letter for PT sponsored / sister institution; synopsis limit by mode (FT 4 yr, PT 5 yr). |
| Overrides by VC / Dean / DC | Approval chains as data plus explicit override objects (`ExtensionGrant`, `COURSEWORK_THIRD_ATTEMPT`), never boolean flags. |
| Concurrent allocation to the last supervisor seat | Row lock plus pending assignments counted in the load. |
| Examiner no-shows | `examiner_escalations()` gives reminders at 60 days and replacements after 30 more; `replace_examiner` keeps the history. |
| Extensions stacking into the ceiling | Deadlines are clamped to `programme_ceiling`; each extension kind can be granted once (partial unique index). |
| Overlapping phases | Gates check facts, not the order in which they happened; `advance_all` catches up. |
| Documents as audit trail | `GeneratedDocument.rendered` is frozen at generation and can link to the workflow object; a signed scan can be attached. |
