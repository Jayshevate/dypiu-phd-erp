from datetime import date

from django.test import TestCase

from core import testing as t
from core.roles import Role
from deadlines import engine
from deadlines.models import AcademicEvent, Deadline, Notification
from lifecycle.models import ProgressReport
from scholars.models import ExtensionGrant
from scholars.services import request_extension
from supervision.services import propose_supervisor


class DeadlineEngineTests(TestCase):
    def setUp(self):
        t.seed()
        self.user = t.user(Role.SCHOLAR)
        self.s = t.scholar(user=self.user, registration_date=date(2026, 8, 1))

    def keys(self, today):
        return {d.key: d for d in engine.sync_scholar(self.s, today)}

    def test_rolling_deadlines_from_registration(self):
        d = self.keys(date(2026, 9, 24))
        self.assertEqual(d["SUPERVISOR"].due_date, date(2027, 2, 1))
        self.assertEqual(d["COURSEWORK"].due_date, date(2027, 8, 1))
        self.assertEqual(d["SYNOPSIS"].due_date, date(2030, 8, 1))
        self.assertEqual(d["MAX_DURATION"].due_date, date(2032, 8, 1))
        self.assertEqual([k for k in d if k.startswith("PROGRESS")], ["PROGRESS:1"])  # 6-month look-ahead
        self.assertNotIn("TAC", d)

    def test_part_time_synopsis_and_extension(self):
        self.s.category = "PT_EXTERNAL"
        self.s.save()
        self.assertEqual(self.keys(date(2026, 9, 24))["SYNOPSIS"].due_date, date(2031, 8, 1))
        grant = request_extension(self.s, ExtensionGrant.Kind.SYNOPSIS)
        t.approve_all(grant.approval)
        self.assertEqual(self.keys(date(2026, 9, 24))["SYNOPSIS"].due_date, date(2032, 8, 1))

    def test_met_flags_and_tac_deadline(self):
        t.approve_all(propose_supervisor(self.s, t.faculty()).approval)
        d = self.keys(date(2026, 9, 24))
        self.assertTrue(d["SUPERVISOR"].met)
        self.assertFalse(d["TAC"].met)
        ProgressReport.objects.create(scholar=self.s, period_no=1, due_date=date(2027, 2, 1), submitted_on=date(2027, 1, 30))
        self.assertTrue(self.keys(date(2027, 1, 31))["PROGRESS:1"].met)

    def test_tiered_notifications_are_idempotent(self):
        engine.sync_scholar(self.s, date(2027, 1, 2))
        # Supervisor allocation and progress report #1 are both due 1 Feb.
        self.assertEqual(engine.dispatch(date(2027, 1, 2)), 2)   # 30-day tier
        self.assertEqual(engine.dispatch(date(2027, 1, 3)), 0)   # same tier, no repeat
        self.assertEqual(engine.dispatch(date(2027, 1, 25)), 2)  # 7-day tier
        rd = t.user(Role.RD_OFFICE)
        engine.sync_scholar(self.s, date(2027, 2, 2))
        engine.dispatch(date(2027, 2, 2))
        overdue = Notification.objects.filter(tier=0, deadline__key="SUPERVISOR")
        self.assertEqual({n.recipient for n in overdue}, {self.user, rd})

    def test_global_calendar_sync_does_not_overwrite(self):
        self.assertEqual(engine.sync_global_calendar(2027), 14)
        ev = AcademicEvent.objects.get(kind="RPET", start__month=6)
        ev.start = ev.end = date(2027, 6, 27)
        ev.save()
        self.assertEqual(engine.sync_global_calendar(2027), 0)
        ev.refresh_from_db()
        self.assertEqual(ev.start, date(2027, 6, 27))

    def test_run_command(self):
        from django.core.management import call_command
        call_command("run_deadline_engine", "--today", "2026-09-24", verbosity=0)
        self.assertTrue(Deadline.objects.filter(scholar=self.s).exists())
        self.assertTrue(AcademicEvent.objects.filter(start__year=2027).exists())
