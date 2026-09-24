# DYPIU PhD ERP — Requirements Extract

Source documents:
- `PhD_Regulations_and_Appendices_DYPIU_1_July_2026.pdf` (regulations + ~35 forms/appendices)
- `PhDAcademicFramework_ImplementationGuidelines.pdf` (journey calendar, coursework design, TA allocation, TA conduct policy)
- `DYPIU_PhD_Calendar03082026_SS.xls` (journey calendar, yearly recurring calendar, duration/extension limits)
- `1790259689417_Recording.m4a` — **not yet processed**; merge any extra requirements here.

## 1. Core entities

| Entity | Key attributes | Notes |
|---|---|---|
| Scholar | PRN, name, category (FT / PT: Sponsored, Sister Institution, External, Internal), status (JRF → SRF), admission date, registration date, extension history | JRF→SRF after 2 years + DC recommendation |
| Application | applicant details, SOP, CV, category, entrance cycle | Feeds Admission |
| Supervisor / Co-Supervisor | designation, current load, cap (4/6/8), years of service remaining, external flag | Cap enforced at assignment time |
| TAC | 2 interdisciplinary members + supervisor | Co-supervisor cannot sit on TAC |
| Course / Coursework record | SIS7001–7008, credits, category, marks, grade, GPA | |
| Research Proposal | title, committee, outcome (4 outcomes) | 2 fails on resubmission = cancelled |
| Progress Report | 6-month cycle, TAC outcome, warnings | 2 warnings → DC |
| Pre-submission Synopsis | submission, TAC clearance, deadline (4 yr FT / 5 yr PT) | |
| Thesis | plagiarism %, 3 examiners of ≥8 nominated, reports, outcome | |
| DPEP / Viva | panel, date, report, outcome | |
| Degree Award | approval chain, certificate | |
| TA Assignment | ≤20 h/week, ≤10 contact, per semester, feedback | |
| Financial Grant Claim | ₹50,000 lifetime cap, 6-month gap | |
| Leave / Extension | annual 25 days, maternity 240 days, career break, DC extension | |
| Committees | DC / SDRC / DPEP / Examination Committee | |

## 2. Roles
Candidate/Scholar · Supervisor · Co-Supervisor · TAC Member · SDRC · Doctoral Committee · Dean/Dy. Dean R&D · Vice-Chancellor · Controller of Examinations · Registrar · HR Office · R&D Office · Course Coordinator · External Examiner · DPEP Member.

## 3. Lifecycle

| # | Phase | Timing | Gate |
|---|---|---|---|
| 1 | Admission | Vacancies Mar/Sep; RPET Jun/Nov (≥50%); interview (≥50%) | Provisional registration + fee |
| 2 | Coursework | Months 0–12; exams early Dec/May | Each course ≥ C+, GPA ≥ 6.0, ≤ 2 attempts in 2 years |
| 3 | Supervisor & TAC | Supervisor within 6 months of registration | DC-approved supervisor; TAC within 1 month |
| 4 | Research Proposal | 6 months after coursework | Recommended |
| 5 | Progress Monitoring | 6-monthly; ≥2 TAC reports/year | 2 unsatisfactory → DC review |
| 6 | Pre-submission Synopsis | ≤4 yr FT / ≤5 yr PT (+1 yr) | TAC clears synopsis |
| 7 | Thesis Submission | ≤6 months after synopsis (+3 months) | Plagiarism ≤10%, fees paid |
| 8 | Thesis Evaluation | 3 of ≥8 nominees; ≤3 months | ≥2 of 3 commend |
| 9 | Viva | ≤2 months after reports | DPEP: satisfactory |
| 10 | Degree Award | DPEP → DC → Dean R&D → COE → Registrar → VC | Certificate issued; COE informs Academic Council |

Duration: minimum 3 years; maximum 6; +2 re-registration (8); +2 female / PwD > 40% (10).

## 4. Recurring calendar
- Mar & Sep: vacancy notification / admission cycle opens
- Jun 20–25 & Nov 20–25 (Sat/Sun): RPET
- Early May & early Dec: coursework exams; registration closes 1 month before
- Elective registration closes 1 month before semester start
- Every 6 months from each scholar's registration: progress report
- Before June & before November: TAC reports to DC (≥2/year)
- Viva: candidate told 15 days before; open invitation ≥ 1 week before

## 5. Grading
A+ 91–100 (10) · A 81–90 (9) · B+ 71–80 (8) · B 61–70 (7) · C+ 51–60 (6) · C 41–50 (5) · D 40 (4) · F < 40 (0) · I / RW / AB.

Courses: SIS7001 Research Methodology & Ethics (4, ethics sub-module cleared independently) · SIS7002 Statistics (2) · SIS7003 Computational Thinking (3) · SIS7005 Industrial Training (2, 5-component rubric) · SIS7006 Conference/Workshop (1, points matrix + reflective-note gate) · SIS7007 Research Seminar (2, 3-criteria rubric) · SIS7008 Electives (3 each).
Credits: B.Tech 23 · M.Tech/M.E./M.Pharm 17 · Integrated/M.Sc/MCA/MBA/M.Com 20.

## 6. Teaching assistantship
JRF ₹31,000 + HRA → SRF ₹36,000 + HRA after 2 years + DC recommendation. TA registration per semester, ≤20 h/week (≤10 contact), ≤10 semesters. Register → form + fee receipt within 2 days of fee deadline → R&D shares list with Schools → induction → end-of-semester feedback (faculty, director, students) gates next semester.

## 7. Edge cases
- Category-dependent workflows (FT vs four PT sub-categories).
- Rolling per-scholar deadlines vs fixed calendar windows.
- Multi-level approval overrides (VC / Dean R&D / DC).
- Capacity and TAC uniqueness enforced at write time.
- Official forms are the audit trail of phase transitions.
