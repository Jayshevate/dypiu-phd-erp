# DYPIU PhD ERP: Inspection Report & Integration Plan

| | |
|---|---|
| Status | **Draft for decision. No integration work has started.** |
| Django code inspected | branch `claude/hopeful-bohr-ubwcsm`, commit `d70b746` |
| React / Google AI Studio code | **Not yet inspected** (to be provided) |
| Scope of this change | This document only. No code was changed. |

Part A is an inventory of the Django implementation. It was built from the
code itself (model introspection, URL resolver, admin registry, a grep of the
call graph and a verbose test run), not from the earlier summary. Parts B–E
cover the integration.

> **Headline finding.** The Django project is a tested **domain core**:
> rules, models and service functions. It is **not yet a usable application**.
> None of the workflow services is reachable from any UI or API. Only tests
> (or a Python shell) can call them. The only user-facing surfaces are
> two read-only pages and the stock Django admin, and admin edits **bypass**
> every business rule. The earlier statement that MVP-1 to MVP-4 are
> "implemented" (in `docs/ARCHITECTURE.md` §5) overstates this and should be
> read as "domain logic implemented".

---

## Part A: What the Django implementation contains

### A1. Size and shape

- About 3,800 lines of Python excluding migrations. About 1,200 of those are tests.
- 8 Django apps plus 1 pure-Python package (`phd_rules`), 3 HTML templates,
  2 management commands, and 4 docs (`REQUIREMENTS`, `ARCHITECTURE`, `OPEN_QUESTIONS`, this file).
- Dependency: `Django>=5.2,<6.0` only. No API framework, no PostgreSQL driver
  installed, and no task queue.

### A2. Django apps

| App / package | Purpose | Files of note |
|---|---|---|
| `phd_rules` (not a Django app) | Regulation numbers and pure functions: grading, date arithmetic, durations, calendar | `policy.py`, `grading.py`, `durations.py`, `calendar_rules.py`, `dates.py` |
| `core` | Schools, departments, faculty, committees, roles, approval chains | `models.py`, `roles.py`, `approvals.py`, `seed.py`, `testing.py` |
| `scholars` | Admission cycles, applications, the scholar record, extensions, leave, dashboard views | `models.py`, `services.py`, `views.py`, `urls.py` |
| `supervision` | Supervisor / co-supervisor assignment, TAC seats | `models.py`, `services.py` |
| `coursework` | Course catalogue, rubric components, attempts, grading | `models.py`, `services.py`, `seed.py` |
| `lifecycle` | Milestone records (proposal to degree award), phase state machine, audit of transitions | `models.py`, `services.py`, `engine.py` |
| `deadlines` | Global calendar events, per-scholar deadlines, notifications | `models.py`, `engine.py`, command `run_deadline_engine` |
| `finance` | Semesters, TA registration/assignment/feedback, conference grant claims | `models.py`, `services.py` |
| `documents` | Form templates and generated (frozen) documents | `models.py`, `services.py` |

### A3. Models (40 domain models across 8 apps)

✱ = database constraint beyond the primary key.

**core (9)**
- `School(code, name)`, `Department(school, code, name)`
- `Faculty(user?, name, email, designation, department, affiliation, is_external, superannuation_date, capacity_override)`. Its `supervision_capacity` property is derived from designation.
- `Committee(type ∈ DC/SDRC/DPEP/EXAM/PROPOSAL, name, school, tenure_start, tenure_end)`
- `CommitteeMembership(committee, faculty, role)` ✱ unique (committee, faculty)
- `ApprovalChain(code, scholar_category, name, description)` ✱ unique (code, category)
- `ApprovalStep(chain, order, role, own_scholar_only, label)` ✱ unique (chain, order)
- `ApprovalRequest(chain, scholar, generic target, summary, requested_by, status, current_step, created_at, closed_at)`
- `ApprovalDecision(request, step, decided_by, approved, remarks, decided_at)`

**scholars (5)**
- `AdmissionCycle(name, vacancy_notified_on, rpet_date)`
- `Application(cycle, name, email, category, entry_qualification, department, SOP file, CV file, exemption, rpet_percent, interview_percent, decision)`. Properties: `rpet_cleared`, `interview_cleared`, `eligible_for_selection`.
- `Scholar(user?, application?, prn, name, email, gender, pwd_percent, category, entry_qualification, department, research_area, admission_date, registration_date, registration_fee_paid, sponsorship_letter, fellowship, fellowship_start, phase, status, coursework_completed_on)`. Properties: `mode`, `relaxation_eligible`, `programme_ceiling`, `stipend`.
- `ExtensionGrant(scholar, kind, reason, status, approval, requested_on)` ✱ one GRANTED row per (scholar, kind)
- `LeaveRecord(scholar, type, start_date, end_date, reason, approved, approval)`

