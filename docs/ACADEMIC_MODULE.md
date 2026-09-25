# Academic module (Steps 5A–5B)

The Academic bounded context lives in the `coursework` Django app (app label kept to avoid a disruptive rename).
Every mutation goes through `coursework/academic/*` services — there is **no other mutation path** (5B removed
`coursework/services.py` and `phd_rules/grading.py`). Each service follows the same sequence:
1. Server-side authorization (`identity.authz`); a denial is audited and never rolled back.
2. Validation, including every rule parameter the operation depends on (UNRESOLVED ⇒ blocked).
3. The write, inside a transaction, plus an allowed audit event.

## 1. Entities

| Area | Entity | Where | Reused / new |
|---|---|---|---|
| Structure | University | `core.University` | **new** (School → University, nullable) |
| | School, Department | `core.School`, `core.Department` | reused |
| | AcademicYear, Semester | `coursework.AcademicYear`, `coursework.Semester` | **new**; `finance.Semester` (TA) now links to it |
| | Academic calendar | AcademicYear + Semester + ExamCycle + `deadlines.AcademicEvent` | composed (reused `AcademicEvent`) |
| Coursework | Course | `coursework.Course` | reused and extended (activity type, evaluation body, applicable categories, owning department, active) |
| | CourseOffering → Section → FacultySubjectAssignment | `coursework.*` | **new** |
| | ScholarCourseEnrollment | `coursework.ScholarCourseEnrollment` | **new** |
| | AttendanceSession, AttendanceRecord, AttendanceCorrection | `coursework.*` | **new** |
| | Research Orientation Seminar | `coursework.OrientationSeminar`, `OrientationAttendance` | **new** (attendance only, no credits) |
| | Assessment → MarkEntry (+ MarkEntryHistory) → Grade → CourseResult (+ ResultEvent) | `coursework.*` | **new**; `CourseResult` is per attempt with `is_current` / `supersedes` (5B, for revaluation); legacy `AssessmentComponent`/`ComponentScore` retained read-only |
| Examination | ExamCycle → Exam → ExamEligibility → ExamRegistration | `coursework.*` | **new** |
| | ExamAttempt | `coursework.CourseAttempt` (alias `ExamAttempt`) | reused and extended (enrollment, status, attempt 1–3 constraint) |
| | ThirdAttemptCase (+ events) | `coursework.ThirdAttemptCase` | **new**; the only third-attempt path (old DC→VC chain retired in 5B, data kept) |
| Electives | Elective | `Course(category=ELECTIVE)` | reused |
| | ElectiveProposal (+ events) | `coursework.ElectiveProposal` | **new** |
| Activities | ActivitySubmission | `coursework.ActivitySubmission` | **new** |
| Record | AcademicProfile, CourseworkStatus, AcademicStanding, Transcript | `coursework/academic/records.py` | **derived** from ratified results; never stored |
| Configuration | AcademicRuleParameter | `coursework.AcademicRuleParameter` | **new**; versioned, append-only; records `approved_by` + `change_request` (5B) |
| | ParameterChangeRequest | `coursework.ParameterChangeRequest` | **new (5B)**; maker-checker |
| Semester | FeeClearance, SemesterRegistration | `coursework.*` | **new (5B)** |
| Electives | ElectiveList → ElectiveListItem, ElectiveListDecision | `coursework.*` | **new (5B)**; department xor school list, staged approval |
| Exam administration | HallTicket | `coursework.HallTicket` | **new (5B)**; one live ticket per registration |
| | QuestionPaperSetterAppointment | `coursework.*` | **new (5B)**; appointment + receipt only, no paper content |
| Revaluation | RevaluationCase, RevaluationMark | `coursework.*` | **new (5B)**; original result never modified |
| Record | TranscriptIssue | `coursework.TranscriptIssue` | **new (5B)**; version + content hash + verification code only |

## 2. Regulatory rule register

16 CONFIRMED · 8 CONFIGURABLE · 7 AMBIGUOUS · 13 UNRESOLVED (44 keys; 13 added in 5B, marked *(5B)*).

