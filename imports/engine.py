"""Import workflow (Step A3): analyse → confirm mapping → preview → commit.

- Authorization is the SAME as for individual operations. The batch is gated on
  the actor holding a capability named by the import type's policy (scoped
  holders such as department admins pass the gate), and every row is then
  applied through the domain service, which authorizes that specific record.
  Provisioning identities additionally needs `identity.person.manage`.
- Preview applies every row inside a transaction that is always rolled back,
  so the validation is exactly what a commit would do (database constraints,
  domain rules, authorization), and nothing is written.
- Commit re-reads the same file (checked by SHA-256), re-runs the identical
  pass inside ONE transaction and aborts, writing nothing, if any row's outcome
  differs from the preview (the data changed in between). Rows that were not
  VALID in the preview are skipped only when the administrator explicitly
  chooses to; otherwise the commit is refused. Any unexpected failure rolls
  the whole import back: no half-created person, login or record remains.
- Every preview and commit is audited; the batch record is immutable once
  committed."""
from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from coursework.academic.common import deny
from identity import audit, provisioning
from identity.authz import POLICIES, holds, require

from . import mapping as mapping_mod
from .models import ImportBatch
from .readers import cell_text, read
from .types import AMBIGUOUS, BLOCKING, CONFLICT, DUPLICATE, INVALID, MISSING, STATUSES, TYPES, VALID, RowProblem, \
    fingerprint, parse_value

PROVISION_MODES = ("none", "person", "login")


class StalePreview(Exception):
    """The data changed between preview and commit."""


class _DryRun(Exception):
    pass


def import_type(key):
    try:
        return TYPES[key]
    except KeyError:
        raise ValidationError(f"Unknown import type {key!r}")


def may_run(actor, itype) -> bool:
    caps = {r.capability for r in POLICIES[itype.action].rules if r.relationship is None}
    return any(holds(actor, c) for c in caps)


def gate(actor, itype, options=None, *, request=None):
    """Batch-level authorization (audited on denial)."""
    if not may_run(actor, itype):
        deny(actor, f"imports.{itype.key}", f"No capability for {itype.label} imports ({itype.action})",
             request=request)
    if options and options.get("provision", "none") != "none":
        require(actor, "identity.person.manage", None, request=request)


def _options(itype, raw) -> dict:
    mode = (raw or {}).get("provision", "none") or "none"
    if mode not in PROVISION_MODES:
        raise ValidationError(f"provision must be one of {', '.join(PROVISION_MODES)}")
    if mode != "none" and not itype.provisionable:
        raise ValidationError(f"{itype.label} imports do not provision identities")
    return {"provision": mode}


@dataclass
class Table:
    book: object
    sheet: str
    header_index: int          # 0-based
    headers: dict              # column → header text
    rows: list                 # [(sheet row number, cells)]


def _table(itype, uploaded, sheet=None, header_row=None) -> Table:
    book = read(uploaded)
    sh = book.sheet(sheet)
    if header_row:
        if not 1 <= int(header_row) <= len(sh.rows):
            raise ValidationError(f"Header row {header_row} is outside the sheet")
        idx = int(header_row) - 1
    else:
        idx = mapping_mod.detect_header_row(itype, sh.rows)
    headers = {i: cell_text(c) for i, c in enumerate(sh.rows[idx]) if cell_text(c)}
    if not headers:
        raise ValidationError("The header row is empty")
    rows = [(n + 1, r) for n, r in enumerate(sh.rows) if n > idx and any(cell_text(c) for c in r)]
    if not rows:
        raise ValidationError("There are no data rows below the header")
    from .readers import limit
    if len(rows) > limit("IMPORT_MAX_ROWS", 5000):
        raise ValidationError(f"The sheet has {len(rows)} data rows; the limit is {limit('IMPORT_MAX_ROWS', 5000)}")
    return Table(book=book, sheet=sh.name, header_index=idx, headers=headers, rows=rows)


def analyze(actor, key, uploaded, *, sheet=None, header_row=None, request=None) -> dict:
    itype = import_type(key)
    gate(actor, itype, request=request)
    t = _table(itype, uploaded, sheet, header_row)
    suggestions, ai = mapping_mod.suggest(itype, t.headers)
    mapped = {s.field for s in suggestions.values() if s.field}
    return {
        "import_type": itype.as_dict(),
        "file": {"name": uploaded.name, "type": t.book.file_type, "size": t.book.size, "sha256": t.book.sha256},
        "sheets": [s.name for s in t.book.sheets], "sheet": t.sheet, "header_row": t.header_index + 1,
        "rows": len(t.rows),
        "columns": [suggestions[c].as_dict() for c in sorted(suggestions)],
        "sample": [[cell_text(c) for c in cells] for _, cells in t.rows[:5]],
        "missing_required": [f.name for f in itype.fields if f.required and f.name not in mapped],
        "ai": ai,
    }


