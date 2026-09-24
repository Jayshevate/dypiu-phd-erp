"""Per-scholar rolling deadlines derived from the scholar's own dates."""
from datetime import date
from typing import Optional

from . import policy
from .dates import add_months, add_years


def relaxation_eligible(gender: str, pwd_percent: int) -> bool:
    return gender == "F" or (pwd_percent or 0) > policy.PWD_RELAXATION_THRESHOLD_PERCENT


def max_duration_years(re_registered: bool, relaxation_granted: bool) -> int:
    years = policy.MAX_DURATION_YEARS
    if re_registered:
        years += policy.RE_REGISTRATION_YEARS
    if relaxation_granted:
        years += policy.RELAXATION_YEARS
    return years


def programme_ceiling(registration: date, re_registered=False, relaxation_granted=False) -> date:
    return add_years(registration, max_duration_years(re_registered, relaxation_granted))


def earliest_thesis_submission(registration: date) -> date:
    return add_years(registration, policy.MIN_DURATION_YEARS)


def supervisor_allocation_due(registration: date) -> date:
    return add_months(registration, policy.SUPERVISOR_ALLOCATION_MONTHS)


def tac_formation_due(supervisor_approved: date) -> date:
    return add_months(supervisor_approved, policy.TAC_FORMATION_MONTHS)


def coursework_due(registration: date) -> date:
    return add_months(registration, policy.COURSEWORK_MONTHS)


def coursework_attempt_window_end(first_attempt: date) -> date:
    return add_years(first_attempt, policy.COURSEWORK_ATTEMPT_WINDOW_YEARS)


def proposal_due(coursework_passed: date) -> date:
    return add_months(coursework_passed, policy.PROPOSAL_DUE_MONTHS_AFTER_COURSEWORK)


def progress_report_due_dates(registration: date, until: date) -> list[date]:
    """Every 6 months from the scholar's registration date, up to ``until``."""
    dues, n = [], 1
    while True:
        due = add_months(registration, n * policy.PROGRESS_REPORT_INTERVAL_MONTHS)
        if due > until:
            return dues
        dues.append(due)
        n += 1


def synopsis_due(registration: date, mode: str, extended: bool = False) -> date:
    years = policy.SYNOPSIS_DUE_YEARS[mode] + (policy.SYNOPSIS_EXTENSION_YEARS if extended else 0)
    return add_years(registration, years)


def thesis_submission_due(synopsis_cleared: date, extended: bool = False) -> date:
    months = policy.THESIS_SUBMISSION_MONTHS_AFTER_SYNOPSIS
    if extended:
        months += policy.THESIS_SUBMISSION_EXTENSION_MONTHS
    return add_months(synopsis_cleared, months)


def examiner_report_due(dispatched: date) -> date:
    from datetime import timedelta
    return dispatched + timedelta(days=policy.EXAMINER_REPORT_DAYS)


def viva_due(last_report_received: date) -> date:
    return add_months(last_report_received, policy.VIVA_MONTHS_AFTER_REPORTS)


def srf_eligible_from(fellowship_start: date) -> date:
    return add_years(fellowship_start, policy.SRF_ELIGIBLE_AFTER_YEARS)


def clamp_to_ceiling(due: date, ceiling: Optional[date]) -> date:
    """No derived deadline may fall after the programme ceiling."""
    return min(due, ceiling) if ceiling else due
