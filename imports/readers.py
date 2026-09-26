"""Reading uploaded CSV / XLSX / XLS files into plain tables (Step A3).

Only cell values are read: no formulas are evaluated (openpyxl `data_only`
returns the cached value), no macros run, and nothing is written to disk.
Limits come from settings (`IMPORT_MAX_BYTES`, `IMPORT_MAX_ROWS`,
`IMPORT_MAX_COLUMNS`)."""
import csv
import hashlib
import io
from dataclasses import dataclass, field
from datetime import date, datetime

from django.conf import settings
from django.core.exceptions import ValidationError

TYPES = {"csv": "csv", "xlsx": "xlsx", "xls": "xls"}


def limit(name, default):
    return getattr(settings, name, default)


@dataclass
class Sheet:
    name: str
    rows: list  # list of lists of cell values (str, int, float, date, datetime or None)


@dataclass
class Workbook:
    file_type: str
    sha256: str
    size: int
    sheets: list = field(default_factory=list)

    def sheet(self, name=None) -> Sheet:
        if name:
            for s in self.sheets:
                if s.name == name:
                    return s
            raise ValidationError(f"The file has no sheet named {name!r}")
        for s in self.sheets:
            if any(any(_present(c) for c in row) for row in s.rows):
                return s
        raise ValidationError("The file contains no data")


def _present(value) -> bool:
    return value is not None and str(value).strip() != ""


def file_type_of(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in TYPES:
        raise ValidationError("Unsupported file type: upload a .csv, .xlsx or .xls file")
    return TYPES[ext]


def read(uploaded) -> Workbook:
    """Parse an uploaded file. Raises ValidationError for anything unreadable or over a limit."""
    kind = file_type_of(uploaded.name)
    max_bytes = limit("IMPORT_MAX_BYTES", 5 * 1024 * 1024)
    if uploaded.size > max_bytes:
        raise ValidationError(f"The file is larger than the {max_bytes // (1024 * 1024)} MB limit")
    data = uploaded.read()
    if not data:
        raise ValidationError("The file is empty")
    book = Workbook(file_type=kind, sha256=hashlib.sha256(data).hexdigest(), size=len(data))
    try:
        book.sheets = {"csv": _read_csv, "xlsx": _read_xlsx, "xls": _read_xls}[kind](data)
    except ValidationError:
        raise
    except Exception:  # noqa: BLE001  (any parser failure means the file is malformed)
        raise ValidationError(f"The file could not be read as {kind.upper()}; it may be damaged or of another type")
    max_rows, max_cols = limit("IMPORT_MAX_ROWS", 5000), limit("IMPORT_MAX_COLUMNS", 100)
    for s in book.sheets:
        while s.rows and not any(_present(c) for c in s.rows[-1]):
            s.rows.pop()
        if len(s.rows) > max_rows + 20:  # a few title/header rows are allowed above the data
            raise ValidationError(f"Sheet {s.name!r} has more than {max_rows} rows; split the file")
        if any(len(r) > max_cols for r in s.rows):
            raise ValidationError(f"Sheet {s.name!r} has more than {max_cols} columns")
    return book


def _read_csv(data: bytes):
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValidationError("The CSV file is not UTF-8 or Windows-1252 text")
    if "\x00" in text:
        raise ValidationError("The CSV file contains binary data")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = [[c if c != "" else None for c in row] for row in csv.reader(io.StringIO(text), dialect)]
    return [Sheet(name="CSV", rows=rows)]


def _read_xlsx(data: bytes):
    import zipfile

    import openpyxl

    # An .xlsx is a ZIP archive: refuse archives that would expand far beyond the upload limit (zip bombs).
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        expanded = sum(i.file_size for i in archive.infolist())
    if expanded > limit("IMPORT_MAX_EXPANDED_BYTES", 100 * 1024 * 1024):
        raise ValidationError("The workbook expands to an unreasonable size; save it again or split it")
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        sheets = []
        max_rows = limit("IMPORT_MAX_ROWS", 5000) + 21
        for ws in wb.worksheets:
            rows = []
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i >= max_rows:
                    rows.append([None])  # marks the sheet as over the limit
                    break
                rows.append(list(row))
            sheets.append(Sheet(name=ws.title, rows=rows))
        return sheets
    finally:
        wb.close()


def _read_xls(data: bytes):
    import xlrd

    book = xlrd.open_workbook(file_contents=data, on_demand=True)
    sheets = []
    for sh in book.sheets():
        rows = []
        for r in range(sh.nrows):
            out = []
            for c in range(sh.ncols):
                cell = sh.cell(r, c)
                if cell.ctype == xlrd.XL_CELL_DATE:
                    out.append(xlrd.xldate.xldate_as_datetime(cell.value, book.datemode))
                elif cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                    out.append(None)
                elif cell.ctype == xlrd.XL_CELL_ERROR:
                    out.append(None)
                else:
                    out.append(cell.value)
            rows.append(out)
        sheets.append(Sheet(name=sh.name, rows=rows))
    return sheets


def cell_text(value) -> str:
    """A cell as display text (dates as ISO, whole floats without '.0')."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == datetime.min.time() else value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()
