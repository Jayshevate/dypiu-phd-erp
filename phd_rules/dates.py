import calendar
from datetime import date


def add_months(d: date, months: int) -> date:
    """Calendar-month arithmetic, clamping to the last day of short months."""
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def add_years(d: date, years: int) -> date:
    return add_months(d, 12 * years)


def months_between(start: date, end: date) -> int:
    """Whole calendar months elapsed from ``start`` to ``end``."""
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return months
