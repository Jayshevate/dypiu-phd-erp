"""Default approval chains. These mirror the approval authorities named in the
regulations extract; they are seeded once and then owned by the R&D office
(editable in the admin). ``own`` marks steps that must be performed by the
scholar's own supervisor."""
from .roles import Role as R

DEFAULT_CHAINS = {
    "SUPERVISOR_ALLOCATION": ("Supervisor / co-supervisor allocation", [R.SDRC, R.DC]),
    "TAC_FORMATION": ("Thesis Advisory Committee formation", [R.DC]),
    "SUPERVISOR_CHANGE": ("Change / discontinuation of supervisor", [R.DC, R.DEAN_RD]),
    "COURSEWORK_THIRD_ATTEMPT": ("Third coursework attempt (override)", [R.DC, R.VC]),
    "PROPOSAL_EVALUATION": ("Research proposal recommendation", [R.DC]),
    "SYNOPSIS_EXTENSION": ("1-year pre-submission synopsis extension", [R.DC, R.DEAN_RD]),
    "THESIS_SUBMISSION_EXTENSION": ("3-month thesis submission extension", [R.DC, R.DEAN_RD]),
    "RE_REGISTRATION": ("Re-registration (+2 years)", [R.DC, R.DEAN_RD, R.VC]),
    "DURATION_RELAXATION": ("Female / PwD relaxation (+2 years)", [R.DC, R.DEAN_RD, R.VC]),
    "EXAMINER_SELECTION": ("Selection of 3 examiners from the panel", [R.DEAN_RD, R.VC]),
    "DEGREE_AWARD": ("Degree award", [R.DC, R.DEAN_RD, R.COE, R.REGISTRAR, R.VC]),
    "SRF_PROMOTION": ("JRF to SRF promotion", [(R.SUPERVISOR, True), R.DC, R.DEAN_RD, R.HR]),
    "GRANT_CLAIM": ("Conference / financial support claim", [(R.SUPERVISOR, True), R.RD_OFFICE, R.DEAN_RD]),
    "LEAVE": ("Leave", [(R.SUPERVISOR, True), R.RD_OFFICE]),
    "MATERNITY_LEAVE": ("Maternity leave / career break", [(R.SUPERVISOR, True), R.DC, R.DEAN_RD]),
    "WITHDRAWAL": ("Withdrawal / cancellation of registration", [R.DC, R.DEAN_RD, R.VC]),
}


def seed_chains():
    from .models import ApprovalChain, ApprovalStep

    created = 0
    for code, (name, steps) in DEFAULT_CHAINS.items():
        chain, is_new = ApprovalChain.objects.get_or_create(code=code, scholar_category="", defaults={"name": name})
        if not is_new:
            continue
        created += 1
        for order, step in enumerate(steps, start=1):
            role, own = step if isinstance(step, tuple) else (step, False)
            ApprovalStep.objects.create(chain=chain, order=order, role=role, own_scholar_only=own)
    return created
