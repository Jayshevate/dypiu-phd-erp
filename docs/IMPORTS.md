# Smart institutional data import (Step A3)

App: `imports/`. UI: `/academic-admin/import` and `/phd-cell/import` (React `src/academic/pages/imports.tsx`).

## Workflow

```
upload → file inspection (type, size, SHA-256) → sheet + header-row detection → column mapping
(deterministic, optionally AI-suggested) → administrator confirms the mapping → institutional value matching
→ deterministic validation → duplicate / conflict detection → relationship + authorization validation
(dry run through the real services, always rolled back) → preview → explicit confirmation → atomic commit
→ import report → audit trail
```

| Step | Endpoint | What happens |
|---|---|---|
| Analyse | `POST /api/academic/imports/analyze/` | Reads the file, lists sheets, detects the header row (title rows above it are skipped), suggests a field for each column. Nothing is stored. |
| Preview | `POST /api/academic/imports/preview/` | Takes the confirmed mapping and the provisioning option. Applies every row through the domain services inside a transaction that is **always rolled back**, then stores an `ImportBatch` with the per-row report. |
| Commit | `POST /api/academic/imports/<id>/commit/` | The browser re-sends the same file (SHA-256 must match). The same pass runs again in **one transaction**. If any row's outcome differs from the preview (the data changed), nothing is written and the answer is 409. |
| Report | `GET /api/academic/imports/`, `GET …/<id>/` | History and per-row report |
| Templates | `GET …/templates/<type>/?kind=xlsx|csv` | Data sheet with the header row only; an Instructions sheet documents each field |
| Activation links | `POST …/<id>/activation-links/` | CSV of one-time links for the logins this import created (`no-store`, formula-safe cells) |

## Import policy

- Each row is one of **VALID, DUPLICATE, INVALID, AMBIGUOUS, MISSING, CONFLICT**, with the exact reason.
- **The commit is all-or-nothing.**
  - It is refused while any INVALID, AMBIGUOUS, MISSING or CONFLICT row exists, unless the administrator explicitly
    chooses to skip those rows. The skip is recorded; nothing from a skipped row is written.
  - DUPLICATE rows are always skipped, so importing the same file twice creates nothing new.
  - An unexpected error rolls the whole import back. No half-created person, login or record remains.
- **Only additions are supported.** Existing records are never updated or merged. A stable identifier that exists
  with different details is a CONFLICT.
- Only the person who previewed a batch may commit it. A committed batch is immutable: model guards plus a database
  check constraint on its commit fields.

## Import types and stable identifiers

| Type | Key | Service (same as the single-record screen) | Batch authority |
|---|---|---|---|
| Schools and departments | department code (school code) | `institution.create_school` / `create_department` | `institution.structure.manage` |
| Course catalogue | course code | `structure.create_course` | `academic.structure.manage` |
| Faculty records | institutional e-mail | `institution.create_faculty_record` (+ provisioning) | `faculty.record.manage` |
| Scholar records | PRN (and e-mail) | `institution.create_scholar_record` (+ provisioning) | `scholar.edit_record` |
| Course enrollments | PRN + course + year + term | `enrollment.enroll` (every enrollment rule applies) | `academic.enrollment.manage` |
| Faculty assignments | e-mail + course + year + term + role + section | `structure.assign_faculty` | `academic.faculty_assignment.manage` |

Display names are never used alone as identity keys.

## Validation

| Kind | Rule |
|---|---|
| Required fields | Blank → MISSING |
| E-mail | Must be a valid address (stored lower-case) |
| Dates | `YYYY-MM-DD`, or `DD-MM-YYYY` / `DD/MM/YYYY` / `DD.MM.YYYY` (day first, as documented in the template). Excel date cells are read as dates. Anything else → INVALID. |
| Numbers / Yes-No | Whole numbers within the field's range; Yes/No/Y/N/True/False/1/0 |
| Allowed values | The model's own codes or labels, case and punctuation insensitive, plus a few explicit aliases (e.g. `Full-time` → `FT`). A spelling matching two values → AMBIGUOUS. No fuzzy guessing. |
| Departments | Code, or exact name. A name matching several departments → AMBIGUOUS (the codes are listed). No match → INVALID, with close names offered only as a hint. |
| Other references | Existing course code, scholar PRN, faculty e-mail, university code, academic year + term, offering, section |
| Duplicates | Repeated key in the file: DUPLICATE if identical, CONFLICT if different. Existing record: DUPLICATE if identical, CONFLICT otherwise. |
| Domain rules and constraints | Enforced by the services and the database during the dry run (e.g. enrollment rules, one active coordinator). Failures are INVALID / CONFLICT with the service's message. |
| Authorization | Batch: the actor must hold a capability named by the type's policy. Row: the service authorizes the specific record, so a department admin can only affect records in scope. Refused rows are INVALID ("Not authorized: …"), with a durable `imports.<type>.row_denied` audit event. |