def confirmed_mapping(itype, raw) -> dict[int, str]:
    """The administrator's confirmed mapping {column: field}. Validated, never inferred."""
    if not isinstance(raw, dict):
        raise ValidationError("A confirmed column mapping is required")
    names = {f.name for f in itype.fields}
    out, used = {}, {}
    for col, fname in raw.items():
        if fname in (None, ""):
            continue
        try:
            col = int(col)
        except (TypeError, ValueError):
            raise ValidationError(f"Invalid column {col!r}")
        if fname not in names:
            raise ValidationError(f"Unknown field {fname!r} for {itype.label}")
        if fname in used:
            raise ValidationError(f"Field {fname!r} is mapped to more than one column")
        used[fname] = col
        out[col] = fname
    missing = [f.label for f in itype.fields if f.required and f.name not in used]
    if missing:
        raise ValidationError(f"Required fields are not mapped: {', '.join(missing)}")
    return out


def _worst(problems):
    for status in (MISSING, INVALID, AMBIGUOUS):
        if any(p.status == status for p in problems):
            return status
    return INVALID


def _process(actor, itype, table, mapping, options):
    """Apply every row (each in its own savepoint). Caller decides commit / rollback."""
    report, refs, persons, denied = [], [], [], []
    seen = {}
    by_field = {f: c for c, f in mapping.items()}
    for row_no, cells in table.rows:
        entry = {"row": row_no, "status": VALID, "key": "", "reasons": []}
        report.append(entry)
        values, problems = {}, []
        for spec in itype.fields:
            col = by_field.get(spec.name)
            raw = cells[col] if col is not None and col < len(cells) else None
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                if spec.required:
                    problems.append(RowProblem(MISSING, f"{spec.label} is required"))
                values[spec.name] = None
                continue
            try:
                values[spec.name] = parse_value(spec, raw)
            except RowProblem as p:
                problems.append(p)
        if problems:
            entry["status"], entry["reasons"] = _worst(problems), [p.reason for p in problems]
            continue
        try:
            itype.resolve(values)
            entry["key"] = itype.describe_key(values)
            key = itype.natural_key(values)
            fp = tuple(fingerprint(values[f.name]) for f in itype.fields)
            if key in seen:
                first_row, first_fp = seen[key]
                if first_fp == fp:
                    raise RowProblem(DUPLICATE, f"Repeats row {first_row}")
                raise RowProblem(CONFLICT, f"Same key as row {first_row} but with different values")
            seen[key] = (row_no, fp)
            itype.existing(values)
        except RowProblem as p:
            entry["status"], entry["reasons"] = p.status, [p.reason]
            continue
        sid = transaction.savepoint()
        try:
            created, provisioned = itype.apply(actor, values, options)
        except PermissionDenied as e:
            transaction.savepoint_rollback(sid)
            entry["status"], entry["reasons"] = INVALID, [f"Not authorized: {e}"]
            denied.append(row_no)
            continue
        except ValidationError as e:
            transaction.savepoint_rollback(sid)
            entry["status"], entry["reasons"] = INVALID, list(getattr(e, "messages", [str(e)]))
            continue
        except IntegrityError as e:
            transaction.savepoint_rollback(sid)
            entry["status"], entry["reasons"] = CONFLICT, [f"Rejected by a database constraint: {e}"]
            continue
        transaction.savepoint_commit(sid)
        refs.extend(created)
        persons.extend(provisioned)
    counts = {s: sum(1 for r in report if r["status"] == s) for s in STATUSES}
    return report, counts, refs, persons, denied


def _record_denials(actor, itype, denied, request):
    # Row-level denials were audited inside rolled-back savepoints; keep a durable record of them.
    if denied:
        audit.record(action=f"imports.{itype.key}.row_denied", allowed=False, actor=actor, request=request,
                     reasons=[f"rows {', '.join(map(str, denied[:50]))}{'…' if len(denied) > 50 else ''} "
                              "were refused by the domain authorization"])


