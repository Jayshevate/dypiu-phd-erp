"""Column mapping for imports (Step A3).

The deterministic mapper matches normalised headers against each field's name
and aliases. An optional AI mapper (`AIImportMapper`) may suggest mappings for
columns the deterministic pass could not map confidently. Neither is ever
final: every mapping is shown to the administrator, who confirms or edits it,
and every value is then validated against real ERP records.

Privacy: an AI mapper receives ONLY the header texts and the field
definitions, never cell values. It is disabled unless
`settings.IMPORT_AI_MAPPER` names a class (dotted path). If it is unavailable,
rate limited, misconfigured or raises, the deterministic result stands and the
response says so."""
import difflib
import logging
import re
import dataclasses
from dataclasses import dataclass

from django.conf import settings
from django.utils.module_loading import import_string

log = logging.getLogger("imports")

EXACT, ALIAS, FUZZY, AI, MANUAL = "EXACT", "ALIAS", "FUZZY", "AI", "MANUAL"
CONFIDENT = {EXACT, ALIAS}


def norm(text) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


@dataclass
class ColumnSuggestion:
    column: int
    header: str
    field: str | None = None
    source: str | None = None           # EXACT / ALIAS / FUZZY / AI
    status: str = "UNMAPPED"            # MAPPED / SUGGESTED / AMBIGUOUS / UNMAPPED
    alternatives: list = dataclasses.field(default_factory=list)

    def as_dict(self):
        return {"column": self.column, "header": self.header, "field": self.field, "source": self.source,
                "status": self.status, "alternatives": self.alternatives}


class AIImportMapper:
    """Provider interface. Implementations must be side-effect free and must not
    retain the headers. Return {column_index: field_name} for columns they can map."""

    name = "abstract"

    def suggest(self, *, import_type: str, fields: list[dict], headers: dict[int, str]) -> dict[int, str]:
        raise NotImplementedError


def configured_ai_mapper() -> AIImportMapper | None:
    path = getattr(settings, "IMPORT_AI_MAPPER", "") or ""
    if not path:
        return None
    try:
        return import_string(path)()
    except Exception:  # noqa: BLE001
        log.warning("IMPORT_AI_MAPPER %r could not be loaded; using deterministic mapping only", path)
        return None


def _vocabulary(itype):
    vocab = {}
    for f in itype.fields:
        vocab.setdefault(norm(f.name), set()).add((f.name, EXACT))
        vocab.setdefault(norm(f.label), set()).add((f.name, ALIAS))
        for a in f.aliases:
            vocab.setdefault(norm(a), set()).add((f.name, ALIAS))
    return vocab


def detect_header_row(itype, rows, *, scan=15) -> int:
    """0-based index of the row that best matches the field vocabulary (title rows above are skipped)."""
    vocab = _vocabulary(itype)
    best, best_score = 0, -1
    for i, row in enumerate(rows[:scan]):
        texts = [norm(c) for c in row if c is not None and str(c).strip()]
        if len(texts) < 2:
            continue
        score = sum(1 for t in texts if t in vocab)
        if score > best_score:
            best, best_score = i, score
    return best


def deterministic(itype, headers: dict[int, str]) -> dict[int, ColumnSuggestion]:
    vocab = _vocabulary(itype)
    keys = list(vocab)
    out = {}
    for col, header in headers.items():
        s = ColumnSuggestion(column=col, header=header)
        h = norm(header)
        if not h:
            out[col] = s
            continue
        hits = vocab.get(h, set())
        fields = {f for f, _ in hits}
        if len(fields) == 1:
            s.field, s.source = next(iter(hits))
            s.status = "MAPPED"
        elif len(fields) > 1:
            s.status, s.alternatives = "AMBIGUOUS", sorted(fields)
        else:
            scored = {}
            for k in keys:
                ratio = difflib.SequenceMatcher(None, h, k).ratio()
                if ratio >= 0.85:
                    for f, _ in vocab[k]:
                        scored[f] = max(scored.get(f, 0), ratio)
            ranked = sorted(scored.items(), key=lambda kv: -kv[1])
            if len(ranked) == 1 or (len(ranked) > 1 and ranked[0][1] - ranked[1][1] >= 0.05):
                s.field, s.source, s.status = ranked[0][0], FUZZY, "SUGGESTED"
            elif len(ranked) > 1:
                s.status, s.alternatives = "AMBIGUOUS", [f for f, _ in ranked[:4]]
        out[col] = s
    _flag_collisions(out)
    return out


def _flag_collisions(result):
    """Two columns claiming the same field: neither is chosen silently."""
    by_field = {}
    for s in result.values():
        if s.field:
            by_field.setdefault(s.field, []).append(s)
    for f, cols in by_field.items():
        if len(cols) > 1:
            for s in cols:
                s.status, s.alternatives, s.field, s.source = "AMBIGUOUS", [f], None, None


def suggest(itype, headers: dict[int, str]) -> tuple[dict[int, ColumnSuggestion], dict]:
    """Deterministic mapping, then (optionally) AI suggestions for what is left."""
    result = deterministic(itype, headers)
    info = {"enabled": False, "used": False, "provider": None, "note": "AI-assisted mapping is not configured; "
            "columns were matched by name and known aliases only."}
    mapper = configured_ai_mapper()
    if mapper is None:
        return result, info
    info.update(enabled=True, provider=getattr(mapper, "name", type(mapper).__name__))
    open_cols = {c: h for c, h in headers.items() if result[c].status in ("UNMAPPED", "AMBIGUOUS", "SUGGESTED")}
    if not open_cols:
        info["note"] = "All columns were matched without AI assistance."
        return result, info
    fields = [{"name": f.name, "label": f.label, "help": f.help} for f in itype.fields]
    try:
        proposals = mapper.suggest(import_type=itype.key, fields=fields, headers=open_cols) or {}
    except Exception:  # noqa: BLE001  (unavailable, rate limited, misconfigured…)
        log.warning("AI import mapper %s failed; deterministic mapping only", info["provider"])
        info["note"] = "The AI mapping assistant is unavailable; columns were matched by name and alias only."
        return result, info
    valid = {f.name for f in itype.fields}
    taken = {s.field for s in result.values() if s.status == "MAPPED"}
    for col, fname in proposals.items():
        try:
            col = int(col)
        except (TypeError, ValueError):
            continue
        if col not in open_cols or fname not in valid or fname in taken:
            continue  # suggestions are only accepted for real fields that are still free
        s = result[col]
        s.field, s.source, s.status, s.alternatives = fname, AI, "SUGGESTED", []
        taken.add(fname)
        info["used"] = True
    _flag_collisions(result)
    info["note"] = ("AI suggestions are marked SUGGESTED and must be confirmed; values are still validated "
                    "against ERP records.")
    return result, info