**supervision (2)**
- `SupervisorAssignment(scholar, faculty, kind, start_date, end_date, approved_on, approval)` ✱ one open SUPERVISOR per scholar; ✱ no duplicate open (scholar, faculty)
- `TACMembership(scholar, faculty, kind, start_date, end_date, approved_on, approval)` ✱ no duplicate open (scholar, faculty)

**coursework (4)**
- `Course(code, title, credits, category, required_for_all, has_ethics_submodule, coordinator)`
- `AssessmentComponent(course, name, weight, is_gate, gate_min_percent)`
- `CourseAttempt(scholar, course, attempt_no, exam_date, marks, special_grade, ethics_cleared, grade, grade_point, override)` ✱ unique (scholar, course, attempt_no)
- `ComponentScore(attempt, component, percent)` ✱ unique (attempt, component)

**lifecycle (10)**
- `PhaseTransition(scholar, from/to phase, from/to status, actor, note, at)`. Append-only in admin.
- `ResearchProposal(scholar, attempt_no, title, document, submitted_on, committee, seminar_date, outcome, remarks)` ✱ unique (scholar, attempt_no)
- `ProgressReport(scholar, period_no, due_date, submitted_on, document, tac_meeting_date, tac_minutes, outcome, dc_review)` ✱ unique (scholar, period_no)
- `DCReview(scholar, reason, opened_on, decision, decided_on, remarks)`
- `Synopsis(scholar, submitted_on, document, open_seminar_date, tac_cleared_on, remarks)`
- `Thesis(scholar, title, submitted_on, document, plagiarism_percent, plagiarism_report, fees_paid, panel_approval)`
- `ExaminerNomination(thesis, name, affiliation, email, is_foreign, selected, replaced)`
- `ExaminerReport(examiner, dispatched_on, reminded_on, received_on, recommendation, document)`
- `Viva(thesis, dpep, scheduled_on, venue, candidate_notified_on, invitation_published_on, outcome, report)`
- `DegreeAward(scholar, approval, approved_on, certificate_no, issued_on, academic_council_notified_on)`

**deadlines (3)**
- `AcademicEvent(key, kind, title, start, end, confirmed)` ✱ unique key
- `Deadline(scholar, key, kind, title, due_date, met, updated_at)` ✱ unique (scholar, key)
- `Notification(recipient, deadline?, event?, tier, message, created_at, emailed_at, read_at)` ✱ one per (recipient, deadline|event, tier)

**finance (5)**
- `Semester(code, start_date, end_date, fee_deadline)`
- `TARegistration(scholar, semester, form_submitted_on, fee_receipt, expertise, state)` ✱ unique (scholar, semester)
- `TAAssignment(registration, school, course_name, faculty, hours_per_week, contact_hours_per_week)`
- `TAFeedback(registration, source, rating, satisfactory, comments)` ✱ unique (registration, source)
- `GrantClaim(scholar, event_name, event_date, amount_claimed, amount_approved, receipts, state, approval, submitted_on)`

**documents (2)**
- `FormTemplate(code, title, phase, body, active)`
- `GeneratedDocument(template, scholar, generic obj, rendered, signed_copy, created_by, created_at)`

Enumerations: `Phase` (10), `Status` (ACTIVE, AWARDED, CANCELLED, WITHDRAWN, ON_BREAK),
`Category` (FT, PT_SPONSORED, PT_SISTER, PT_EXTERNAL, PT_INTERNAL),
`EntryQualification` (BTECH, MTECH, PG), `Fellowship` (NONE, JRF, SRF),
`Designation` (Assistant/Associate/Professor/Other), and `Role` (15).

### A4. Services and business rules

"UI entry point" means whether any view, admin action, API or command
calls the function. **Answer for every row: none.** They are called only from
other services and from tests.

