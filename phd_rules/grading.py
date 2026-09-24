"""Coursework grading (Academic Framework Implementation Guidelines)."""
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable, Optional

from . import policy

# (letter, lowest mark in band, grade point) - highest band first.
GRADE_BANDS = (
    ("A+", 91, 10),
    ("A", 81, 9),
    ("B+", 71, 8),
    ("B", 61, 7),
    ("C+", 51, 6),
    ("C", 41, 5),
    ("D", 40, 4),
)
FAIL = ("F", 0)
# Incomplete / Re-register (withdrawn) / Absent: never a pass, no marks.
SPECIAL_GRADES = ("I", "RW", "AB")


def grade_for_marks(marks) -> tuple[str, int]:
    """Map a 0-100 mark to (letter, grade point). Marks round half-up first."""
    marks = Decimal(str(marks)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if not 0 <= marks <= 100:
        raise ValueError(f"marks out of range: {marks}")
    for letter, lower, point in GRADE_BANDS:
        if marks >= lower:
            return letter, point
    return FAIL


def is_pass(grade_point: Optional[int]) -> bool:
    return grade_point is not None and grade_point >= policy.COURSE_MIN_GRADE_POINT


@dataclass(frozen=True)
class CourseResult:
    code: str
    credits: int
    grade_point: Optional[int]       # None for I / RW / AB
    ethics_cleared: Optional[bool] = None  # only for courses with an ethics sub-module


def gpa(results: Iterable[CourseResult]) -> Decimal:
    results = [r for r in results if r.grade_point is not None]
    credits = sum(r.credits for r in results)
    if not credits:
        return Decimal("0.00")
    points = sum(r.credits * r.grade_point for r in results)
    return (Decimal(points) / Decimal(credits)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass
class CourseworkVerdict:
    complete: bool
    gpa: Decimal
    earned_credits: int
    required_credits: int
    reasons: list


def evaluate_coursework(
    results: Iterable[CourseResult],
    entry_qualification: str,
    mandatory_codes: Iterable[str] = (),
) -> CourseworkVerdict:
    """Coursework is complete when every counted course is at least C+, the
    required credits for the scholar's entry category are earned, all mandatory
    courses are passed, ethics sub-modules are cleared and GPA >= 6.0."""
    results = list(results)
    required = policy.REQUIRED_CREDITS[entry_qualification]
    reasons = []

    passed = [r for r in results if is_pass(r.grade_point)]
    earned = sum(r.credits for r in passed)
    passed_codes = {r.code for r in passed}

    for r in results:
        if not is_pass(r.grade_point):
            reasons.append(f"{r.code}: below minimum grade C+")
        if r.ethics_cleared is False:
            reasons.append(f"{r.code}: ethics sub-module not cleared")
    for code in mandatory_codes:
        if code not in passed_codes:
            reasons.append(f"{code}: mandatory course not passed")
    if earned < required:
        reasons.append(f"credits earned {earned} < required {required}")
    score = gpa(results)
    if score < policy.COURSEWORK_MIN_GPA:
        reasons.append(f"GPA {score} < {policy.COURSEWORK_MIN_GPA}")

    return CourseworkVerdict(not reasons, score, earned, required, reasons)