Values change **only** by maker-checker: `config.propose_change` (ACADEMIC_ADMIN) → `config.decide_change`
(DEAN_RND, not the proposer). Approval creates a new dated version that records its approver and change request.
Every result and eligibility decision stores the parameter versions it used. Results computed with AMBIGUOUS
or UNRESOLVED parameters are `is_provisional`, and COE must pass an explicit acknowledgement to ratify them.
An operation that depends on an UNRESOLVED parameter is **blocked**; nothing is guessed.

| Key | Value | Status | Effect while not CONFIRMED | Source | Decision sheet |
|---|---|---|---|---|---|
| `coursework.structure.mandatory_taught_courses` | `3` | **CONFIRMED** | — | Academic source supplied by DYPIU (Step 5A) | — |
| `coursework.structure.mandatory_activities` | `3` | **CONFIRMED** | — | Academic source supplied by DYPIU (Step 5A) | — |
| `coursework.category_of_entry_qualification` | `{"BTECH": "I", "MTECH": "II", "PG": "III"}` | **CONFIGURABLE** | Institution may change via maker-checker | React journeyData.ts (Regulations July 2026 transcription) | RD-43 |
| `coursework.required_credits` | `{"I": 23, "II": 17, "III": 20}` | **CONFIGURABLE** | Institution may change via maker-checker | React journeyData.ts (Regulations July 2026 transcription) | RD-C1 |
| `coursework.electives_required` | `{"I": 3, "II": 1, "III": 2}` | **AMBIGUOUS** | Completion provisional | React journeyData.ts (Regulations July 2026 transcription) | RD-08 |
| `coursework.min_gpa` | `"6.00"` | **CONFIRMED** | — | Academic source supplied by DYPIU (Step 5A) | RD-C3 |
| `coursework.course_pass_min_marks` | `"40"` | **CONFIRMED** | — | Academic source supplied by DYPIU (Step 5A): minimum 40 marks (D) | RD-01 |
| `coursework.course_pass_min_applies_to` | `"COURSE"` | **AMBIGUOUS** | Results provisional | Academic source supplied by DYPIU (Step 5A): 'each course component' may mean each course or each assessment component | RD-01 |
| `coursework.continuation_min_grade` | `"C+"` | **CONFIRMED** | — | Academic source supplied by DYPIU (Step 5A) | RD-02 |
| `coursework.ethics_module_required` | `true` | **CONFIRMED** | — | Academic source supplied by DYPIU (Step 5A): Research Methodology & Ethics has a mandatory ethics/plagiarism module | — |
| `grading.scheme` | `{"mode": "ABSOLUTE", "rounding": "HALF_UP_TO_INTEGER", "bands": [["…` | **AMBIGUOUS** | Results provisional; ratification needs explicit acknowledgement | Requirements brief (Django docs/REQUIREMENTS.md) (Implementation Guidelines bands); React states relative grading | RD-01, RD-02, RD-40 |
| `assessment.default_weights` | `{"CONTINUOUS": 50, "END_TERM": 50}` | **CONFIRMED** | — | Academic source supplied by DYPIU (Step 5A): 50% Continuous Assessment, 50% End-term | — |
| `assessment.elective_reweighting_limit_percent` | `10` | **CONFIGURABLE** | Institution may change via maker-checker | Requirements brief (Django docs/REQUIREMENTS.md): adaptable ±10% weighting template for electives | RD-37 |
| `assessment.elective_reweighting_approver` | `null` | **UNRESOLVED** | Elective re-weighting blocked | Not stated | RD-37, RD-38 |
| `attendance.physical_only` | `true` | **CONFIRMED** | — | Academic source supplied by DYPIU (Step 5A): physical attendance requirement | — |
| `attendance.min_percent` | `{"FT": 75, "PT": null}` | **AMBIGUOUS** | Part-time (PT = null): exam eligibility blocked with a reason | React journeyData.ts (Regulations July 2026 transcription): full-time 'min 75% attendance'; basis (per course/overall) and part-time rule not stated | RD-26 |
| `exam.cycles` | `["JAN_JUN", "JUL_DEC"]` | **CONFIRMED** | — | Academic source supplied by DYPIU (Step 5A): January–June and July–December cycles | RD-35 |
| `exam.max_regular_attempts` | `2` | **CONFIRMED** | — | Academic source supplied by DYPIU (Step 5A) | RD-C3 |
| `exam.max_attempts_with_approval` | `3` | **CONFIRMED** | — | Academic source supplied by DYPIU (Step 5A): third (final) attempt case | RD-16 |
| `exam.attempt_window_years` | `2` | **CONFIRMED** | — | React journeyData.ts (Regulations July 2026 transcription): within 2 years of enrolment | RD-C3 |
| `exam.attempt_window_start` | `"ENROLMENT"` | **AMBIGUOUS** | Standing provisional | React M12 'of enrolment' vs Django legacy 'first attempt' | RD-C3 |
| `exam.absent_counts_as_attempt` | `null` | **UNRESOLVED** | Standing DECISION_REQUIRED when an absent attempt exists | Not stated | — |
| `exam.reattempt_carries_continuous_assessment` | `null` | **UNRESOLVED** | Result preparation for attempt 2/3 blocked | Not stated | — |
| `exam.requires_fee_clearance` | `true` | **CONFIGURABLE** | Institution may change via maker-checker | React journeyData.ts (Regulations July 2026 transcription) M10: no admission to the exam unless tuition fees and hostel/library dues are cleared | — |
| `third_attempt.review_chain` | `["DEAN_RND", "VC_OPERATOR"]` | **CONFIRMED** | — | Academic source supplied by DYPIU (Step 5A): Dean R&D review followed by VC approval | RD-16 |
| `third_attempt.mentor_required` | `null` | **UNRESOLVED** | VC approval blocked | React M12 mentions a weekly-tracking mentor; not stated in the Step 5A source | RD-16 |
| `elective.approval_authority` | `null` | **UNRESOLVED** | Elective decision blocked | React M9.1 names 'DC / Dean of Research'; approving capability not confirmed | RD-38 |
| `elective.supervisor_recommendation_required` | `null` | **UNRESOLVED** | Elective decision blocked | Academic source supplied by DYPIU (Step 5A): 'where required' | RD-38 |
| `activity.required_artefacts` | `{"INDUSTRIAL_TRAINING": ["PLAN", "LOGBOOK", "REPORT", "POSTER"], "C…` | **CONFIRMED** | — | Academic source supplied by DYPIU (Step 5A) | RD-37 |
| `activity.artefacts_in_order` | `{"INDUSTRIAL_TRAINING": true}` | **CONFIRMED** | — | Academic source supplied by DYPIU (Step 5A): plan → logbook → report → poster flow | — |
| `result.custody_chain` | `["PREPARE", "VERIFY:RND_CELL_OPERATOR", "RATIFY:COE_OPERATOR"]` | **CONFIRMED** | — | Academic source supplied by DYPIU (Step 5A): Course Coordinator / SDRC → R&D Cell → COE | — |
| `result.absence_policy` *(5B)* | `null` | **UNRESOLVED** | Result preparation blocked when any component is marked absent | Not stated. The only documented notion is the special grade AB (Brief §5) | — |
| `attendance.basis` *(5B)* | `"PER_COURSE"` | **AMBIGUOUS** | Eligibility computed per course; result provisional | React journeyData.ts (Regulations July 2026 transcription): 'min 75% attendance' — per course or overall is not stated | RD-26 |
| `semester_registration.required` *(5B)* | `true` | **CONFIGURABLE** | Institution may change via maker-checker | React journeyData.ts (Regulations July 2026 transcription) M7: semester course registration every semester | — |
| `semester_registration.requires_fee_clearance` *(5B)* | `true` | **CONFIGURABLE** | Institution may change via maker-checker | React journeyData.ts (Regulations July 2026 transcription) M7: prescribed fees paid every semester until thesis submission | — |
| `elective.list_approval_chain` *(5B)* | `["DC_MEMBER", "DEAN_RND"]` | **AMBIGUOUS** | Chain snapshotted on submission; UNRESOLVED would block submission | React journeyData.ts (Regulations July 2026 transcription) M9.1: 'Elective list approved jointly by DC and Dean of Research' (order not stated) | RD-38 |
| `elective.proposal_requires_approved_list` *(5B)* | `true` | **CONFIGURABLE** | Institution may change via maker-checker | React journeyData.ts (Regulations July 2026 transcription) M9.1: electives chosen from the approved list | RD-38 |
| `question_paper.setter_appointed_by` *(5B)* | `["DC_MEMBER"]` | **CONFIGURABLE** | Institution may change via maker-checker | React journeyData.ts (Regulations July 2026 transcription) M11: 'DC appoints experts to set the question paper' | — |
| `revaluation.enabled` *(5B)* | `null` | **UNRESOLVED** | Revaluation request and review blocked | No revaluation provision in the supplied sources | — |
| `revaluation.request_window_days` *(5B)* | `null` | **UNRESOLVED** | Revaluation request and review blocked | Not stated | — |
| `revaluation.reviewer` *(5B)* | `null` | **UNRESOLVED** | Revaluation request and review blocked | Not stated | — |
| `activity.scoring_scheme.INDUSTRIAL_TRAINING` *(5B)* | `null` | **UNRESOLVED** | Activity assessment scheme cannot be defined | Requirements brief (Django docs/REQUIREMENTS.md): '5-component rubric incl. poster' — components and weights not given | RD-37 |
| `activity.scoring_scheme.CONFERENCE_WORKSHOP` *(5B)* | `null` | **UNRESOLVED** | Activity assessment scheme cannot be defined | Requirements brief (Django docs/REQUIREMENTS.md): 'points-accumulation matrix + reflective-note gate' — matrix not given | RD-37 |
| `activity.scoring_scheme.RESEARCH_SEMINAR` *(5B)* | `null` | **UNRESOLVED** | Activity assessment scheme cannot be defined | Requirements brief (Django docs/REQUIREMENTS.md): '3-criteria rubric' — criteria and weights not given | RD-37 |