| Area | Function | Rule(s) enforced |
|---|---|---|
| Approvals | `start_approval`, `decide`, `can_decide`, `resolve_chain`, `@on_decision` | Ordered role steps; step role must match the user's group; `own_scholar_only` steps need the scholar's own supervisor/TAC member; a rejection closes the chain; the category-specific chain is preferred; callbacks run in the same transaction. **Superusers bypass all role checks.** |
| Extensions | `request_extension` (+ callback) | Each kind granted once; relaxation only for female or PwD > 40%; opens the matching chain |
| Leave | `validate_leave`, `apply_for_leave` (+ callback) | Annual ≤ 25 days per calendar year (no cross-year leave); maternity ≤ 240 days total. Career/semester break: **no rules** |
| Supervision | `propose_supervisor`, `check_supervisor_eligibility`, `supervisor_load`, `current_supervisor` | Cap 4/6/8 by designation (pending and co-supervisions count); external faculty only as co-supervisor; ≥ 3 years of service remaining; one supervisor per scholar; faculty row locked (effective on PostgreSQL only) |
| TAC | `propose_tac`, `check_tac_members`, `tac_complete` | Needs an approved supervisor; exactly 2 distinct members; no supervisor/co-supervisor as member; members from another department; a member is unique across one supervisor's scholars |
| Coursework | `register_attempt`, `record_result`, `marks_from_components`, `evaluate`, `update_coursework_status` | ≤ 2 attempts within 2 years unless there is an approved `COURSEWORK_THIRD_ATTEMPT`; no re-attempt after a pass; ethics result required for SIS7001; rubric weights must sum to 100; a failed gate component means F; completion needs every course ≥ C+, GPA ≥ 6.0, credits for the entry category, all core courses and the ethics module cleared |
| Grading (pure) | `grade_for_marks`, `is_pass`, `gpa`, `evaluate_coursework` | 8 bands, marks rounded half-up |
| Proposal | `record_proposal_outcome` | 2 failing outcomes cancel registration |
| Progress | `record_tac_review`, `open_warnings`, `decide_dc_review` | 2 unresolved unsatisfactory reviews open a DC review; a DC CANCEL decision terminates registration |
| Examiners | `submit_examiner_panel`, `select_examiners`, `replace_examiner`, `examiner_escalations` | ≥ 8 nominees; the panel approval must be approved; exactly 3 selected from the panel; a replacement comes from unselected nominees; reminder at 60 days, replacement 30 days later (**computed only, never executed**) |
| Viva | `validate_viva` | Candidate told ≥ 15 days before; open invitation ≥ 7 days before; held ≤ 2 months after the last report (**never called on save**) |
| Degree | `start_degree_award` (+ callback) | Opens the 5-step chain (DC → Dean R&D → COE → Registrar → VC) |
| State machine | `evaluate_gate`, `advance`, `advance_all`, `terminate` | 10 gate functions (see ARCHITECTURE §2); transitions logged; row lock |
| Deadlines | `scholar_deadlines`, `sync_scholar`, `sync_global_calendar`, `dispatch`, `run`, `tier_for` | Rolling per-scholar deadlines, extension-aware and clamped to the programme ceiling; 14 global events a year; reminders at 30/7/1 days and overdue, idempotent |
| TA | `ta_eligibility_problems`, `register_for_ta`, `assign_ta` | ≤ 10 semesters; form ≤ 2 days after fee deadline; previous semester's 3 feedback sources present and satisfactory; ≤ 20 h/week, ≤ 10 contact hours |
| Grants | `submit_grant_claim`, `grant_balance`, `grant_committed` (+ callback) | ₹50,000 lifetime (approved + pending); ≥ 6 months between supported events |
| Documents | `generate` | Renders a Django template body and freezes the output |
| Durations (pure) | `programme_ceiling`, `synopsis_due`, `thesis_submission_due`, `progress_report_due_dates`, … | Durations and extensions from the brief |

**Services are not authorization-aware.** Apart from `decide()`, no service
takes or checks an acting user. For example, `record_result` does not check
that the caller is a course coordinator or COE, and `select_examiners` does
not check for the VC.

Approval chains seeded (16). Four of them have **no code that uses them**:
`SUPERVISOR_CHANGE`, `PROPOSAL_EVALUATION`, `SRF_PROMOTION`, `WITHDRAWAL`.

### A5. Interfaces

| Kind | What exists |
|---|---|
| HTTP views | `GET /` redirects to the dashboard. `GET /scholars/` is the role-scoped dashboard (overdue/upcoming deadlines, scholar list). `GET /scholars/<prn>/` is the scholar detail page (gate reasons, deadlines, approvals, history). **Both are read-only; there are no forms and no POST endpoints.** |
| API | **None.** No REST/GraphQL, no OpenAPI, no JSON endpoints. |
| Django admin | 32 models registered (incl. auth User/Group). Plain `ModelAdmin` CRUD. No `save_model` overrides and no admin actions. So: (a) supervisor/TAC/attempt/grant/leave rows created in admin **skip every service rule**; (b) `phase`/`status` are read-only and there is **no action to advance a phase**; (c) approval decisions are read-only inlines, so **approvals cannot be decided from any UI**; (d) `CourseAttempt.grade` is read-only and never computed on admin save. |
| Management commands | `seed_reference_data` (roles, chains, 6 core courses; idempotent), `run_deadline_engine [--today]` (intended for daily cron) |
| Templates | `base.html`, `scholars/dashboard.html`, `scholars/detail.html`. Minimal inline CSS, no JS. |

