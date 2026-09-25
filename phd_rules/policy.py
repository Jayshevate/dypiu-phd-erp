"""Numeric policy values taken from the DYPIU PhD Regulations (1 July 2026),
the Academic Framework Implementation Guidelines and the PhD Calendar sheet.

Every tunable number lives here so a regulation amendment is a one-line change.
Values tagged ``ASSUMPTION`` are not stated in the requirements extract and must
be confirmed against the source PDFs (see docs/OPEN_QUESTIONS.md).
"""
from decimal import Decimal

# --- Admission -------------------------------------------------------------
RPET_PASS_PERCENT = Decimal("50")
INTERVIEW_PASS_PERCENT = Decimal("50")
# Qualifications that exempt a candidate from RPET.
RPET_EXEMPTIONS = ("GATE", "NET", "CSIR", "SLET", "JRF")  # ASSUMPTION: exact list

# --- Duration ceilings (years) ----------------------------------------------
MIN_DURATION_YEARS = 3
MAX_DURATION_YEARS = 6
RE_REGISTRATION_YEARS = 2          # -> 8 years total
RELAXATION_YEARS = 2               # female / PwD > 40% -> 10 years total
PWD_RELAXATION_THRESHOLD_PERCENT = 40

# --- Supervision -----------------------------------------------------------
SUPERVISOR_CAPACITY = {
    "ASSISTANT_PROFESSOR": 4,
    "ASSOCIATE_PROFESSOR": 6,
    "PROFESSOR": 8,
}
COUNT_CO_SUPERVISION_TOWARDS_CAP = True       # ASSUMPTION
MIN_SERVICE_YEARS_REMAINING = 3               # ASSUMPTION: threshold not in extract
SUPERVISOR_ALLOCATION_MONTHS = 6              # from registration
TAC_FORMATION_MONTHS = 1                      # from supervisor approval
TAC_MEMBER_COUNT = 2                          # interdisciplinary members + supervisor
TAC_REQUIRE_INTERDISCIPLINARY = True          # member dept != scholar dept
# A faculty member may sit on only one TAC among the scholars of the same supervisor.
TAC_MEMBER_UNIQUE_PER_SUPERVISOR = True

# --- Coursework ------------------------------------------------------------
# Coursework rules (pass, GPA, C+, credits, attempts, completion) are academic
# regulatory parameters: see coursework.AcademicRuleParameter. Only the
# scheduling clock for the coursework-completion reminder remains here (RD-06).
COURSEWORK_MONTHS = 12

# --- Research proposal -----------------------------------------------------
PROPOSAL_DUE_MONTHS_AFTER_COURSEWORK = 6
PROPOSAL_MAX_FAILED_ATTEMPTS = 2              # then registration is cancelled

# --- Progress monitoring ---------------------------------------------------
PROGRESS_REPORT_INTERVAL_MONTHS = 6
MIN_TAC_REPORTS_PER_YEAR = 2
WARNINGS_BEFORE_DC_REVIEW = 2

# --- Synopsis / thesis ------------------------------------------------------
SYNOPSIS_DUE_YEARS = {"FT": 4, "PT": 5}
SYNOPSIS_EXTENSION_YEARS = 1
THESIS_SUBMISSION_MONTHS_AFTER_SYNOPSIS = 6
THESIS_SUBMISSION_EXTENSION_MONTHS = 3
MAX_PLAGIARISM_PERCENT = Decimal("10")

# --- Evaluation / viva -----------------------------------------------------
EXAMINER_PANEL_MIN_NOMINEES = 8
EXAMINERS_SELECTED = 3
EXAMINERS_COMMEND_REQUIRED = 2
EXAMINER_REPORT_DAYS = 90                     # "within 3 months"
EXAMINER_REMINDER_DAYS = 60
EXAMINER_ESCALATION_GRACE_DAYS = 30           # after reminder -> replace examiner
VIVA_MONTHS_AFTER_REPORTS = 2
VIVA_NOTICE_DAYS = 15
VIVA_OPEN_INVITATION_DAYS = 7

# --- Fellowship / TA -------------------------------------------------------
JRF_STIPEND = Decimal("31000")
SRF_STIPEND = Decimal("36000")
SRF_ELIGIBLE_AFTER_YEARS = 2
TA_MAX_HOURS_PER_WEEK = 20
TA_MAX_CONTACT_HOURS_PER_WEEK = 10
TA_MAX_SEMESTERS = 10
TA_FORM_DAYS_AFTER_FEE_DEADLINE = 2

# --- Financial support -----------------------------------------------------
GRANT_LIFETIME_CAP = Decimal("50000")
GRANT_MIN_GAP_MONTHS = 6

# --- Leave -----------------------------------------------------------------
ANNUAL_LEAVE_DAYS = 25                        # per calendar year (ASSUMPTION: leave year)
MATERNITY_LEAVE_DAYS = 240                    # whole programme

# --- Notifications ---------------------------------------------------------
REMINDER_LEAD_DAYS = (30, 7, 1)
