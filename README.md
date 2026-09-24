# DYPIU PhD ERP

Lifecycle management for PhD scholars at D Y Patil International University,
Akurdi, Pune. It is built from the PhD Regulations (1 July 2026), the Academic
Framework Implementation Guidelines and the PhD Calendar.

- **Requirements:** [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md)
- **Design** (data model, state machine, deadline engine, stack, build plan, edge cases): [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- **Assumptions to confirm:** [docs/OPEN_QUESTIONS.md](docs/OPEN_QUESTIONS.md)

## What works today

- 10-phase lifecycle state machine with gate reasons, an audit log and automatic cancellation rules
- Configurable multi-level approval chains (16 seeded), including category-specific chains and "own supervisor" steps
- Supervisor caps (4/6/8), external/service-remaining checks and TAC composition rules, all enforced at write time
- Coursework: 8-band grading, GPA, credit totals by entry qualification, ethics sub-module, rubric/gate components, 2-attempt/2-year limit with VC override
- Proposal, progress (warnings → DC review), synopsis, thesis (plagiarism, min/max duration), examiner panel (≥8 → 3, reminder/replacement), viva timing, degree award chain
- Dual-clock deadlines: an organisation calendar plus rolling per-scholar deadlines, with 30/7/1-day and overdue reminders
- Teaching assistantship (hour caps, 10-semester limit, feedback gate) and conference grants (₹50k lifetime, 6-month gap)
- Leave caps (25 days annual, 240 days maternity) and extensions (synopsis, thesis, re-registration, relaxation)
- Django admin back office plus a role-scoped dashboard at `/scholars/`

## Run locally

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_reference_data        # roles, approval chains, SIS70xx courses
python manage.py createsuperuser
python manage.py runserver                  # http://localhost:8000/admin/
python manage.py run_deadline_engine        # schedule daily (cron)
python manage.py test                       # 67 tests
```

For PostgreSQL, install `psycopg[binary]` and set `POSTGRES_DB`,
`POSTGRES_USER`, `POSTGRES_PASSWORD` and `POSTGRES_HOST`.

## Changing a rule

Every regulation number lives in `phd_rules/policy.py`. Approval chains live
in the admin (**Core → Approval chains**). Calendar defaults live in
`phd_rules/calendar_rules.py`.