### A6. Authentication and RBAC

- **Authentication:** Django username/password sessions via the admin login page
  (`LOGIN_URL = admin:login`). No SSO, MFA, password-reset flow or lockout.
- **Roles:** 15 `Role` values, each a Django `Group` of the same name.
  `user_has_role` checks group membership, and superusers always pass.
- **Where roles are used:** (1) approval step checks in `decide()`;
  (2) dashboard visibility: oversight roles (DC, SDRC, Dean R&D, VC, COE,
  Registrar, R&D Office) see all scholars; faculty see scholars they
  supervise or sit on the TAC of (including *pending* supervisions); a
  scholar sees themself; (3) choosing who receives notifications.
- **Not implemented:**
  - Roles have **no scope**: a DC member is DC for every school, and committee
    membership (`CommitteeMembership`) is not consulted for authorization.
  - No Django model permissions are assigned to any group, and `is_staff` is
    never set. In practice **only superusers can use the admin**, and a
    superuser can do anything, bypassing the rules.
  - No object-level permissions, no field-level restrictions, and no
    separation of duties (e.g. the same superuser can approve every step of
    a chain; the tests rely on this).
  - Roles `COURSE_COORDINATOR`, `EXTERNAL_EXAMINER`, `DPEP_MEMBER`,
    `CO_SUPERVISOR` and `TAC_MEMBER` are defined but not used by any check.
    External examiners have no user accounts; they are text records.

### A7. Database configuration

- The default is SQLite (`db.sqlite3`). PostgreSQL is used when `POSTGRES_DB` is set
  (plus `POSTGRES_USER/PASSWORD/HOST/PORT`). The driver `psycopg` is **not in
  requirements** (it is commented out), and the code has **never been run on PostgreSQL**.
- `SELECT … FOR UPDATE` (supervisor capacity, approvals, phase advance) is a
  no-op on SQLite, so the concurrency protections are untested.
- Partial unique indexes (conditional `UniqueConstraint`) are supported on both.
- `TIME_ZONE = Asia/Kolkata`, `USE_TZ = True`, but 11 call sites use
  `date.today()` (server-local date) rather than `timezone.localdate()`.
- File uploads use `FileField` on the local `MEDIA_ROOT`. There is no storage backend,
  no access control on files, and no URL route serving media.
- Security settings: `SECRET_KEY` has an insecure default and `DEBUG` defaults
  on. `check --deploy` reports HSTS, SSL redirect, secure-cookie and weak-key warnings.

### A8. Tests: the 67 that pass

Run with `python manage.py test` (Django runner, SQLite, about 12 s). All 67 pass at `d70b746`.

| Module | # | Tests |
|---|---|---|
| `phd_rules.tests` (pure) | 17 | Grading: band_edges, rounds_half_up, out_of_range, c_is_not_a_pass, credit_requirements_by_entry, ethics_submodule_must_clear, gpa_floor · Dates: add_months_clamps, months_between · Durations: ceilings, relaxation_eligibility, synopsis_by_mode, thesis_window, progress_reports_roll_from_registration · Calendar: rpet_always_on_a_weekend_in_window, year_events, viva_notice |
| `core.tests` | 5 | steps_run_in_order_and_enforce_roles, rejection_closes_request, own_scholar_supervisor_step, category_specific_chain_wins, unknown_chain |
| `supervision.tests` | 13 | Capacity: assistant_professor_capped_at_four, co_supervision_counts_towards_cap, ended_assignment_frees_a_seat, external_cannot_be_main_supervisor, service_remaining, one_supervisor_per_scholar, approval_activates_rejection_ends · TAC: happy_path, needs_approved_supervisor, exactly_two_members, co_supervisor_excluded, interdisciplinary, member_unique_across_same_supervisors_scholars |
| `coursework.tests` | 6 | completion_requires_credits, ethics_result_required_for_sis7001, third_attempt_needs_vc_override, attempt_window, cannot_reattempt_passed_course, rubric_and_gate_component |
| `lifecycle.tests` | 7 | admission_gate, sponsored_part_time_needs_letter, full_journey_to_award, minimum_duration_blocks_early_thesis, two_failed_proposals_cancel_registration, two_warnings_open_dc_review, examiner_escalation |
| `deadlines.tests` | 6 | rolling_deadlines_from_registration, part_time_synopsis_and_extension, met_flags_and_tac_deadline, tiered_notifications_are_idempotent, global_calendar_sync_does_not_overwrite, run_command |
| `scholars.tests` | 8 | rpet_exemption_and_interview, programme_ceiling_extensions, annual_leave_cap, maternity_cap_and_approval, supervisor_sees_only_own_scholars, oversight_role_sees_all, scholar_redirected_to_own_page, login_required |
| `finance.tests` | 4 | hour_caps, form_deadline, feedback_gates_next_semester, lifetime_cap_and_gap |
| `documents.tests` | 1 | render_is_frozen |

