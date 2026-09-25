"""Grade from a configured scheme. Nothing here is hard-coded: the scheme is
the `grading.scheme` parameter (currently AMBIGUOUS, RD-01/RD-02/RD-40)."""
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError


def apply_rounding(total: Decimal, scheme: dict) -> Decimal:
    rounding = scheme.get("rounding")
    if rounding == "HALF_UP_TO_INTEGER":
        return total.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if rounding in (None, "NONE"):
        return total
    raise ValidationError(f"Unknown rounding rule {rounding!r} in grading.scheme (RD-40)")


def grade_for(total: Decimal, scheme: dict) -> tuple[str, Decimal]:
    if scheme.get("mode") != "ABSOLUTE":
        raise ValidationError(
            f"grading.scheme mode {scheme.get('mode')!r} has no defined computation; relative grading "
            "requires an institutional method (RD-01)")
    value = apply_rounding(Decimal(total), scheme)
    for letter, lower, point in scheme["bands"]:
        if value >= Decimal(str(lower)):
            return letter, Decimal(str(point))
    letter, point = scheme["fail"]
    return letter, Decimal(str(point))


def grade_at_least(grade: str, minimum: str, scheme: dict) -> bool:
    order = scheme["order"]
    if grade not in order or minimum not in order:
        raise ValidationError(f"Grade {grade!r} or {minimum!r} missing from grading.scheme order")
    return order.index(grade) >= order.index(minimum)
