# Academic REST API (Step 5C)

Django REST Framework on top of the Academic services (`coursework/academic/*`). React is a presentation layer only;
identity, permissions and every academic outcome are decided here.

## Request path

`React → /api/... → SessionAuthentication (CSRF) → HasActivePerson → view → service authorization
(identity.authz, deny by default, audited) → validation → atomic write + audit → JSON`.

- **Authentication:** Django session. `GET /api/auth/csrf/`, `POST /api/auth/login/`, `POST /api/auth/logout/`.
  The login must already be linked to an active `identity.Person`, so no second identity is created.
  The identity provider (OIDC/SAML, decision D-IdP) is still open.
- **Identity:** `GET /identity/me/` returns the server-computed person, capabilities, workspaces and scope. The scope
  covers department, school, committees, supervised scholars and active teaching assignments. The client can't choose
  a person, role or capability: scholar endpoints (`/me/...`) carry no scholar id, and staff endpoints that take an id
  authorize that id on every request.
- **Errors:** JSON `{"code", "detail", "errors"?}` with no traceback. Codes:
  - 401 `not_authenticated`
  - 403 `permission_denied` / `csrf_failed` / `no_identity`
  - 400 `validation_error`
  - 404 `not_found`
  - 409 `conflict`
  - 500 `server_error`
- **Stale data:** transitions accept `expected_status` (plus `expected_step` for elective lists), and mark and
  attendance edits accept `expected_updated_at`. A mismatch returns 409.
- **`can` flags:** list responses include per-row `can` flags computed with the same policies the services enforce.
  They only decide which controls to *show*; the action is re-authorized when it is submitted.
- **Unresolved rules:** when an operation depends on an UNRESOLVED parameter, the service refuses it with 400 and the
  parameter key (for example `elective.approval_authority`, `third_attempt.mentor_required`, `revaluation.*`,
  `activity.scoring_scheme.*`). Read endpoints report the parameter status so the UI can explain the block.
- **Settings:**
  - `REST_FRAMEWORK` in `config/settings.py`;
  - `CSRF_FAILURE_VIEW` returns JSON;
  - `DJANGO_CSRF_TRUSTED_ORIGINS` (environment) for the SPA's public origin.
- **Development:** the Vite dev server proxies `/api` and `/identity` to Django (`DJANGO_API_ORIGIN`, default
  `http://127.0.0.1:8000`) with `changeOrigin: false`, so the browser, the session cookie and the CSRF check stay
  same-origin.

## Endpoints (`/api/academic/`)

| Method | Path | Who (server-enforced) |
|---|---|---|
| GET | `catalogue/` | any active person (courses, semesters) |
| GET | `rules/` | any active person (the rule register as applied) |
| POST | `rules/changes/` · `rules/changes/<id>/decide/` | propose: ACADEMIC_ADMIN · decide: DEAN_RND, not the proposer |
| GET | `me/dashboard/` `me/courses/` `me/attendance/` `me/results/` `me/exams/` `me/transcript/` | the signed-in scholar (own record) |
| GET/POST | `me/semester-registration/` `me/electives/` `me/activities/` `me/third-attempt/` `me/revaluations/` | the signed-in scholar |
| GET/POST | `me/offerings/` · `me/enrollments/` · `me/exam-registrations/` | the signed-in scholar |
| GET | `hall-tickets/<id>/` | the scholar, COE, R&D Cell, PhD Cell |
| GET | `teaching/assignments/` | faculty (own active assignments only) |
| GET | `offerings/<id>/` `…/roster/` `…/marks/` `…/results/` | Course Coordinator (whole offering), instructor (own sections), governance offices |
| GET/POST | `sections/<id>/sessions/` · `sessions/<id>/records/` | instructor of the section, Course Coordinator |
| POST | `marks/` | CA: instructor / coordinator · end-term & module: coordinator · activity: SDRC (school) |
| POST | `attempts/<id>/prepare-result/` | coordinator (course) / SDRC (activity) |
| GET | `sdrc/activities/` · POST `activity-submissions/<id>/review/` · `enrollments/<id>/open-evaluation/` | SDRC of the scholar's school |
| GET | `activity-submissions/<id>/document/` | the scholar, SDRC, record viewers |
| GET | `results/?stage=verify\|ratify` · `results/<id>/` | rows the person may act on / view |
| POST | `results/<id>/verify/` · `…/return/` · `…/ratify/` | R&D Cell (not the preparer) · R&D Cell / COE · COE (not preparer / verifier; provisional needs `acknowledge_provisional`) |
| GET | `scholars/` · `scholars/<id>/record/` · `scholars/<id>/transcript/` | `academic.record.view` / `academic.transcript.view` scope |
| POST | `scholars/<id>/fee-clearance/` · `…/semester-registration/` · `…/enrollments/` | PhD / R&D Cell (enrollment also ACADEMIC_ADMIN) |
| POST | `scholars/<id>/transcript/issue/` | COE |
| GET | `transcripts/verify/<code>/` | public (validity, PRN, version only) |
| GET/POST | `exam-cycles/` · POST `exams/` | read: any person · write: COE, ACADEMIC_ADMIN |
| GET | `exams/<id>/registrations/` | COE, ACADEMIC_ADMIN, R&D / PhD Cell |
| POST | `exam-registrations/<id>/hall-ticket/` · `hall-tickets/<id>/revoke/` | COE |
| GET/POST | `governance/offerings/` and sub-resources (`sections`, `assessments`, `assignments`), `governance/assignments/<id>/revoke/`, `governance/faculty/` | ACADEMIC_ADMIN, CISR (structure); ACADEMIC_ADMIN, DEPARTMENT / SCHOOL admin in scope (assignments) |
| GET | `third-attempt-cases/` · POST `…/review/` · `…/decide/` | `third_attempt.review_chain`: DEAN_RND review → VC decision; SoD |
| GET | `elective-proposals/` · POST `…/recommend/` · `…/decide/` | recommend: the scholar's supervisor · decide: `elective.approval_authority` (UNRESOLVED ⇒ blocked) |
| GET/POST | `elective-lists/` · POST `…/submit/` · `…/decide/` | prepare: DEPARTMENT / SCHOOL admin in scope · decide: `elective.list_approval_chain` step, within scope |
| GET | `question-papers/` · POST `exams/<id>/setters/` · `setter-appointments/<id>/received/` | appoint: `question_paper.setter_appointed_by` (DC, school scope) · receipt: COE |

Not exposed: revaluation *review* (the reviewer is UNRESOLVED), rule parameters with no key (these are added by
migration), and direct model CRUD.

## Tests

`coursework/test_academic_api.py` has 39 tests covering authentication (401 everywhere, CSRF, login without a
person), negative authorization and behaviour. The negative cases are:
- scholar A → scholar B;
- instructor A → instructor B's section;
- faculty with no assignment, or from another department;
- expired or revoked assignments;
- a revoked capability;
- department and school boundaries;
- SDRC across schools;
- unauthorized approvals (third attempt, elective, config, result chain);
- unauthorized result and transcript operations;
- browser-supplied role, capability or id (ignored or denied);
- an automated audit that every route requires authentication.