**What the tests prove:** the rule functions and services behave as coded for
the listed cases. The end-to-end test drives one scholar through all 10
phases by calling services directly.

**What they do not prove:**
- That any user can perform these workflows (there is no UI or API to test).
- Behaviour on PostgreSQL, or under concurrency.
- Authorization of the services themselves: `t.approve_all()` uses a superuser.
- That the admin keeps data valid (it does not).
- That the regulations are interpreted correctly: the tests assert *my* reading
  of the brief, including the 17 assumptions below.
- Anything about email, files, documents-as-PDF or performance.

The admin smoke check run during development (all 32 admin pages render) was
ad hoc and is not a committed test.

### A9. Genuinely implemented vs placeholder / admin-only

| Capability | State |
|---|---|
| Regulation constants and pure rule functions | **Implemented and unit-tested** |
| Domain models, constraints, migrations | **Implemented** |
| Workflow services (supervision, TAC, coursework, proposal, progress, examiners, degree, TA, grants, leave, extensions) | **Implemented in code, tested, not reachable by users** |
| Approval-chain engine | **Implemented and tested; no UI to decide approvals** |
| Phase state machine | **Implemented and tested; no UI to advance** |
| Deadline engine + reminders | **Implemented** (command); notifications stored in DB only, **no email, no in-app inbox** |
| Scholar dashboard / detail | **Implemented, read-only** |
| Everything else users would do (enter marks, propose supervisors, submit reports/thesis, apply for leave/grants/TA, approve) | **Admin-only raw CRUD that bypasses rules**, or not possible at all |
| Documents / forms | **Placeholder**: mechanism only; no templates, no official wording, no PDF |
| Rubrics (SIS7005/6/7) | **Placeholder**: mechanism only; weights not entered |
| Admissions | **Partial**: scoring properties only; no application intake, merit list, interview scheduling or conversion to Scholar |
| Exams | **Not implemented** beyond calendar events and per-attempt marks |

### A10. Defects and weaknesses found during this inspection (not fixed; recorded only)

1. **Admin bypasses all business rules** (A5). This is the most serious issue for data integrity.
2. **Services lack actor authorization** (A4). Server-authoritative authorization does not exist yet except in `decide()`.
3. **Rejected leave still counts toward caps**: `LeaveRecord` has only `approved: bool`, so rejected and pending both count (`scholars/services.py:34`).
4. **`start_degree_award` does not check** that a satisfactory viva exists (`lifecycle/services.py:126`). The chain can complete before the viva; the phase gate order is the only protection.
5. **Examiner escalations are never executed**: `examiner_escalations()` is not called by `run_deadline_engine`, and nothing sets `reminded_on`.
6. **`validate_viva` is never called** on save.
7. **Four seeded approval chains are unused** (A4).
8. **Roles are unscoped** (A6); committee membership is not used for authorization.
9. **`date.today()` is timezone-naive** relative to `Asia/Kolkata`.
10. `ProgressReport` rows are never auto-created, and the "≥ 2 TAC reports/year to DC" rule exists only as a calendar event.
11. **SRF promotion is manual**: the eligibility deadline is informational and there is no promotion service.
12. `ON_BREAK` status, career break and semester break have **no rules or effects** (e.g. they do not pause deadlines).
13. `ARCHITECTURE.md` §5 marks MVP-1 to 4 "✅ implemented", which overstates the state (see the headline finding).

### A11. Assumptions (from `docs/OPEN_QUESTIONS.md`, all unconfirmed)

