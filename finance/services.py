from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from core.approvals import on_decision, start_approval
from phd_rules import policy
from phd_rules.dates import add_months

from .models import GrantClaim, Semester, TAAssignment, TAFeedback, TARegistration


# --- Teaching assistantship ----------------------------------------------------

def ta_eligibility_problems(scholar, semester: Semester, form_date) -> list[str]:
    problems = []
    done = scholar.ta_registrations.exclude(semester=semester).count()
    if done >= policy.TA_MAX_SEMESTERS:
        problems.append(f"TA limit of {policy.TA_MAX_SEMESTERS} semesters reached")
    if form_date > semester.fee_deadline + timedelta(days=policy.TA_FORM_DAYS_AFTER_FEE_DEADLINE):
        problems.append(f"Form must be submitted within {policy.TA_FORM_DAYS_AFTER_FEE_DEADLINE} days of the fee deadline")
    previous = (scholar.ta_registrations.filter(semester__start_date__lt=semester.start_date)
                .order_by("-semester__start_date").first())
    if previous:
        feedback = {f.source: f.satisfactory for f in previous.feedback.all()}
        missing = [s.label for s in TAFeedback.Source if s not in feedback]
        if missing:
            problems.append(f"Feedback for {previous.semester} missing from: {', '.join(missing)}")
        elif not all(feedback.values()):
            problems.append(f"Feedback for {previous.semester} was not satisfactory")
    return problems


@transaction.atomic
def register_for_ta(scholar, semester: Semester, form_date, **fields) -> TARegistration:
    problems = ta_eligibility_problems(scholar, semester, form_date)
    if problems:
        raise ValidationError(problems)
    return TARegistration.objects.create(scholar=scholar, semester=semester, form_submitted_on=form_date, **fields)


@transaction.atomic
def assign_ta(registration: TARegistration, **fields) -> TAAssignment:
    registration = TARegistration.objects.select_for_update().get(pk=registration.pk)
    a = TAAssignment(registration=registration, **fields)
    if a.contact_hours_per_week > a.hours_per_week:
        raise ValidationError("Contact hours cannot exceed total hours")
    totals = registration.assignments.aggregate(h=Sum("hours_per_week"), c=Sum("contact_hours_per_week"))
    hours = (totals["h"] or 0) + a.hours_per_week
    contact = (totals["c"] or 0) + a.contact_hours_per_week
    if hours > policy.TA_MAX_HOURS_PER_WEEK:
        raise ValidationError(f"TA load {hours} h/week exceeds {policy.TA_MAX_HOURS_PER_WEEK}")
    if contact > policy.TA_MAX_CONTACT_HOURS_PER_WEEK:
        raise ValidationError(f"Contact load {contact} h/week exceeds {policy.TA_MAX_CONTACT_HOURS_PER_WEEK}")
    a.save()
    if registration.state in (TARegistration.State.REGISTERED, TARegistration.State.LISTED):
        registration.state = TARegistration.State.INDUCTED
        registration.save(update_fields=["state"])
    return a


# --- Financial support / conference grants ---------------------------------------

def grant_committed(scholar) -> Decimal:
    approved = scholar.grant_claims.filter(state=GrantClaim.State.APPROVED).aggregate(t=Sum("amount_approved"))["t"]
    pending = scholar.grant_claims.filter(state=GrantClaim.State.PENDING).aggregate(t=Sum("amount_claimed"))["t"]
    return (approved or Decimal(0)) + (pending or Decimal(0))


def grant_balance(scholar) -> Decimal:
    return policy.GRANT_LIFETIME_CAP - grant_committed(scholar)


@transaction.atomic
def submit_grant_claim(scholar, event_name, event_date, amount, user=None, **fields) -> GrantClaim:
    amount = Decimal(str(amount))
    problems = []
    if amount > grant_balance(scholar):
        problems.append(f"Claim of Rs {amount} exceeds remaining lifetime balance Rs {grant_balance(scholar)}")
    others = scholar.grant_claims.exclude(state=GrantClaim.State.REJECTED)
    for c in others:
        lo, hi = sorted([c.event_date, event_date])
        if add_months(lo, policy.GRANT_MIN_GAP_MONTHS) > hi:
            problems.append(f"Must be at least {policy.GRANT_MIN_GAP_MONTHS} months from supported event "
                            f"'{c.event_name}' ({c.event_date})")
    if problems:
        raise ValidationError(problems)
    claim = GrantClaim.objects.create(scholar=scholar, event_name=event_name, event_date=event_date,
                                      amount_claimed=amount, **fields)
    claim.approval = start_approval("GRANT_CLAIM", summary=f"{scholar.prn}: Rs {amount} for {event_name}",
                                    scholar=scholar, target=claim, user=user)
    claim.save(update_fields=["approval"])
    return claim


@on_decision("GRANT_CLAIM")
def _close_grant(req, approved):
    claim = GrantClaim.objects.filter(approval=req).first()
    if claim is None:
        return
    claim.state = GrantClaim.State.APPROVED if approved else GrantClaim.State.REJECTED
    if approved and claim.amount_approved is None:
        claim.amount_approved = claim.amount_claimed
    claim.save(update_fields=["state", "amount_approved"])
