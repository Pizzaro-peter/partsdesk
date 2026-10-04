import csv

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from catalog.models import BranchStock, Category, Part
from core.models import Branch, Sequence
from core.utils import decimal_input
from stock.models import StockMovement
from stock.services import StockError, record_movement

HEADERS = "sku,name,category,brand,condition,oem_number,unit,bin_location,cost_price,sell_price,trade_price,quantity,reorder_level"


def dec(value, default="0"):
    return decimal_input(value or default, label="Imported number", minimum=0)


class Command(BaseCommand):
    help = (f"Import parts atomically from CSV. Columns: {HEADERS}. Only 'name' is required. "
            "Opening quantity applies once to each branch/part without stock history; existing stock is unchanged.")

    def add_arguments(self, parser):
        parser.add_argument("csv_file")
        parser.add_argument("--tenant", required=True, help="Business slug")
        parser.add_argument("--branch", required=True, help="Branch code")

    @transaction.atomic
    def handle(self, csv_file, **opts):
        branch = Branch.objects.filter(tenant__slug=opts["tenant"], code=opts["branch"], is_active=True).first()
        if branch is None:
            raise CommandError("No active branch matches that tenant slug and branch code.")
        tenant = branch.tenant
        created = updated = openings = 0
        with open(csv_file, newline="", encoding="utf-8-sig") as fh:
            for n, row in enumerate(csv.DictReader(fh), start=2):
                if None in row:
                    raise CommandError(f"Line {n}: more values than CSV columns. Quote values containing commas.")
                row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
                if not row.get("name"):
                    raise CommandError(f"Line {n}: name is required")
                try:
                    qty = dec(row.get("quantity"))
                    reorder = dec(row.get("reorder_level"))
                    cat = Category.objects.get_or_create(tenant=tenant, name=row["category"])[0] if row.get("category") else None
                    condition = row.get("condition", "new").lower()
                    defaults = dict(
                        name=row["name"], category=cat, brand=row.get("brand", ""),
                        condition=condition if condition in Part.Condition.values else "new",
                        oem_number=row.get("oem_number", ""), unit=row.get("unit") or "each",
                        cost_price=dec(row.get("cost_price")), sell_price=dec(row.get("sell_price")),
                        trade_price=dec(row["trade_price"]) if row.get("trade_price") else None,
                    )
                    sku = row.get("sku", "").upper()
                    if sku:
                        part, was_new = Part.objects.update_or_create(tenant=tenant, sku=sku, defaults=defaults)
                    else:
                        part, was_new = Part.objects.create(tenant=tenant,
                            sku=f"{branch.code}-{Sequence.next(branch, 'part', 'P')}", **defaults), True
                    part.full_clean()
                    item, _ = BranchStock.objects.get_or_create(branch=branch, part=part,
                                                                 defaults={"cost_price": part.cost_price})
                    item = BranchStock.objects.select_for_update().get(pk=item.pk)
                    item.bin_location = row.get("bin_location", "")
                    item.reorder_level = reorder
                    item.full_clean()
                    item.save(update_fields=["bin_location", "reorder_level"])
                    # A shared SKU may already exist in another branch. Its destination
                    # branch can receive opening stock once, including a pre-created zero row.
                    has_history = StockMovement.objects.filter(branch=branch, part=part).exists()
                    if qty > 0 and item.quantity_on_hand == 0 and not has_history:
                        item.cost_price = part.cost_price
                        item.save(update_fields=["cost_price"])
                        record_movement(part, qty, "opening", note="Imported opening stock", branch=branch)
                        openings += 1
                except (ValueError, ValidationError, IntegrityError, StockError) as exc:
                    raise CommandError(f"Line {n}: {exc}") from exc
                created += was_new
                updated += not was_new
        self.stdout.write(self.style.SUCCESS(f"Imported: {created} new, {updated} updated; {openings} branch openings."))