1. Co-supervisions count toward the 4/6/8 cap.
2. Minimum 3 years of service remaining to take a scholar.
3. "TAC uniqueness" means a faculty member may sit on only one TAC among a given supervisor's scholars.
4. An "interdisciplinary" TAC member is from a department other than the scholar's.
5. Proposal outcomes are Recommended / Minor mods (pass) and Resubmit / Not recommended (fail); 2 fails cancel registration.
6. Examiner recommendations are Commend / Revise & resubmit / Not commend.
7. Examiner escalation: reminder at 60 days, replacement 30 days after the reminder.
8. Female/PwD +2 years is independent of re-registration; maximum 10 years total.
9. RPET exemptions: GATE, NET, CSIR, SLET, JRF.
10. Semesters start 1 Jan and 1 Jul.
11. "Early May/Dec" exams fall on days 1–10.
12. The leave year for the 25-day cap is the calendar year.
13. SIS7005/6/7 rubric weights are not seeded.
14. The core courses are SIS7001–7003 and SIS7005–7007 (14 credits), with electives making up the totals. This is inferred from the credit totals.
15. Approval chains per workflow are defaults from `core/seed.py`.
16. Marks are rounded half-up to a whole mark before banding.
17. The audio recording has not been processed.

A cross-cutting assumption not listed there: **all scholars are governed by
the 1 July 2026 regulations**. Nothing models which regulation version
applies to which admission cohort.

### A12. Missing functionality, against the required capabilities

| Required capability | Django today | Gap |
|---|---|---|
| Institutional RBAC | Global groups, 15 roles | Scoping (school/dept/committee/scholar), role assignment with validity dates, delegation, separation of duties, permission matrix |
| Server-authoritative authorization | Only in `decide()` | Actor-aware policy checks in every command; admin locked down |
| Academic workflows | Services only | UI/API for every step |
| Research workflows | Services only | UI/API, document uploads, TAC meeting minutes workflow |
| **42 official PhD milestones** | **10 phases only** | Milestone catalogue (list not yet provided), per-scholar milestone tracking, mapping to phases, forms and deadlines |
| Configurable regulatory rules | Python constants + editable approval chains | DB-backed, versioned rule sets by effective date / cohort; admin UI for them |
| Dual-clock deadlines | Implemented | Pause on leave/break, escalations, calendar editing UI, per-school calendars |
| Supervisor capacity | Implemented in service | Enforced only via service; change-of-supervisor workflow missing |
| TAC / DC / SDRC | TAC implemented; committees are data only | Committee-scoped authority, meetings, minutes, quorum |
| Coursework | Implemented in services | Course registration/enrolment UI, elective choice |
| Exams | **Missing** | Exam sessions, registration, eligibility, hall tickets, marks entry, moderation, result publication lock (COE) |
| Marks / grades | Per-attempt marks → grade | Marks-entry workflow, audit of mark changes, grade cards / transcripts |
| Thesis | Model + gate | Submission UI, plagiarism report handling, versions |
| Examiners | Services | Examiner portal/accounts, dispatch letters, honorarium, escalations wired |
| Viva / DPEP | Model + validation function | Scheduling UI, DPEP composition rules, public invitation, report capture |
| Degree award | Chain + model | Certificate generation, Academic Council notification record |
| TA | Services | Semester workflow UI, School lists, stipend/HR interface |
| Grants | Services | Receipts processing, disbursement, finance integration |
| Leave | Annual/maternity only | Rejected-state fix, career/semester breaks, deadline pausing |
| Documents | Mechanism | ~35 official templates, PDF output, signatures, secure storage |
| Notifications | DB rows | Email/SMS delivery, in-app inbox, preferences, templates |
| Audit | PhaseTransition, ApprovalDecision, Django admin LogEntry | Uniform audit of every command (actor, before/after), tamper-evidence, retention |
| Reporting | Two lists | Institutional reports (load, overdue by phase, completion times, NIRF/UGC returns), exports |
| Imports | **Missing** | Existing scholars, faculty, historical marks, with dry-run and validation |
| SSO readiness | **Missing** | OIDC/SAML integration, user provisioning, role mapping |
| Known gaps from the previous run | — | Scholar self-submission, approval inbox, email reminders, importer, SSO, official wording/rubrics, audio requirements |

---

## Part B: React / Google AI Studio frontend (pending)

This part is not written because the repository has not been provided. When it
is, it will be inspected with the same method, specifically for:

1. **Structure:** routes/pages, component library, state management, build config, TypeScript strictness.
2. **Data layer:** mock data vs real calls; where types live; whether the domain types match the Django models above.
3. **Backend assumptions:** Firebase/Firestore, Supabase, a Node server, or none.
4. **Business logic in the browser:** rules duplicated client-side (they can become UX hints, never the authority).
5. **Auth:** how login and roles are represented; whether authorization is client-only.
6. **Secrets:** AI Studio apps commonly call the Gemini API from the browser with a key in the bundle. If so, this must move server-side.
7. **Coverage:** which of the 42 milestones and modules have screens.
8. **Quality:** tests, lint, accessibility, responsiveness.

The output will be a React inventory in the same format as Part A, plus a
screen-to-endpoint mapping table.

---

## Part C: Architecture evaluation

