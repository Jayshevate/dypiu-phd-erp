from datetime import date
from decimal import Decimal
from unittest import TestCase

from phd_rules import calendar_rules, durations
from phd_rules.dates import add_months, months_between


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
