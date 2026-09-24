"""Organisation-wide academic calendar (the "global clock").

Generates the recurring events from the Yearly Calendar sheet for a given
year. Dates the sheet gives only as a month ("early May") are materialised on
a configurable default day and must be confirmed by the R&D office each year.
"""
from dataclasses import dataclass
from datetime import date, timedelta

from .dates import add_months


@dataclass(frozen=True)
class EventSpec:
    kind: str
    title: str
    start: date
    end: date

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.start.isoformat()}"


# Defaults for month-only entries (ASSUMPTION: confirm yearly).
VACANCY_DAY = 1
EXAM_WINDOW = (1, 10)            # "early May / early December"
SEMESTER_STARTS = ((1, 1), (7, 1))  # (month, day) - not in the extract
RPET_WINDOW = (20, 25)
TAC_REPORT_CUTOFFS = ((5, 31), (10, 31))  # "before June", "before November"


def rpet_dates(year: int, month: int) -> list[date]:
    """Saturdays/Sundays within the 20-25 window. Any 6 consecutive days
    contain at least one weekend day, so this is never empty."""
    lo, hi = RPET_WINDOW
    days = (date(year, month, d) for d in range(lo, hi + 1))
    return [d for d in days if d.weekday() >= 5]


def events_for_year(year: int) -> list[EventSpec]:
    events: list[EventSpec] = []
    for month, label in ((3, "March"), (9, "September")):
        d = date(year, month, VACANCY_DAY)
        events.append(EventSpec("VACANCY_NOTIFICATION", f"PhD vacancy notification & admission cycle opens ({label})", d, d))

    for month in (6, 11):
        days = rpet_dates(year, month)
        events.append(EventSpec("RPET", f"RPET entrance test ({days[0]:%B})", days[0], days[-1]))

    for month in (5, 12):
        start = date(year, month, EXAM_WINDOW[0])
        end = date(year, month, EXAM_WINDOW[1])
        events.append(EventSpec("COURSEWORK_EXAM", f"Coursework examinations ({start:%B})", start, end))
        reg = add_months(start, -1)
        events.append(EventSpec("EXAM_REGISTRATION_DEADLINE", f"Coursework exam registration closes ({start:%B} exams)", reg, reg))

    for month, day in SEMESTER_STARTS:
        start = date(year, month, day)
        events.append(EventSpec("SEMESTER_START", f"Semester starts ({start:%B})", start, start))
        reg = add_months(start, -1)
        events.append(EventSpec("ELECTIVE_REGISTRATION_DEADLINE", f"Elective registration closes ({start:%B} semester)", reg, reg))

    for month, day in TAC_REPORT_CUTOFFS:
        d = date(year, month, day)
        events.append(EventSpec("TAC_REPORTS_TO_DC", f"TAC reports due to DC ({d:%B})", d, d))

    return sorted(events, key=lambda e: e.start)


def viva_notice_dates(viva: date) -> tuple[date, date]:
    """(date by which the candidate must be told, date by which the open
    invitation must be published)."""
    from . import policy
    return viva - timedelta(days=policy.VIVA_NOTICE_DAYS), viva - timedelta(days=policy.VIVA_OPEN_INVITATION_DAYS)