Django is **not** assumed to be the final backend. The options are evaluated
against the required capabilities.

### Options

| | A. Django backend + React SPA | B. TypeScript backend (NestJS/Fastify + Prisma/Drizzle + PostgreSQL) + React | C. BaaS (Firebase/Firestore or Supabase) + React | D. React with client-side logic only |
|---|---|---|---|---|
| Server-authoritative rules | ✅ services exist; need actor checks | ✅ once written | ⚠️ Firestore rules unsuitable for complex regulation; Supabase needs SQL/RLS + edge functions | ❌ disqualifying |
| Relational integrity (caps, uniqueness, locks) | ✅ PostgreSQL, partial indexes, row locks | ✅ PostgreSQL | ❌ Firestore / ✅ Supabase (Postgres) | ❌ |
| Reuse of existing work | ✅ all Django rules/tests; React UI reused as client | ⚠️ port `phd_rules` (~300 LOC) + services (~800 LOC) + tests; React reused | ⚠️ reuse React; rewrite rules | ✅ React only |
| Back office for R&D office | ✅ Django admin (after lockdown) | ❌ must be built | ⚠️ Supabase studio is not an end-user tool | ❌ |
| One language / shared types | ❌ Python + TS; mitigated by OpenAPI-generated TS types | ✅ | ✅ | ✅ |
| SSO (OIDC/SAML) | ✅ mature libraries | ✅ mature libraries | ✅ built-in | n/a |
| Reporting / SQL | ✅ | ✅ | ❌ Firestore / ✅ Supabase | ❌ |
| Hosting on university infrastructure | ✅ | ✅ | ⚠️ vendor-hosted | — |
| Team skills | Unknown: **decision input needed** | Unknown | Unknown | — |

### Provisional recommendation

**Option A: Django (plus an API layer) as the system of record, with the React
app as the primary user interface.** Keep the Django admin only as a locked-down
super-admin and configuration console.

Why: the domain rules, constraints and tests already exist and are the
hardest part to get right. The regulatory, relational and reporting needs
favour PostgreSQL behind a server. Django's admin covers configuration
screens (approval chains, rule values, calendars, templates) that would
otherwise have to be built. Generating a TypeScript client from an OpenAPI
schema removes most of the two-language cost.

**Conditions that would change this recommendation** (checked once the React repo is seen):
- The React repo already contains a substantial, tested TypeScript backend on
  PostgreSQL → Option B becomes competitive. The Django rules are small enough
  to port, with the Django tests used as the specification.
- The institution's maintainers are TypeScript-only → Option B, for long-term ownership.
- The institution mandates a specific platform (e.g. Azure/.NET, or an existing
  Firebase tenancy) → re-evaluate.

Option D is rejected outright: client-side authorization cannot satisfy the
server-authoritative requirement. Option C with Firestore is not recommended
as the system of record for this domain.

---

## Part D: Integration plan (assuming Option A; not started)

### D1. Target architecture

```
Browser ── React SPA (Vite, TS) ──┐
                                  │ same origin: https://phd.dypiu.ac.in
                         reverse proxy (nginx)
                           ├── /            → built SPA assets
                           ├── /api/v1/…    → Django (gunicorn) → PostgreSQL
                           ├── /admin/      → Django admin (restricted network/roles)
                           └── /auth/…      → OIDC/SAML callbacks (SSO)
Workers: deadline engine (daily), email/notification queue, PDF rendering
Storage: S3-compatible object store for uploads/generated documents (signed URLs)
```

- **Same-origin deployment** lets the SPA use Django's session cookie + CSRF.
  No tokens are kept in `localStorage`, and SSO works through server-side redirects.
- **Monorepo later** (`backend/`, `frontend/`, `docs/`). Files are **not moved**
  until the plan is approved.

### D2. API contract

- The API is REST, documented as OpenAPI 3 (`drf-spectacular` with DRF, or
  `django-ninja`). A TypeScript client and types are **generated** from the
  schema (e.g. `openapi-typescript`/`orval`) and consumed with TanStack Query.
- **Command endpoints map one-to-one to services.** Example:
  `POST /api/v1/scholars/{prn}/supervisor-proposals` → `propose_supervisor`.
  Generic CRUD on workflow tables is avoided, because it recreates the admin-bypass problem.
- **Read models:** scholar summary, gate reasons, deadlines, inbox, dashboards.
- **Error contract:** a service `ValidationError` becomes HTTP 422
  `{reasons: [...]}`, so the UI shows the regulation reasons verbatim.
- `GET /api/v1/me` returns the user, scoped roles and a **capability list**. The
  UI uses it only to hide controls; the server re-checks every request.

