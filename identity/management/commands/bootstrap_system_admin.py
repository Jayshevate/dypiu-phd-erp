from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.management.base import BaseCommand, CommandError

from identity.services import bootstrap_system_admin


class Command(BaseCommand):
    help = ("Create the first SYSTEM_ADMIN (server-side, audited). Refused once any active "
            "SYSTEM_ADMIN exists. Replaces the old client-side 'seed admin email'.")

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--name", required=True)
        parser.add_argument("--username", help="Existing login to link (optional)")
        parser.add_argument("--basis", required=True, help="Authorising order / reference")

    def handle(self, *args, email, name, username=None, basis, **options):
        user = None
        if username:
            try:
                user = get_user_model().objects.get(username=username)
            except get_user_model().DoesNotExist:
                raise CommandError(f"No user {username!r}")
        try:
            grant = bootstrap_system_admin(full_name=name, email=email, user=user, basis=basis)
        except PermissionDenied as e:
            raise CommandError(str(e))
        self.stdout.write(self.style.SUCCESS(f"SYSTEM_ADMIN granted to {grant.person} (grant #{grant.pk})"))
