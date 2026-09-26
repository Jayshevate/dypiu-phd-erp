"""Person + login provisioning for institutional records, with one-time
activation (Step A2).

- A faculty or scholar record gets a Person (matched by its institutional
  e-mail, never by name) through `services.provision_person`, and the profile is
  linked with `link_faculty_profile` / `link_scholar_profile`. The FACULTY /
  SCHOLAR capability is DERIVED from that link; nothing is granted here.
- A login is created with an unusable password. The person sets their own
  password through a single-use, expiring activation token (Django's token
  generator, with its own salt). The token is never stored and never audited.
  Once a password is set the token stops working, and activation cannot be
  used as a password reset for an already-activated account.
- Every step is authorised (`identity.person.manage`) and audited by the
  identity services. Checks run before any write, so a refusal leaves nothing
  half-created. The identity provider decision (D-IdP) may later replace
  password activation."""
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

from core.models import Faculty
from scholars.models import Scholar

from . import audit, services
from .authz import require
from .models import Person


class ActivationTokenGenerator(PasswordResetTokenGenerator):
    key_salt = "identity.provisioning.ActivationTokenGenerator"


activation_tokens = ActivationTokenGenerator()


def login_state(person: Person) -> str:
    if person.user_id is None:
        return "NO_LOGIN"
    return "ACTIVE" if person.user.has_usable_password() else "PENDING_ACTIVATION"


def _profile_kind(profile):
    if isinstance(profile, Faculty):
        return "faculty"
    if isinstance(profile, Scholar):
        return "scholar"
    raise ValidationError("Only faculty and scholar records can be provisioned")


def provision_for_record(actor, profile, *, create_login=True, request=None) -> Person:
    """Create the Person for a faculty/scholar record, link the record and
    (optionally) create a login awaiting activation."""
    kind = _profile_kind(profile)
    require(actor, "identity.person.manage", None, request=request)
    email = (profile.email or "").strip().lower()
    if not email:
        raise ValidationError("The record has no institutional e-mail; add one before provisioning")
    linked = Person.objects.filter(**{f"{kind}_profile": profile}).first()
    if linked is not None:
        raise ValidationError(f"This {kind} record is already linked to {linked.full_name}")
    if Person.objects.filter(email__iexact=email).exists():
        # Never merge silently: an existing person with this e-mail needs a reviewed, manual link.
        raise ValidationError(f"A person with e-mail {email} already exists; link the record to it by review")
    User = get_user_model()
    if create_login and User.objects.filter(username__iexact=email).exists():
        raise ValidationError(f"A login named {email} already exists")

    with transaction.atomic():
        person = services.provision_person(actor, full_name=profile.name, email=email, request=request)
        link = services.link_faculty_profile if kind == "faculty" else services.link_scholar_profile
        link(actor, person, profile, request=request)
        if create_login:
            user = User(username=email, email=email)
            user.set_unusable_password()
            user.save()
            services.link_user(actor, person, user, request=request)
    return person


def issue_activation(actor, person: Person, *, request=None) -> dict:
    """A fresh single-use activation token for a login that has never been activated."""
    require(actor, "identity.person.manage", None, request=request)
    services._protect_system_admin(actor, person, "identity.login.activation", request)
    if actor is not None and actor.pk == person.pk:
        services._deny("identity.login.activation", actor, ["cannot issue an activation for yourself"], person,
                       request)
    if not person.is_active:
        raise ValidationError("This identity is deactivated")
    if person.user_id is None:
        raise ValidationError("This person has no login")
    user = person.user
    if user.has_usable_password():
        raise ValidationError("This login is already activated; activation cannot be used to reset a password")
    if user.last_login is not None:
        # A login that has ever signed in (e.g. through a future identity provider) is never "activated" again.
        raise ValidationError("This login has already been used; activation is only for new logins")
    audit.record(action="identity.login.activation", allowed=True, actor=actor, resource=person, request=request,
                 after={"user_id": user.pk})
    return {"uid": urlsafe_base64_encode(force_bytes(user.pk)), "token": activation_tokens.make_token(user)}


def _user_for(uid):
    try:
        pk = force_str(urlsafe_base64_decode(uid))
        return get_user_model().objects.get(pk=pk)
    except (TypeError, ValueError, OverflowError, get_user_model().DoesNotExist):
        return None


def activate(uid: str, token: str, password: str, *, request=None) -> Person:
    """Public: the person sets their own password with a valid activation token."""
    user = _user_for(uid)
    person = getattr(user, "person", None) if user is not None else None
    if (user is None or person is None or not person.is_active or user.has_usable_password()
            or user.last_login is not None or not activation_tokens.check_token(user, token)):
        audit.record(action="identity.login.activate", allowed=False, actor=person, request=request,
                     reasons=["invalid, expired or already used activation link"])
        raise PermissionDenied("This activation link is invalid, expired or already used")
    validate_password(password, user)
    with transaction.atomic():
        user.set_password(password)
        user.save(update_fields=["password"])
        audit.record(action="identity.login.activate", allowed=True, actor=person, resource=person, request=request,
                     after={"user_id": user.pk})
    return person