### Unsourced assumptions removed in 5B

| Former behaviour | Now |
|---|---|
| An absent component produced outcome ABSENT | `result.absence_policy` UNRESOLVED ⇒ preparation blocked when a component is absent |
| Continuous Assessment silently carried into a re-attempt | blocked until `exam.reattempt_carries_continuous_assessment` is decided |
| Part-time scholars checked against the full-time 75% | blocked with a reason while `attendance.min_percent.PT` is null |
| Attendance basis implied | `attendance.basis` = PER_COURSE, AMBIGUOUS (provisional) |
| VC could approve a third attempt without the mentor rule | blocked while `third_attempt.mentor_required` is UNRESOLVED |
| Built-in activity rubrics / caller-supplied weights | only `activity.scoring_scheme.<TYPE>`; UNRESOLVED ⇒ blocked |
| Elective re-weighting by ACADEMIC_ADMIN | only the `assessment.elective_reweighting_approver` capability; UNRESOLVED ⇒ blocked |
| Elective decision without the recommendation rule | blocked while `elective.supervisor_recommendation_required` is UNRESOLVED |
| Pass/grade constants in `phd_rules.policy` / `phd_rules.grading` | removed; the lifecycle gate reads `records.coursework_status` |
| Provisional results ratified silently | explicit `acknowledge_provisional=True`, recorded on the result event |

