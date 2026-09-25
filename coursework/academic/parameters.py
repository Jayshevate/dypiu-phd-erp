"""Initial academic regulatory parameters (version 1).

Classification:
  CONFIRMED    stated by the Step 5A academic source supplied by DYPIU
  CONFIGURABLE value known (both codebases agree or a single documented value)
               but still pending PhD Cell / Dean R&D confirmation
  AMBIGUOUS    sources conflict or the wording admits more than one reading
  UNRESOLVED   no value; the operation depending on it is blocked or advisory

The same data is frozen into migration coursework/0003_seed_rule_parameters.
Change values ONLY through coursework.academic.config.set_parameter (versioned,
authorised, audited), never by editing this file."""

SRC_5A = "Academic source supplied by DYPIU (Step 5A)"
SRC_REACT = "React journeyData.ts (Regulations July 2026 transcription)"
SRC_BRIEF = "Requirements brief (Django docs/REQUIREMENTS.md)"

INITIAL_PARAMETERS = [
    # key, value, status, source, reference
    ("coursework.structure.mandatory_taught_courses", 3, "CONFIRMED", SRC_5A, ""),
    ("coursework.structure.mandatory_activities", 3, "CONFIRMED", SRC_5A, ""),
    ("coursework.category_of_entry_qualification", {"BTECH": "I", "MTECH": "II", "PG": "III"},
     "CONFIGURABLE", SRC_REACT, "RD-43"),
    ("coursework.required_credits", {"I": 23, "II": 17, "III": 20}, "CONFIGURABLE", SRC_REACT, "RD-C1"),
    ("coursework.electives_required", {"I": 3, "II": 1, "III": 2}, "AMBIGUOUS", SRC_REACT, "RD-08"),
    ("coursework.min_gpa", "6.00", "CONFIRMED", SRC_5A, "RD-C3"),
    ("coursework.course_pass_min_marks", "40", "CONFIRMED", SRC_5A + ": minimum 40 marks (D)", "RD-01"),
    ("coursework.course_pass_min_applies_to", "COURSE", "AMBIGUOUS",
     SRC_5A + ": 'each course component' may mean each course or each assessment component", "RD-01"),
    ("coursework.continuation_min_grade", "C+", "CONFIRMED", SRC_5A, "RD-02"),
    ("coursework.ethics_module_required", True, "CONFIRMED",
     SRC_5A + ": Research Methodology & Ethics has a mandatory ethics/plagiarism module", ""),
    ("grading.scheme", {
        "mode": "ABSOLUTE",
        "rounding": "HALF_UP_TO_INTEGER",
        "bands": [["A+", 91, "10"], ["A", 81, "9"], ["B+", 71, "8"], ["B", 61, "7"],
                  ["C+", 51, "6"], ["C", 41, "5"], ["D", 40, "4"]],
        "fail": ["F", "0"],
        "order": ["F", "D", "C", "C+", "B", "B+", "A", "A+"],
    }, "AMBIGUOUS", SRC_BRIEF + " (Implementation Guidelines bands); React states relative grading",
     "RD-01, RD-02, RD-40"),
    ("assessment.default_weights", {"CONTINUOUS": 50, "END_TERM": 50}, "CONFIRMED",
     SRC_5A + ": 50% Continuous Assessment, 50% End-term", ""),
    ("assessment.elective_reweighting_limit_percent", 10, "CONFIGURABLE",
     SRC_BRIEF + ": adaptable ±10% weighting template for electives", "RD-37"),
    ("assessment.elective_reweighting_approver", None, "UNRESOLVED", "Not stated", "RD-37, RD-38"),
    ("attendance.physical_only", True, "CONFIRMED", SRC_5A + ": physical attendance requirement", ""),
    ("attendance.min_percent", {"FT": 75, "PT": None}, "AMBIGUOUS",
     SRC_REACT + ": full-time 'min 75% attendance'; basis (per course/overall) and part-time rule not stated",
     "RD-26"),
    ("exam.cycles", ["JAN_JUN", "JUL_DEC"], "CONFIRMED", SRC_5A + ": January–June and July–December cycles",
     "RD-35"),
    ("exam.max_regular_attempts", 2, "CONFIRMED", SRC_5A, "RD-C3"),
    ("exam.max_attempts_with_approval", 3, "CONFIRMED", SRC_5A + ": third (final) attempt case", "RD-16"),
    ("exam.attempt_window_years", 2, "CONFIRMED", SRC_REACT + ": within 2 years of enrolment", "RD-C3"),
    ("exam.attempt_window_start", "ENROLMENT", "AMBIGUOUS",
     "React M12 'of enrolment' vs Django legacy 'first attempt'", "RD-C3"),
    ("exam.absent_counts_as_attempt", None, "UNRESOLVED", "Not stated", ""),
    ("exam.reattempt_carries_continuous_assessment", None, "UNRESOLVED", "Not stated", ""),
    ("exam.requires_fee_clearance", True, "CONFIGURABLE",
     SRC_REACT + " M10: no admission to the exam unless tuition fees and hostel/library dues are cleared", ""),
    ("third_attempt.review_chain", ["DEAN_RND", "VC_OPERATOR"], "CONFIRMED",
     SRC_5A + ": Dean R&D review followed by VC approval", "RD-16"),
    ("third_attempt.mentor_required", None, "UNRESOLVED", "React M12 mentions a weekly-tracking mentor; "
     "not stated in the Step 5A source", "RD-16"),
    ("elective.approval_authority", None, "UNRESOLVED", "React M9.1 names 'DC / Dean of Research'; "
     "approving capability not confirmed", "RD-38"),
    ("elective.supervisor_recommendation_required", None, "UNRESOLVED", SRC_5A + ": 'where required'", "RD-38"),
    ("activity.required_artefacts", {
        "INDUSTRIAL_TRAINING": ["PLAN", "LOGBOOK", "REPORT", "POSTER"],
        "CONFERENCE_WORKSHOP": ["REFLECTIVE_NOTE"],
        "RESEARCH_SEMINAR": ["PRESENTATION"],
    }, "CONFIRMED", SRC_5A, "RD-37"),
    ("activity.artefacts_in_order", {"INDUSTRIAL_TRAINING": True}, "CONFIRMED",
     SRC_5A + ": plan → logbook → report → poster flow", ""),
    ("result.custody_chain", ["PREPARE", "VERIFY:RND_CELL_OPERATOR", "RATIFY:COE_OPERATOR"], "CONFIRMED",
     SRC_5A + ": Course Coordinator / SDRC → R&D Cell → COE", ""),
]

