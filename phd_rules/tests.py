from datetime import date
from decimal import Decimal
from unittest import TestCase

from phd_rules import calendar_rules, durations, grading
from phd_rules.dates import add_months, months_between


class GradingTests(TestCase):
    def test_band_edges(self):
        cases = {100: ("A+", 10), 91: ("A+", 10), 90: ("A", 9), 81: ("A", 9), 80: ("B+", 8), 61: ("B", 7),
                 60: ("C+", 6), 51: ("C+", 6), 50: ("C", 5), 41: ("C", 5), 40: ("D", 4), 39: ("F", 0), 0: ("F", 0)}
        for marks, expected in cases.items():
            self.assertEqual(grading.grade_for_marks(marks), expected, marks)

    def test_rounds_half_up(self):
        self.assertEqual(grading.grade_for_marks("90.5"), ("A+", 10))
        self.assertEqual(grading.grade_for_marks("39.4"), ("F", 0))
        self.assertEqual(grading.grade_for_marks("39.5"), ("D", 4))

    def test_out_of_range(self):
        with self.assertRaises(ValueError):
            grading.grade_for_marks(101)

    def test_c_is_not_a_pass(self):
        self.assertFalse(grading.is_pass(5))
        self.assertTrue(grading.is_pass(6))
        self.assertFalse(grading.is_pass(None))

    def _core(self, gp=8):
        R = grading.CourseResult
        return [R("SIS7001", 4, gp, True), R("SIS7002", 2, gp), R("SIS7003", 3, gp),
                R("SIS7005", 2, gp), R("SIS7006", 1, gp), R("SIS7007", 2, gp)]

    def test_credit_requirements_by_entry(self):
        core = self._core()
        mtech = core + [grading.CourseResult("E1", 3, 8)]
        self.assertTrue(grading.evaluate_coursework(mtech, "MTECH").complete)
        self.assertFalse(grading.evaluate_coursework(mtech, "PG").complete)
        btech = mtech + [grading.CourseResult("E2", 3, 8), grading.CourseResult("E3", 3, 8)]
        verdict = grading.evaluate_coursework(btech, "BTECH")
        self.assertTrue(verdict.complete, verdict.reasons)
        self.assertEqual(verdict.earned_credits, 23)

    def test_ethics_submodule_must_clear(self):
        results = self._core()
        results[0] = grading.CourseResult("SIS7001", 4, 9, ethics_cleared=False)
        v = grading.evaluate_coursework(results + [grading.CourseResult("E1", 3, 8)], "MTECH")
        self.assertIn("SIS7001: ethics sub-module not cleared", v.reasons)

    def test_gpa_floor(self):
        v = grading.evaluate_coursework(self._core(gp=6) + [grading.CourseResult("E1", 3, 6)], "MTECH")
        self.assertTrue(v.complete)
        self.assertEqual(v.gpa, Decimal("6.00"))


class DateTests(TestCase):
    def test_add_months_clamps(self):
        self.assertEqual(add_months(date(2027, 1, 31), 1), date(2027, 2, 28))
        self.assertEqual(add_months(date(2028, 1, 31), 1), date(2028, 2, 29))
        self.assertEqual(add_months(date(2026, 11, 15), 3), date(2027, 2, 15))
        self.assertEqual(add_months(date(2026, 3, 15), -3), date(2025, 12, 15))

    def test_months_between(self):
        self.assertEqual(months_between(date(2026, 1, 15), date(2026, 7, 14)), 5)
        self.assertEqual(months_between(date(2026, 1, 15), date(2026, 7, 15)), 6)


class DurationTests(TestCase):
    reg = date(2026, 8, 1)

    def test_ceilings(self):
        self.assertEqual(durations.programme_ceiling(self.reg), date(2032, 8, 1))
        self.assertEqual(durations.programme_ceiling(self.reg, re_registered=True), date(2034, 8, 1))
        self.assertEqual(durations.programme_ceiling(self.reg, True, True), date(2036, 8, 1))

    def test_relaxation_eligibility(self):
        self.assertTrue(durations.relaxation_eligible("F", 0))
        self.assertTrue(durations.relaxation_eligible("M", 41))
        self.assertFalse(durations.relaxation_eligible("M", 40))

    def test_synopsis_by_mode(self):
        self.assertEqual(durations.synopsis_due(self.reg, "FT"), date(2030, 8, 1))
        self.assertEqual(durations.synopsis_due(self.reg, "PT"), date(2031, 8, 1))
        self.assertEqual(durations.synopsis_due(self.reg, "FT", extended=True), date(2031, 8, 1))

    def test_thesis_window(self):
        self.assertEqual(durations.thesis_submission_due(date(2030, 1, 10)), date(2030, 7, 10))
        self.assertEqual(durations.thesis_submission_due(date(2030, 1, 10), extended=True), date(2030, 10, 10))

    def test_progress_reports_roll_from_registration(self):
        dues = durations.progress_report_due_dates(date(2026, 8, 31), date(2027, 9, 1))
        self.assertEqual(dues, [date(2027, 2, 28), date(2027, 8, 31)])


class CalendarTests(TestCase):
    def test_rpet_always_on_a_weekend_in_window(self):
        for year in range(2026, 2060):
            for month in (6, 11):
                days = calendar_rules.rpet_dates(year, month)
                self.assertTrue(days)
                self.assertTrue(all(d.weekday() >= 5 and 20 <= d.day <= 25 for d in days))

    def test_year_events(self):
        kinds = [e.kind for e in calendar_rules.events_for_year(2027)]
        self.assertEqual(kinds.count("RPET"), 2)
        self.assertEqual(kinds.count("COURSEWORK_EXAM"), 2)
        self.assertEqual(kinds.count("TAC_REPORTS_TO_DC"), 2)
        reg = [e for e in calendar_rules.events_for_year(2027) if e.kind == "EXAM_REGISTRATION_DEADLINE"]
        self.assertEqual([e.start for e in reg], [date(2027, 4, 1), date(2027, 11, 1)])

    def test_viva_notice(self):
        self.assertEqual(calendar_rules.viva_notice_dates(date(2030, 3, 20)), (date(2030, 3, 5), date(2030, 3, 13)))
