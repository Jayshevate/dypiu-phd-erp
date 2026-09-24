# Open questions (confirm against the source PDFs)

Each item below is a value or interpretation the requirements extract doesn't
pin down. The current default is in `phd_rules/policy.py` (or the file named).
Changing it is a one-line edit.

| # | Question | Current default |
|---|---|---|
| 1 | Do co-supervisions count towards a faculty member's 4/6/8 cap? | Yes (`COUNT_CO_SUPERVISION_TOWARDS_CAP`) |
| 2 | Minimum years of service remaining to take a new scholar? | 3 (`MIN_SERVICE_YEARS_REMAINING`) |
| 3 | Exact meaning of the "TAC uniqueness" rule | A faculty member may sit on only one TAC among the scholars of the same supervisor |
| 4 | "Interdisciplinary" TAC member means a different department from the scholar's? | Yes, enforced |
| 5 | The 4 research-proposal outcomes, and what counts as a "fail" | Recommended / Minor modifications (pass); Resubmit / Not recommended (fail); 2 fails → cancelled |
| 6 | Examiner recommendation categories | Commend / Revise & resubmit / Not commend |
| 7 | Examiner "60-day / 1-month" escalation | Remind at 60 days, replace 30 days after the reminder |
| 8 | Does the female/PwD +2 years stack only after re-registration? | Independent extension, each granted once; max 10 years total |
| 9 | Exact RPET exemption list | GATE, NET, CSIR, SLET, JRF |
| 10 | Semester start dates (for elective-registration cut-off) | 1 Jan & 1 Jul (`calendar_rules.SEMESTER_STARTS`) |
| 11 | "Early May / early Dec" exam window | 1–10 of the month (`calendar_rules.EXAM_WINDOW`) |
| 12 | Leave year for the 25-day annual cap | Calendar year |
| 13 | Rubric weights for SIS7005 / SIS7006 / SIS7007 | Not seeded; enter them in the admin (Course → components) |
| 14 | Which courses are core for every entry category | SIS7001–7003, 7005–7007 (14 credits). Electives fill the rest: 23 − 14 = 3 electives (B.Tech), 1 (M.Tech), 2 (PG). This matches the stated totals. |
| 15 | Approval chains per workflow | `core/seed.py`; editable in the admin |
| 16 | Marks rounding | Half-up to a whole mark before banding (90.5 → A+) |
| 17 | Contents of the audio recording `1790259689417_Recording.m4a` | Not processed |
