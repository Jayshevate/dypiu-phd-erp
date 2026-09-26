"""Import API (Step A3): /api/academic/imports/...

URL · method · who:
- GET  imports/types/                     types the person may run (+ field documentation)
- GET  imports/templates/<type>/?kind=    xlsx (default) or csv template, for people who may run that type
- POST imports/analyze/                   multipart: file, import_type[, sheet, header_row] → sheets, headers,
                                          mapping suggestions (nothing stored, nothing written)
- POST imports/preview/                   multipart: + mapping (JSON), provision → validated dry run, batch record
- GET  imports/                           recent batches of types the person may run (or their own)
- GET  imports/<id>/                      one batch with its per-row report
- POST imports/<id>/commit/               multipart: file (the same file), skip_blocking → atomic import
- POST imports/<id>/activation-links/     CSV of one-time activation links for logins this import created

Every mutation is authorized by the import engine and the domain services, and audited."""
import csv
import io
import json

from django.core.exceptions import ValidationError
from django.http import HttpResponse
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response

from coursework.academic.common import deny
from coursework.api.common import AcademicView, can, dt, load, person_name
from identity.api_base import Conflict

from . import engine, templates
from .models import ImportBatch
from .types import TYPES


def _upload(request):
    f = request.FILES.get("file")
    if f is None:
        raise ValidationError("Upload a file")
    return f


def _json(request, name, default=None):
    raw = request.data.get(name)
    if raw in (None, ""):
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        raise ValidationError(f"{name} must be JSON")


def _int(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValidationError("header_row must be a number")


def batch_data(b: ImportBatch, *, with_report=False):
    data = {"id": b.pk, "import_type": b.import_type, "label": TYPES[b.import_type].label, "status": b.status,
            "created_by": person_name(b.created_by), "created_at": dt(b.created_at), "file_name": b.file_name,
            "file_type": b.file_type, "file_size": b.file_size, "file_sha256": b.file_sha256,
            "sheet_name": b.sheet_name, "header_row": b.header_row, "mapping": b.mapping,
            "mapping_sources": b.mapping_sources, "options": b.options, "rows_total": b.rows_total,
            "counts": b.counts, "committed_by": person_name(b.committed_by), "committed_at": dt(b.committed_at),
            "rows_created": b.rows_created, "rows_skipped": b.rows_skipped,
            "provisioned": len(b.provisioned_person_ids), "audit_event_id": b.audit_event_id}
    if with_report:
        data["report"] = b.report
    return data


def _visible(person, b: ImportBatch) -> bool:
    return b.created_by_id == person.pk or engine.may_run(person, TYPES[b.import_type])


class TypesView(AcademicView):
    def get(self, request):
        allowed = [t for t in TYPES.values() if engine.may_run(self.person, t)]
        if not allowed:
            deny(self.person, "imports.view", "No capability for any institutional import", request=request)
        return Response({"results": [t.as_dict() for t in allowed],
                         "can": {"provision": can(self.person, "identity.person.manage")}})


class TemplateView(AcademicView):
    def get(self, request, key):
        itype = engine.import_type(key)
        engine.gate(self.person, itype, request=request)
        if request.query_params.get("kind") == "csv":
            resp = HttpResponse(templates.csv_template(itype), content_type="text/csv; charset=utf-8")
            resp["Content-Disposition"] = f'attachment; filename="{key}-import-template.csv"'
        else:
            resp = HttpResponse(templates.xlsx_template(itype),
                                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            resp["Content-Disposition"] = f'attachment; filename="{key}-import-template.xlsx"'
        return resp


class AnalyzeView(AcademicView):
    parser_classes = [MultiPartParser]

    def post(self, request):
        return Response(engine.analyze(self.person, request.data.get("import_type"), _upload(request),
                                       sheet=request.data.get("sheet") or None,
                                       header_row=_int(request.data.get("header_row")), request=request))


class PreviewView(AcademicView):
    parser_classes = [MultiPartParser]

    def post(self, request):
        batch = engine.preview(self.person, request.data.get("import_type"), _upload(request),
                               mapping=_json(request, "mapping"),
                               options={"provision": request.data.get("provision") or "none"},
                               sheet=request.data.get("sheet") or None,
                               header_row=_int(request.data.get("header_row")), request=request)
        return Response(batch_data(batch, with_report=True), status=201)


class BatchListView(AcademicView):
    def get(self, request):
        allowed = [k for k, t in TYPES.items() if engine.may_run(self.person, t)]
        if not allowed:
            deny(self.person, "imports.view", "No capability for any institutional import", request=request)
        qs = ImportBatch.objects.select_related("created_by", "committed_by").filter(import_type__in=allowed)[:100]
        return Response({"results": [batch_data(b) for b in qs]})


class BatchView(AcademicView):
    def get(self, request, pk):
        b = load(ImportBatch, pk)
        if not _visible(self.person, b):
            deny(self.person, "imports.view", "Not permitted to view this import", resource=b, request=request)
        return Response(batch_data(b, with_report=True))


class CommitView(AcademicView):
    parser_classes = [MultiPartParser]

    def post(self, request, pk):
        b = load(ImportBatch, pk)
        skip = str(request.data.get("skip_blocking", "")).lower() in ("true", "1", "yes")
        try:
            b = engine.commit(self.person, b, _upload(request), skip_blocking=skip, request=request)
        except engine.StalePreview as e:
            raise Conflict(str(e))
        return Response(batch_data(b, with_report=True))


def _csv_safe(value: str) -> str:
    """Neutralise spreadsheet formula injection in exported cells."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


class ActivationLinksView(AcademicView):
    def post(self, request, pk):
        b = load(ImportBatch, pk)
        rows = engine.activation_links(self.person, b, url_for=request.build_absolute_uri, request=request)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["name", "email", "activation_link"])
        for r in rows:
            w.writerow([_csv_safe(r["name"]), _csv_safe(r["email"]), r["link"]])
        resp = HttpResponse(buf.getvalue().encode("utf-8-sig"), content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="import-{b.pk}-activation-links.csv"'
        resp["Cache-Control"] = "no-store"
        return resp