# Added in Step 5B (hardening). Frozen into migration coursework/0005.
STEP_5B_PARAMETERS = [
    ("result.absence_policy", None, "UNRESOLVED",
     "Not stated. The only documented notion is the special grade AB (Brief §5)", ""),
    ("attendance.basis", "PER_COURSE", "AMBIGUOUS",
     SRC_REACT + ": 'min 75% attendance' — per course or overall is not stated", "RD-26"),
    ("semester_registration.required", True, "CONFIGURABLE",
     SRC_REACT + " M7: semester course registration every semester", ""),
    ("semester_registration.requires_fee_clearance", True, "CONFIGURABLE",
     SRC_REACT + " M7: prescribed fees paid every semester until thesis submission", ""),
    ("elective.list_approval_chain", ["DC_MEMBER", "DEAN_RND"], "AMBIGUOUS",
     SRC_REACT + " M9.1: 'Elective list approved jointly by DC and Dean of Research' (order not stated)", "RD-38"),
    ("elective.proposal_requires_approved_list", True, "CONFIGURABLE",
     SRC_REACT + " M9.1: electives chosen from the approved list", "RD-38"),
    ("question_paper.setter_appointed_by", ["DC_MEMBER"], "CONFIGURABLE",
     SRC_REACT + " M11: 'DC appoints experts to set the question paper'", ""),
    ("revaluation.enabled", None, "UNRESOLVED", "No revaluation provision in the supplied sources", ""),
    ("revaluation.request_window_days", None, "UNRESOLVED", "Not stated", ""),
    ("revaluation.reviewer", None, "UNRESOLVED", "Not stated", ""),
    ("activity.scoring_scheme.INDUSTRIAL_TRAINING", None, "UNRESOLVED",
     SRC_BRIEF + ": '5-component rubric incl. poster' — components and weights not given", "RD-37"),
    ("activity.scoring_scheme.CONFERENCE_WORKSHOP", None, "UNRESOLVED",
     SRC_BRIEF + ": 'points-accumulation matrix + reflective-note gate' — matrix not given", "RD-37"),
    ("activity.scoring_scheme.RESEARCH_SEMINAR", None, "UNRESOLVED",
     SRC_BRIEF + ": '3-criteria rubric' — criteria and weights not given", "RD-37"),
]
