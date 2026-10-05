from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Branch, ShopSettings, Tenant


class Command(BaseCommand):
    help = "Provision a business, its first branch, and owner (password entered securely)."

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True)
        parser.add_argument("--slug", required=True)
        parser.add_argument("--owner", required=True, help="Globally unique owner username")
        parser.add_argument("--branch", default="Main branch")
        parser.add_argument("--code", default="MAIN")

    @transaction.atomic
    def handle(self, **options):
        User = get_user_model()
        if Tenant.objects.filter(slug=options["slug"]).exists():
            raise CommandError("Business slug already exists.")
        if User.objects.filter(username=options["owner"]).exists():
            raise CommandError("Username already exists.")
        import getpass
        password = getpass.getpass("Owner password: ")
        if not password:
            raise CommandError("Password is required.")
        tenant = Tenant.objects.create(name=options["name"], slug=options["slug"])
        branch = Branch.objects.create(tenant=tenant, name=options["branch"], code=options["code"])
        ShopSettings.objects.create(tenant=tenant, shop_name=options["name"])
        user = User.objects.create_user(username=options["owner"], password=password,
            role=User.Role.OWNER, tenant=tenant)
        user.branches.add(branch)
        self.stdout.write(self.style.SUCCESS(f"Created {tenant.name}: {branch.name}, owner {user.username}"))