### D3. Authorization model (the prerequisite for everything else)

- Introduce `RoleAssignment(user, role, scope_type ∈ {institution, school,
  department, committee, scholar}, scope_id, valid_from, valid_to)`,
  replacing global groups as the source of truth.
- Add one policy module (e.g. `can(actor, action, obj)`) called by **every**
  service command, with deny by default. Committee authority comes from
  `CommitteeMembership` plus tenure.
- Enforce separation of duties in approval chains: one person cannot decide
  two steps of the same request; the requester cannot approve their own request.
- Lock down the admin: workflow models become read-only there, or route
  through services in `save_model`; access limited to the R&D office and super-admins.

### D4. Domain additions required before or alongside the UI

| Item | Approach |
|---|---|
| 42 milestones | `MilestoneDefinition` (code, name, phase, order, prerequisites, deadline rule, responsible roles, approval chain, form template) as **configuration data**, plus a per-scholar `ScholarMilestone` (state, dates, evidence). The 10 phases become groupings of milestones, and the existing gates are re-expressed as milestone completions. **Needs the official list of 42.** |
| Versioned rules | Move `policy.py` values to `RuleSet(version, effective_from, applies_to_cohorts)` + `RuleValue`, with Python defaults as the seed; rules resolve per scholar by admission cohort. |
| Exams | `ExamSession`, `ExamRegistration` (eligibility, deadline), marks entry with moderation, COE publication lock, grade card |
| Documents | Official templates → HTML → PDF (WeasyPrint); object storage; access checks; signature/verification IDs |
| Notifications | In-app inbox API + email via a queue; templates; wire in examiner escalations |
| Audit | One `AuditEvent` per command (actor, action, target, before/after, IP), plus history on key tables |
| Imports | CSV/XLSX importers for faculty, scholars, historic coursework and milestones: dry-run → validation report → commit; idempotent on PRN/employee ID |
| Reporting | Read-only SQL views + API + XLSX export for the listed reports |
| SSO | OIDC (e.g. `mozilla-django-oidc`) or SAML (`djangosaml2`) to the university IdP; just-in-time provisioning; role mapping stays in `RoleAssignment` |

### D5. Sequenced phases (each has an exit criterion; none has started)

| # | Phase | Exit criterion |
|---|---|---|
| 0 | Inspect the React repo; decide the backend option; obtain the 42-milestone list, answers to A11 and the audio requirements | Signed-off decisions recorded in this doc |
| 1 | **Harden the Django core, with no new features:** actor-aware policy on all services; fix defects A10.1–A10.9; PostgreSQL in CI; timezone-safe dates; secure settings | All services deny unauthorized actors (tests); admin cannot create invalid state; suite green on PostgreSQL |
| 2 | API layer + OpenAPI + `/me` + session/CSRF auth | Contract published; generated TS client builds |
| 3 | Connect React module by module (read-only screens first, then commands); remove mock data and any client-held secrets | Each React screen backed by the API; no business decision made only in the browser |
| 4 | Milestones (42) + versioned rules | All milestones tracked per scholar; rule values editable with effective dates |
| 5 | Missing modules: approval inbox, scholar self-submission, exams, documents/PDF, email notifications, imports, reports | Acceptance tests per module against the regulations |
| 6 | SSO, security review, performance test, data migration, UAT with the R&D office, go-live | UAT sign-off |

### D6. Risks

- **Regulation ambiguity:** the tests currently encode assumptions. Mitigation: A11 answers before Phase 4.
- **Model mismatch:** React mock data shapes may diverge from the Django models. Mitigation: the OpenAPI schema is the single contract, and the React types are generated.
- **Duplicated logic:** rules re-implemented in React drift over time. Mitigation: the server returns gate reasons and validation; the UI displays them.
- **Secret exposure:** if the AI Studio app calls Gemini from the browser, any AI feature must be proxied through the backend with policy checks and audit.
- **Scope:** 42 milestones × roles × forms is large. Mitigation: phase 3 connects what already exists before phase 5 adds more.

---

## Part E: Decisions needed from DYPIU

1. Provide the Google AI Studio repository (next step).
2. The official list of **42 PhD milestones** (with the source clause/appendix for each).
3. Answers to the 17 assumptions in A11 (or confirmation of the defaults).
4. The contents of the audio recording (a transcript or summary).
5. The team's maintenance language (Python / TypeScript) and hosting constraints (on-prem / cloud / mandated platform).
6. The identity provider for SSO (Azure AD / Google Workspace / other) and whether students and external examiners get accounts.
7. Whether the 1 July 2026 regulations apply to all current scholars or only to new cohorts.
