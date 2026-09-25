import getpass

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from identity import audit, services
from identity.authz import holds, require
from identity.capabilities import ALLOWED_SCOPES, DERIVED_CAPABILITIES, Capability, ScopeType
from identity.models import Person

GRANT = "identity.capability.grant"


class Command(BaseCommand):
    help = ("Provision ONE institutional identity: a normal Django login + Person + one institution-scoped "
            "capability grant, performed by an existing active SYSTEM_ADMIN through identity.services "
            "(authorised and audited). The password is prompted, never taken from the command line.")

    def add_arguments(self, parser):
        parser.add_argument("--actor-email", required=True, help="Email of the SYSTEM_ADMIN performing this")
        parser.add_argument("--username", required=True, help="New login to create")
        parser.add_argument("--email", required=True)
        parser.add_argument("--name", required=True)
        parser.add_argument("--capability", required=True, help="e.g. ACADEMIC_ADMIN (institution scope)")
        parser.add_argument("--basis", required=True, help="Authorising order / reference")

    def _deny(self, actor, reasons):
        audit.record(action=GRANT, allowed=False, actor=actor, reasons=reasons)
        raise CommandError("; ".join(reasons))

    def _password(self, user):
        password = getpass.getpass("Password for the new login: ")
        if password != getpass.getpass("Password (again): "):
            raise CommandError("Passwords do not match")
        try:
            validate_password(password, user)
        except ValidationError as e:
            raise CommandError("; ".join(e.messages))
        return password

    def handle(self, *args, actor_email, username, email, name, capability, basis, **options):
        email = email.strip().lower()
        actor = Person.objects.filter(email__iexact=actor_email.strip(), is_active=True).first()
        if actor is None:
            raise CommandError(f"No active person with email {actor_email!r}")

        # Everything is checked before anything is written, so a refusal leaves no
        # half-provisioned login. Denials are audited here, outside any transaction.
        if not holds(actor, Capability.SYSTEM_ADMIN):
            self._deny(actor, ["provisioning requires an active SYSTEM_ADMIN actor"])
        if capability not in Capability.values:
            raise CommandError(f"Unknown capability {capability!r}")
        if capability in DERIVED_CAPABILITIES:
            self._deny(actor, [f"{capability} is derived and cannot be granted"])
        if capability == Capability.SYSTEM_ADMIN:
            self._deny(actor, ["SYSTEM_ADMIN is not provisioned by this command"])
        if ScopeType.INSTITUTION not in ALLOWED_SCOPES.get(capability, set()):
            raise CommandError(f"{capability} cannot be scoped to {ScopeType.INSTITUTION}")
        if email == actor.email or (actor.user_id and actor.user.username == username):
            self._deny(actor, ["separation of duties: you cannot grant a capability to yourself"])
        if not basis.strip():
            raise CommandError("A basis (order number / approval reference) is required")
        try:
            require(actor, "identity.person.manage")
        except PermissionDenied as e:
            raise CommandError(str(e))
        ok, reasons, _ = services._granting_authority(actor, capability, services._target_scope(ScopeType.INSTITUTION))
        if not ok:
            self._deny(actor, reasons)

        User = get_user_model()
        if User.objects.filter(username__iexact=username).exists():
            raise CommandError(f"Login {username!r} already exists; this command only creates new logins")
        if Person.objects.filter(email__iexact=email).exists():
            raise CommandError(f"A person with email {email} already exists")
        user = User(username=username, email=email, is_staff=False, is_superuser=False)
        password = self._password(user)

        # The services re-check authorisation themselves; all writes are one unit.
        try:
            with transaction.atomic():
                user.set_password(password)
                user.save()
                person = services.provision_person(actor, full_name=name, email=email)
                services.link_user(actor, person, user)
                grant = services.grant_capability(actor, person, capability, ScopeType.INSTITUTION, basis=basis)
        except (PermissionDenied, ValidationError) as e:
            raise CommandError("; ".join(getattr(e, "messages", [str(e)])))
        self.stdout.write(self.style.SUCCESS(
            f"Provisioned {person} (login {user.username!r}) with {grant.capability} @ {grant.scope_label} "
            f"(grant #{grant.pk})"))
