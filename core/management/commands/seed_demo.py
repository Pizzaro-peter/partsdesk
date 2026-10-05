"""Creates demo users and a small catalog so a new install isn't empty. Idempotent."""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from catalog.models import BranchStock, Category, Part, PartNumber, Supplier, Vehicle
from core.models import Branch, ShopSettings, User
from sales.models import Customer, CustomerVehicle
from stock.services import record_movement

USERS = [
    ("owner", "owner", "Owner", "Shop"),
    ("manager", "manager", "Mary", "Manager"),
    ("counter1", "counter", "Chipo", "Banda"),
    ("mechanic1", "mechanic", "Moses", "Phiri"),
    ("storekeeper1", "storekeeper", "Sarah", "Tembo"),
]


class Command(BaseCommand):
    help = "Create demo users, suppliers, vehicles and parts. Safe to run more than once."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", required=True)
        parser.add_argument("--branch", default="MAIN")

    @transaction.atomic
    def handle(self, **opts):
        branch = Branch.objects.filter(tenant__slug=opts["tenant"], code=opts["branch"]).first()
        if branch is None:
            from django.core.management.base import CommandError
            raise CommandError("Choose an existing tenant slug and branch code.")
        tenant = branch.tenant
        ShopSettings.objects.get_or_create(tenant=tenant, defaults=dict(
            shop_name="Kabwata Auto Spares", currency_symbol="K", tax_name="VAT", tax_rate=Decimal("16"),
            default_labour_rate=Decimal("150")))

        for username, role, first, last in USERS:
            if opts["tenant"] != "original-business":
                username = f"{tenant.slug}_{username}"
            user, created = User.objects.get_or_create(
                username=username, defaults=dict(tenant=tenant, role=role, first_name=first, last_name=last))
            if created:
                user.set_password("partsdesk123")
                user.save()
            if user.tenant_id == tenant.pk:
                user.branches.add(branch)
        self.stdout.write(self.style.SUCCESS(f"Users ready (password for all: partsdesk123): {[u[0] for u in USERS]}"))

        bosch = Supplier.objects.get_or_create(tenant=tenant, name="Bosch Distributors Zambia", defaults=dict(
            contact_person="Alice Mwansa", phone="+260-97-0000001", payment_terms="Net 30"))[0]
        local = Supplier.objects.get_or_create(tenant=tenant, name="Lusaka Parts Wholesale", defaults=dict(
            contact_person="John Zulu", phone="+260-96-0000002", payment_terms="Cash on delivery"))[0]

        cat_brakes = Category.objects.get_or_create(tenant=tenant, name="Brakes")[0]
        cat_filters = Category.objects.get_or_create(tenant=tenant, name="Filters")[0]
        cat_electrical = Category.objects.get_or_create(tenant=tenant, name="Electrical")[0]
        cat_engine = Category.objects.get_or_create(tenant=tenant, name="Engine")[0]

        corolla = Vehicle.objects.get_or_create(tenant=tenant, make="Toyota", model="Corolla", year_from=2007, year_to=2013, engine="1.8L")[0]
        hilux = Vehicle.objects.get_or_create(tenant=tenant, make="Toyota", model="Hilux", year_from=2012, year_to=2015, engine="2.5L Diesel")[0]
        golf = Vehicle.objects.get_or_create(tenant=tenant, make="Volkswagen", model="Golf", year_from=2010, year_to=2014, engine="1.6L")[0]

        parts = [
            dict(sku="BRK-1001", name="Front brake pad set", category=cat_brakes, brand="Bosch",
                 condition="new", oem_number="04465-02240", cost_price="180", sell_price="260", reorder_level=4,
                 reorder_qty=10, preferred_supplier=bosch, fits=[corolla], bin_location="A1-01", qty="12"),
            dict(sku="FLT-2001", name="Oil filter", category=cat_filters, brand="Mann", condition="new",
                 oem_number="90915-YZZD4", cost_price="18", sell_price="35", reorder_level=10, reorder_qty=30,
                 preferred_supplier=local, fits=[corolla, hilux], bin_location="B2-04", qty="3"),
            dict(sku="FLT-2002", name="Air filter", category=cat_filters, brand="Mann", condition="new",
                 cost_price="22", sell_price="42", reorder_level=6, reorder_qty=20, preferred_supplier=local,
                 fits=[golf], bin_location="B2-05", qty="14"),
            dict(sku="ELE-3001", name="12V car battery 60Ah", category=cat_electrical, brand="Exide",
                 condition="new", cost_price="450", sell_price="620", reorder_level=2, reorder_qty=6,
                 preferred_supplier=local, is_universal=True, bin_location="C1-01", qty="5"),
            dict(sku="ENG-4001", name="Timing belt kit", category=cat_engine, brand="Gates", part_type="aftermarket",
                 condition="new", oem_number="13568-09010", cost_price="310", sell_price="450", reorder_level=2,
                 reorder_qty=4, preferred_supplier=bosch, fits=[hilux], bin_location="D3-02", qty="1"),
            dict(sku="BRK-1002", name="Rear brake pad set (used, good condition)", category=cat_brakes,
                 brand="Toyota Genuine", part_type="oem", condition="used", cost_price="90", sell_price="150",
                 reorder_level=0, fits=[corolla], bin_location="A1-02", qty="2"),
        ]
        for data in parts:
            fits = data.pop("fits", [])
            qty = Decimal(data.pop("qty", "0"))
            for f in ("cost_price", "sell_price"):
                data[f] = Decimal(data[f])
            bin_location = data.pop("bin_location", "")
            reorder_level = data.pop("reorder_level", 0)
            reorder_qty = data.pop("reorder_qty", 0)
            part, created = Part.objects.get_or_create(tenant=tenant, sku=data.pop("sku"), defaults=data)
            item, _ = BranchStock.objects.get_or_create(branch=branch, part=part,
                                                         defaults={"cost_price": part.cost_price})
            item.bin_location, item.reorder_level, item.reorder_qty = bin_location, reorder_level, reorder_qty
            item.save()
            if fits:
                part.fits.set(fits)
            if created and qty > 0:
                record_movement(part, qty, "opening", note="Demo seed stock", branch=branch)
        PartNumber.objects.get_or_create(part=Part.objects.get(tenant=tenant, sku="FLT-2001"), number="W712/75", kind="alt",
                                         note="Mann equivalent")

        garage, _ = Customer.objects.get_or_create(tenant=tenant, name="Chanda Motors (garage)", defaults=dict(
            phone="+260-95-1234567", customer_type="trade", credit_limit=Decimal("5000")))
        CustomerVehicle.objects.get_or_create(customer=garage, reg_number="ABC 123", defaults=dict(
            make="Toyota", model="Corolla", year=2010))
        walkin, _ = Customer.objects.get_or_create(tenant=tenant, name="Grace Mumba", defaults=dict(phone="+260-96-7654321"))
        CustomerVehicle.objects.get_or_create(customer=walkin, reg_number="BAZ 987", defaults=dict(
            make="Toyota", model="Hilux", year=2013))

        self.stdout.write(self.style.SUCCESS("Demo data ready. Sign in as owner / partsdesk123 (and change it!)."))