def preview(actor, key, uploaded, *, mapping, options=None, sheet=None, header_row=None, request=None) -> ImportBatch:
    itype = import_type(key)
    opts = _options(itype, options)
    gate(actor, itype, opts, request=request)
    t = _table(itype, uploaded, sheet, header_row)
    cmap = confirmed_mapping(itype, mapping)
    result = {}
    try:
        with transaction.atomic():
            result["out"] = _process(actor, itype, t, cmap, opts)
            raise _DryRun  # never write during a preview
    except _DryRun:
        pass
    report, counts, _, _, denied = result["out"]
    _record_denials(actor, itype, denied, request)
    suggestions, _ = mapping_mod.suggest(itype, t.headers)
    sources = {str(c): (suggestions[c].source if suggestions.get(c) and suggestions[c].field == f else
                        mapping_mod.MANUAL) for c, f in cmap.items()}
    batch = ImportBatch.objects.create(
        import_type=itype.key, created_by=actor, file_name=uploaded.name[:255], file_type=t.book.file_type,
        file_size=t.book.size, file_sha256=t.book.sha256, sheet_name=t.sheet[:100], header_row=t.header_index + 1,
        mapping={str(c): f for c, f in cmap.items()}, mapping_sources=sources, options=opts,
        rows_total=len(report), counts=counts, report=report)
    audit.record(action=f"imports.{itype.key}.preview", allowed=True, actor=actor, resource=batch, request=request,
                 after={"batch": batch.pk, "file_sha256": batch.file_sha256, "rows": batch.rows_total,
                        "counts": counts})
    return batch


def commit(actor, batch: ImportBatch, uploaded, *, skip_blocking=False, request=None) -> ImportBatch:
    itype = import_type(batch.import_type)
    if batch.status != ImportBatch.Status.PREVIEWED:
        raise ValidationError("This import has already been committed")
    if actor is None or batch.created_by_id != actor.pk:
        deny(actor, f"imports.{itype.key}", "Only the person who previewed an import may commit it",
             resource=batch, request=request)
    gate(actor, itype, batch.options, request=request)
    t = _table(itype, uploaded, batch.sheet_name or None, batch.header_row)
    if t.book.sha256 != batch.file_sha256:
        raise ValidationError("This is not the file that was previewed; preview it again")
    blocking = sum(n for s, n in batch.counts.items() if s in BLOCKING)
    if blocking and not skip_blocking:
        raise ValidationError(f"{blocking} row(s) are not valid. Correct the file and preview again, or confirm "
                              "that these rows are to be skipped")
    if not batch.counts.get(VALID):
        raise ValidationError("There are no valid rows to import")
    cmap = {int(c): f for c, f in batch.mapping.items()}
    expected = [(r["row"], r["status"]) for r in batch.report]
    with transaction.atomic():
        report, counts, refs, persons, denied = _process(actor, itype, t, cmap, batch.options)
        if [(r["row"], r["status"]) for r in report] != expected:
            raise StalePreview("The ERP data changed since the preview; preview the file again")
        event = audit.record(action=f"imports.{itype.key}.commit", allowed=True, actor=actor, resource=batch,
                             request=request,
                             after={"batch": batch.pk, "file_sha256": batch.file_sha256, "created": len(refs),
                                    "skipped": len(report) - counts[VALID], "provisioned": len(persons)})
        batch.status = ImportBatch.Status.COMMITTED
        batch.committed_by, batch.committed_at = actor, timezone.now()
        batch.rows_created, batch.rows_skipped = counts[VALID], len(report) - counts[VALID]
        batch.created_refs, batch.provisioned_person_ids = refs, persons
        batch.audit_event_id = event.pk
        batch.report = report
        batch.save()
    return batch


def activation_links(actor, batch: ImportBatch, *, url_for, request=None) -> list[dict]:
    """One-time activation links for the logins this import created and that are still not activated."""
    from identity.models import Person

    if batch.status != ImportBatch.Status.COMMITTED:
        raise ValidationError("The import has not been committed")
    require(actor, "identity.person.manage", None, request=request)
    out = []
    for person in Person.objects.filter(pk__in=batch.provisioned_person_ids).select_related("user").order_by("pk"):
        if provisioning.login_state(person) != "PENDING_ACTIVATION" or person.user.last_login is not None:
            continue
        link = provisioning.issue_activation(actor, person, request=request)
        out.append({"name": person.full_name, "email": person.email,
                    "link": url_for(f"/academic/activate?uid={link['uid']}&token={link['token']}")})
    return out
