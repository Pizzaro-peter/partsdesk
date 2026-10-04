"""Branch-specific stock transactions and the corresponding movement ledger."""
from core.audit import audit_actor
from decimal import Decimal

from django.db import transaction

from catalog.models import Part, BranchStock
from core.models import Sequence, audit
from core.tenancy import check_staff_branch, same_tenant
from core.utils import TWO, decimal_input, qty_str

from .models import PurchaseOrder, PurchaseOrderLine, StockMovement

R = StockMovement.Reason


class StockError(Exception):
    """A stock operation that can't be completed; the message is safe to show staff."""


def stock_number(value, **kwargs):
    try:
        return decimal_input(value, **kwargs)
    except ValueError as exc:
        raise StockError(str(exc)) from exc


@audit_actor
@transaction.atomic
def record_movement(part, qty, reason, user=None, reference="", unit_cost=None, note="", *, branch):
    same_tenant(branch.tenant, part)
    if user is not None:
        check_staff_branch(user, branch)
    qty = stock_number(qty, label="Stock quantity")
    if unit_cost is not None:
        unit_cost = stock_number(unit_cost, label="Unit cost", minimum=0)
    locked, _ = BranchStock.objects.get_or_create(branch=branch, part=part, defaults={"cost_price": part.cost_price})
    locked = BranchStock.objects.select_for_update().get(pk=locked.pk)
    new_qty = stock_number(locked.quantity_on_hand + qty, label="Stock balance")
    if new_qty < 0:
        raise StockError(
            f"Not enough stock for {part.sku} – {part.name} (on hand: {qty_str(locked.quantity_on_hand)}).")
    if reason == R.RECEIPT and unit_cost is not None and qty > 0:  # moving-average cost
        base = max(locked.quantity_on_hand, Decimal("0"))
        locked.cost_price = ((base * locked.cost_price + qty * unit_cost) / (base + qty)).quantize(TWO)
    locked.quantity_on_hand = new_qty
    locked.save(update_fields=["quantity_on_hand", "cost_price"])
    return StockMovement.objects.create(
        branch=branch, part=part, quantity=qty, quantity_after=new_qty, reason=reason, reference=reference,
        unit_cost=unit_cost, note=note, user=user)


@audit_actor
@transaction.atomic
def adjust_to_count(part, counted, user, note="", *, branch):
    """Set stock to a physically counted quantity; the difference goes in the ledger."""
    same_tenant(branch.tenant, part)
    check_staff_branch(user, branch)
    locked, _ = BranchStock.objects.get_or_create(branch=branch, part=part, defaults={"cost_price": part.cost_price})
    locked = BranchStock.objects.select_for_update().get(pk=locked.pk)
    counted = stock_number(counted, label="Counted quantity", minimum=0)
    delta = counted - locked.quantity_on_hand
    if delta == 0:
        return None
    movement = record_movement(part, delta, R.ADJUSTMENT, user, note=note, branch=branch)
    audit(user, "stock.adjust", part.sku, f"{delta:+} — {note}", branch=branch)
    return movement


@audit_actor
def add_po_line(po, part, qty, unit_cost):
    same_tenant(po.branch.tenant, part)
    qty = stock_number(qty, label="Ordered quantity", minimum=Decimal("0.01"))
    unit_cost = stock_number(unit_cost, label="Unit cost", minimum=0)
    if po.status != PurchaseOrder.Status.DRAFT:
        raise StockError("Lines can only be changed while the order is a draft.")
    line, created = PurchaseOrderLine.objects.get_or_create(
        po=po, part=part, defaults={"qty_ordered": qty, "unit_cost": unit_cost})
    if not created:
        line.qty_ordered, line.unit_cost = qty, unit_cost
        line.save(update_fields=["qty_ordered", "unit_cost"])
    return line


@audit_actor
@transaction.atomic
def receive_po(po, quantities, user, supplier_ref=""):
    """quantities: {line_id: Decimal}. Supports partial deliveries."""
    po = PurchaseOrder.objects.select_for_update().get(pk=po.pk)
    check_staff_branch(user, po.branch)
    if not po.can_receive:
        raise StockError("Only ordered purchase orders can be received.")
    received = False
    for line in po.lines.select_related("part"):
        qty = stock_number(quantities.get(line.id, Decimal("0")), label="Received quantity", minimum=0)
        if qty <= 0:
            continue
        record_movement(line.part, qty, R.RECEIPT, user, reference=po.number, unit_cost=line.unit_cost, branch=po.branch)
        line.qty_received += qty
        line.save(update_fields=["qty_received"])
        received = True
    if not received:
        raise StockError("Enter the quantity received for at least one line.")
    done = all(l.qty_received >= l.qty_ordered for l in po.lines.all())
    po.status = PurchaseOrder.Status.RECEIVED if done else PurchaseOrder.Status.PARTIAL
    if supplier_ref:
        po.supplier_ref = supplier_ref
    po.save(update_fields=["status", "supplier_ref"])
    audit(user, "po.receive", po.number, f"status → {po.status}", branch=po.branch)
    return po


@audit_actor
@transaction.atomic
def draft_pos_from_low_stock(user, branch):
    check_staff_branch(user, branch)
    """Group low-stock parts by preferred supplier into draft POs, skipping parts already on order."""
    from django.db.models import F

    open_status = [PurchaseOrder.Status.ORDERED, PurchaseOrder.Status.PARTIAL]
    on_order = set(PurchaseOrderLine.objects.filter(po__branch=branch, po__status__in=open_status).values_list("part_id", flat=True))
    stocks = BranchStock.objects.filter(
        branch=branch, part__is_active=True, reorder_level__gt=0,
        quantity_on_hand__lte=F("reorder_level"), part__preferred_supplier__isnull=False
    ).exclude(part_id__in=on_order).select_related("part__preferred_supplier")
    pos = {}
    for item in stocks:
        part = item.part
        supplier = part.preferred_supplier
        if supplier.pk not in pos:
            po = PurchaseOrder.objects.filter(branch=branch, supplier=supplier, status=PurchaseOrder.Status.DRAFT).first()
            pos[supplier.pk] = po or PurchaseOrder.objects.create(
                branch=branch, number=Sequence.next(branch, "po", "PO"), supplier=supplier, created_by=user)
        qty = item.reorder_qty or max(item.reorder_level * 2 - item.quantity_on_hand, Decimal("1"))
        add_po_line(pos[supplier.pk], part, qty, item.cost_price)
    return list(pos.values())
