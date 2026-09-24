from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

from core.roles import Role
from core.seed import seed_chains
from coursework.seed import seed_courses


class Command(BaseCommand):
    help = "Create role groups, default approval chains and the SIS70xx course catalogue (idempotent)."

    def handle(self, *args, **options):
        for role in Role:
            Group.objects.get_or_create(name=role.value)
        chains = seed_chains()
        courses = seed_courses()
        self.stdout.write(self.style.SUCCESS(
            f"Roles: {len(Role)} ensured; approval chains created: {chains}; courses created: {courses}"
        ))