## 3. Academic permissions (server-side)

Relationship rules are institutional facts and are not provisional: assigned faculty only, own records only, own supervisor only.
Who holds the approving authority is **provisional (RD-38)** unless the Step 5A source states it.

| Function | Allowed |
|---|---|
| Configuration change | propose: ACADEMIC_ADMIN · approve: DEAN_RND (not the proposer) |
| Fee clearance | PHD_CELL_OPERATOR, RND_CELL_OPERATOR |
| Semester registration | the scholar (own record); PHD_CELL_OPERATOR, RND_CELL_OPERATOR |
| Elective list | prepare/submit: DEPARTMENT_ADMIN / SCHOOL_ADMIN in scope · decide: each capability of `elective.list_approval_chain` in turn, within scope; no person twice; not the preparer |
| Hall ticket | issue / revoke: COE_OPERATOR · view: the scholar, COE, R&D Cell, PhD Cell |
| Question-paper setter | appoint: `question_paper.setter_appointed_by` (DC_MEMBER, within school) · record receipt: COE_OPERATOR |
| Revaluation | request: the scholar, PHD/RND Cell · review: `revaluation.reviewer` (**UNRESOLVED**, blocked); never anyone in the original result's chain |
| Activity scoring scheme | ACADEMIC_ADMIN, CISR_OPERATOR, SDRC (in scope) — from `activity.scoring_scheme.<TYPE>` only |
| Transcript issue | COE_OPERATOR |
| Courses, years, semesters, offerings, sections, assessment schemes | ACADEMIC_ADMIN, CISR_OPERATOR |
| Faculty teaching assignments | ACADEMIC_ADMIN; DEPARTMENT_ADMIN / SCHOOL_ADMIN within scope |
| Enrollment | the scholar (own record); RND_CELL_OPERATOR, PHD_CELL_OPERATOR, ACADEMIC_ADMIN |
| Attendance | instructor of the section; Course Coordinator of the offering (active assignment only) |
| Continuous Assessment marks | instructor of the section; Course Coordinator |
| End-term marks, mandatory module | Course Coordinator |
| Activity marks, artefact review, activity result | SDRC member (scholar's school) |
| Prepare course result | Course Coordinator |
| Verify result | RND_CELL_OPERATOR (not the preparer) |
| Ratify result | COE_OPERATOR (not the preparer or verifier) |
| Return result | RND_CELL_OPERATOR, COE_OPERATOR (not after ratification) |
| Exam cycles and exams | COE_OPERATOR, ACADEMIC_ADMIN |
| Exam registration | the scholar (own record); COE_OPERATOR, RND_CELL_OPERATOR, PHD_CELL_OPERATOR |
| Third-attempt case | submit: the scholar, PHD/RND Cell · review: `third_attempt.review_chain[0]` (DEAN_RND) · decide: `[1]` (VC_OPERATOR) |
| Elective proposal | propose: the scholar (course must be on an APPROVED list) · recommend: the scholar's supervisor · decide: `elective.approval_authority` (**UNRESOLVED**, blocked); also blocked while `elective.supervisor_recommendation_required` is UNRESOLVED |
| Academic record / results (read) | the scholar (ratified results only), supervisor, assigned faculty, dept/school admin in scope, SDRC/DC in scope, academic oversight offices |
| Transcript | the scholar, supervisor, dept/school admin in scope, academic oversight offices |
| SYSTEM_ADMIN, superuser, VC (except third attempt) | **no academic access** |

## 4. Chain of custody

| Record | Prepared / evaluated by | Verified / approved by | Final authority | Stored as |
|---|---|---|---|---|
| Coursework result (taught, elective) | Course Coordinator | R&D Cell | COE | `CourseResult.prepared_by/verified_by/ratified_by` + `ResultEvent` |
| Coursework result (activity) | SDRC | R&D Cell | COE | same |
| Third-attempt case | scholar / PhD or R&D Cell | Dean R&D | VC | `ThirdAttemptCase.dean_by/vc_by` + events |
| Elective proposal | scholar | supervisor (recommendation) | configured authority (UNRESOLVED) | `ElectiveProposal` + events |
| Rule parameter | ACADEMIC_ADMIN (proposal) | DEAN_RND (approval; not the proposer) | new version on approval | `ParameterChangeRequest` + `AcademicRuleParameter.approved_by` + audit |
| Elective list | Department / School admin | chain capabilities in order | last step → APPROVED | `ElectiveListDecision` (append-only) |
| Revaluation | reviewer (`revaluation.reviewer`) | R&D Cell | COE ratifies → revised result becomes current | `RevaluationCase`, `RevaluationMark`, revised `CourseResult.supersedes` |
| Transcript | derived | — | COE issues | `TranscriptIssue` (hash + code; append-only) |

Research chains (supervisor assignment → DC, TAC → DC, proposal → SDRC/DC) are out of scope for Steps 5A–5B.

## 5. Legacy cleanup (5B)

| Legacy item | Action | Data |
|---|---|---|
| `coursework/services.py` (`register_attempt`, `record_result`, `update_coursework_status`) | **deleted** | — |
| `phd_rules/grading.py`, academic constants in `phd_rules/policy.py` | **deleted** (only `COURSEWORK_MONTHS`, the RD-06 scheduling clock, remains) | — |
| `lifecycle.engine._coursework` | now calls `coursework.academic.records.coursework_status` | — |
| `CourseAttempt` rows with status LEGACY | kept, **not counted** (completion reports them as a reason) | listed by `manage.py academic_legacy_report` |
| `COURSEWORK_THIRD_ATTEMPT` DC→VC chain | renamed `SUPERSEDED_COURSEWORK_THIRD_ATTEMPT` (core 0004); no longer seeded | requests kept and listed by the report |
| `AssessmentComponent`, `ComponentScore`, `CourseAttempt.override` | read-only (admin is read-only) | kept |

## 6. Operations added in 5B

- **Semester registration:** a window on `Semester`, active scholar, semester not ended, and fee clearance when
  `semester_registration.requires_fee_clearance`. Enrollment and exam eligibility require it when
  `semester_registration.required`.
- **Elective lists:** department or school list → submit (snapshot of `elective.list_approval_chain`) → staged
  decisions. Returning a list requires remarks; the same person cannot decide twice. Scholars may propose only
  courses on an APPROVED list for that semester.
- **Hall tickets:** COE issues one only for an active, eligible registration. The number is a system identifier
  (official format UNRESOLVED) plus a random verification code. No signature or seal is generated.
- **Question papers:** setter appointment by the configured authority with a basis reference, then receipt
  recorded by COE. No paper content is stored.
- **Revaluation:** fully blocked until `revaluation.enabled` / `request_window_days` / `reviewer` are decided.
  The original result and raw marks are never modified. The revised result supersedes only after R&D Cell
  verification and COE ratification; if it is returned, the case is REJECTED and the original stays current.
- **Transcript:** `transcript_data` is deterministic (format version 1) and derived from current ratified
  results; LEGACY rows are excluded and counted. `issue_transcript` (COE) stores version, SHA-256 content hash
  and verification code only. `verify_transcript(code)` reports validity, whether the records still match, and
  any superseding version.

## 7. Integrity

Database constraints: `CourseResult` (custody order, status consistency, one current result per attempt);
`CourseAttempt` (attempt 1–3, marks 0–100); `ExamRegistration` (a third attempt needs a case);
`ThirdAttemptCase` (VC after Dean); `ParameterChangeRequest` (one pending per key, decision consistency);
`Semester` (dates, registration window); `FeeClearance` / `SemesterRegistration` (uniqueness);
`ElectiveList` (exactly one owner); `HallTicket` (one live per registration); `RevaluationCase` (one open per
result); `TranscriptIssue` (unique version). A ratified `CourseResult`, `RevaluationMark` and `TranscriptIssue`
refuse update and delete at model level. All writes are atomic, and authorization denials are audited outside
the transaction. Verified on SQLite and on PostgreSQL 16 (full suite, migrate from zero, reverse and re-apply).

## 8. React academic UI (5C)

The React client is connected to this module through the REST API described in `docs/ACADEMIC_API.md`. The Academic
workspaces (`/scholar/academic`, `/faculty`, `/sdrc`, `/cisr`, `/rnd-cell`, `/phd-cell`, `/coe`, `/academic-admin`,
`/dean-rd`, `/vc`, `/dc`, `/department`, `/school`) read identity from `GET /identity/me/` and data from
`/api/academic/`. The browser holds no academic state and no identity selector. See `docs/ACADEMIC_UI_AUDIT.md` in
the React repository for the route inventory.