**Limits:** `IMPORT_MAX_BYTES` (5 MB), `IMPORT_MAX_ROWS` (5000), `IMPORT_MAX_COLUMNS` (100), and
`IMPORT_MAX_EXPANDED_BYTES` (100 MB uncompressed, which guards against .xlsx zip bombs).

**Malformed input:** damaged workbooks, binary data in a CSV, empty files, files without data rows and unsupported
extensions are rejected with a message, never a traceback. Formulas are not evaluated and macros never run
(openpyxl `data_only`, xlrd read-only).

## Bulk account provisioning

For faculty and scholar imports, the administrator chooses one of:
- records only;
- also the institutional **person**;
- also a **login awaiting activation**.

Provisioning:
- Needs `identity.person.manage` (checked for the whole batch).
- Uses `identity.provisioning.provision_for_record`: the e-mail is the key, existing persons are never merged, and no
  password is ever set.
- Never grants a capability from the spreadsheet. The Faculty / Scholar workspace is derived by the server from the
  linked record. Columns such as "Role" or "Capability" have no field to map to.

After the commit, the activation-links CSV gives each new login a single-use, expiring link. The link is not stored;
issuing it is audited without the token. Delivery is manual until e-mail delivery / D-IdP is decided.

## AI-assisted mapping (`imports/mapping.py`)

- **Deterministic mapper (always on):** normalised header = field name, label or a listed alias → MAPPED.
  - Near misses (≥ 0.85 similarity, one clear winner) → SUGGESTED.
  - Two fields equally close, or two columns claiming one field → AMBIGUOUS. Neither is chosen.
- **`AIImportMapper`** is a provider interface. It is enabled only by `IMPORT_AI_MAPPER` (dotted path); the default is
  empty, so it is off and nothing is sent anywhere.
  - A provider receives **only header texts and field definitions, never cell values**.
  - Its suggestions are accepted only for real fields that are still free.
  - They are always marked SUGGESTED, and the administrator must tick "I have reviewed the suggested mappings" before
    validation.
  - If the provider is missing, misconfigured, rate limited or raises, deterministic mapping stands and the response
    says so.
- No commercial provider is wired in. Tests use deterministic mock providers.

## Audit

- `imports.<type>.preview`: batch id, file SHA-256, row counts.
- `imports.<type>.commit`: batch id, file SHA-256, created / skipped / provisioned. Its id is stored on the batch as
  the correlation reference.
- Every created record also has its own service audit event (e.g. `scholar.edit_record.create`,
  `identity.person.create`).
- Denials: `imports.<type>` (batch gate), `identity.person.manage` (provisioning), `imports.<type>.row_denied`.
- `ImportBatch` stores:
  - actor, time, file name / type / size / SHA-256, sheet, header row;
  - confirmed mapping and how each column was mapped;
  - options, per-row report and counts;
  - created records, provisioned persons, committer, commit time.

  It never stores the file itself, passwords or activation tokens.

## Dependencies

- `openpyxl` (MIT) reads .xlsx and writes templates.
- `xlrd` 2.x (BSD) reads legacy .xls only.
- Both are approved in Step A3. The committed test fixture `imports/fixtures/scholars_legacy.xls` was generated once
  with `xlwt` outside the project; `xlwt` is not a dependency.

## Unresolved decisions that affect imports

- RD-38: the authorities for structure / faculty records (provisional: Academic Admin).
- RD-23: official course codes.
- RD-43: the coursework category of each entry qualification (applied by the enrollment rules).
- D-IdP: whether provisioned logins will use local passwords at all.
