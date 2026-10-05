from django.db import migrations


def backfill(apps, schema_editor):
    Tenant = apps.get_model("core", "Tenant")
    Branch = apps.get_model("core", "Branch")
    User = apps.get_model("core", "User")
    Settings = apps.get_model("core", "ShopSettings")
    Sequence = apps.get_model("core", "Sequence")
    Audit = apps.get_model("core", "AuditLog")
    Category = apps.get_model("catalog", "Category")
    Supplier = apps.get_model("catalog", "Supplier")
    Vehicle = apps.get_model("catalog", "Vehicle")
    Part = apps.get_model("catalog", "Part")
    BranchStock = apps.get_model("catalog", "BranchStock")
    Customer = apps.get_model("sales", "Customer")
    Sale = apps.get_model("sales", "Sale")
    PO = apps.get_model("stock", "PurchaseOrder")
    Movement = apps.get_model("stock", "StockMovement")
    Job = apps.get_model("workshop", "JobCard")

    if not any(m.objects.exists() for m in (User, Settings, Part, Customer, Sale, PO, Movement, Job)):
        return  # New installs create businesses through create_tenant.

    old_settings = Settings.objects.order_by("pk").first()
    name = old_settings.shop_name if old_settings else "Existing business"
    tenant = Tenant.objects.create(name=name, slug="original-business")
    branch = Branch.objects.create(tenant=tenant, name="Main branch", code="MAIN")
    User.objects.update(tenant=tenant, is_staff=False, is_superuser=False)
    for user in User.objects.all():
        user.branches.add(branch)
    Settings.objects.update(tenant=tenant)
    Sequence.objects.update(branch=branch)
    Audit.objects.update(branch=branch)
    for model in (Category, Supplier, Vehicle, Part, Customer):
        model.objects.update(tenant=tenant)
    for model in (Sale, PO, Movement, Job):
        model.objects.update(branch=branch)
    for part in Part.objects.all().iterator():
        BranchStock.objects.create(branch=branch, part=part,
            quantity_on_hand=part.quantity_on_hand, bin_location=part.bin_location,
            reorder_level=part.reorder_level, reorder_qty=part.reorder_qty)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_branch_tenant_alter_sequence_name_auditlog_branch_and_more"),
        ("catalog", "0002_branchstock_alter_vehicle_unique_together_and_more"),
        ("sales", "0002_customer_tenant_sale_branch_alter_sale_number_and_more"),
        ("stock", "0002_purchaseorder_branch_stockmovement_branch_and_more"),
        ("workshop", "0002_jobcard_branch_alter_jobcard_number_and_more"),
    ]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
