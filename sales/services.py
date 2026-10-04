"""Sales business rules. Views stay thin; everything money- or stock-related happens here, atomically."""
from core.audit import audit_actor
from decimal import Decimal

from django.db import transaction

from core.models import Sequence, ShopSettings, User, audit
from core.tenancy import check_staff_branch, same_tenant
from core.utils import ZERO, decimal_input, q2
from catalog.models import BranchStock
from stock.services import StockError, record_movement

from .models import CheckoutRequest, Payment, Sale, SaleLine, SaleReturn, SaleReturnLine

M = Sale.Method
R = "sale"


class SaleError(Exception):
    """Business-rule failure with a message that is safe to show staff."""


class CheckoutConflict(SaleError):
    pass


def sale_number(value, **kwargs):
    try:
        return decimal_input(value, **kwargs)
    except ValueError as exc:
        raise SaleError(str(exc)) from exc


@audit_actor
@transaction.atomic
def checkout_once(*, user, branch, key, payload_hash, create):
    """Claim a request in the same transaction as its invoice and stock writes."""
    check_staff_branch(user, branch)
    receipt, _ = CheckoutRequest.objects.get_or_create(
        branch=branch, user=user, key=key, defaults={"payload_hash": payload_hash})
    receipt = CheckoutRequest.objects.select_for_update().get(pk=receipt.pk)
    if receipt.payload_hash != payload_hash:
        raise CheckoutConflict("This checkout request was already used for a different ticket. Open a new sale.")
    if receipt.sale_id:
        return receipt.sale
    sale = create()
    receipt.sale = sale
    receipt.save(update_fields=["sale"])
    return sale


@audit_actor
@transaction.atomic
def create_sale(*, user, branch, lines, customer=None, vehicle=None, payment_method=M.CASH, amount_paid=None, notes=""):
    """
    lines: list of dicts with part (or None), description, quantity, unit_price,
           discount_pct=0, deduct=True (False when stock was already issued, e.g. a job card),
           is_labour=False.
    """
    if not lines:
        raise SaleError("Add at least one item.")
    if payment_method not in M.values:
        raise SaleError("Choose a valid payment method.")
    check_staff_branch(user, branch)
    same_tenant(branch.tenant, customer)
    if vehicle and (not customer or vehicle.customer_id != customer.pk):
        raise SaleError("Vehicle must belong to the selected customer.")
    cfg = ShopSettings.get(branch.tenant)
    sale_number(cfg.tax_rate, label="Tax rate", minimum=0, maximum=100, max_digits=5)
    sale_number(cfg.counter_max_discount, label="Counter discount", minimum=0, maximum=100, max_digits=5)
    if amount_paid is not None:
        amount_paid = sale_number(amount_paid, label="Amount paid", minimum=0)
    for l in lines:
        same_tenant(branch.tenant, l.get("part"))
        disc = sale_number(l.get("discount_pct", 0), label="Discount", minimum=0, maximum=100, max_digits=5)
        if not (0 <= disc <= 100):
            raise SaleError("Discount must be between 0 and 100%.")
        if user.role == User.Role.COUNTER and disc > cfg.counter_max_discount:
            raise SaleError(f"Counter staff can give at most {cfg.counter_max_discount}% discount. Ask a manager.")
        l["quantity"] = sale_number(l["quantity"], label="Quantity", minimum=Decimal("0.01"))
        l["unit_price"] = sale_number(l["unit_price"], label="Price", minimum=0)
        l["discount_pct"] = disc
        if l["quantity"] <= 0:
            raise SaleError("Quantities must be more than zero.")
        if Decimal(l["unit_price"]) < 0:
            raise SaleError("Prices cannot be negative.")
    gross = q2(sum((l["quantity"] * l["unit_price"] for l in lines), ZERO))
    sale_number(gross, label="Ticket subtotal", minimum=0)
    net = sum((q2(l["quantity"] * l["unit_price"] * (100 - l["discount_pct"]) / 100) for l in lines), ZERO)
    total = net if cfg.prices_include_tax else net + q2(net * cfg.tax_rate / 100)
    sale_number(total, label="Ticket total", minimum=0)

    sale = Sale.objects.create(
        branch=branch, number=Sequence.next(branch, "sale", "INV"), customer=customer, vehicle=vehicle, created_by=user,
        payment_method=payment_method, tax_rate=cfg.tax_rate, tax_inclusive=cfg.prices_include_tax, notes=notes[:200])
    for l in lines:
        part, qty = l.get("part"), Decimal(l["quantity"])
        if part and l.get("deduct", True):
            record_movement(part, -qty, R, user, reference=sale.number, branch=branch)
        SaleLine.objects.create(
            sale=sale, part=part, description=l.get("description") or (part.name if part else "Item"),
            is_labour=l.get("is_labour", False), quantity=qty, unit_price=Decimal(l["unit_price"]),
            discount_pct=Decimal(l.get("discount_pct", 0)),
            unit_cost=BranchStock.objects.get(branch=branch, part=part).cost_price if part else ZERO)
    sale.recalculate()

    if payment_method == M.CREDIT:
        paid = min(Decimal(amount_paid or 0), sale.total)
    else:
        paid = sale.total if amount_paid is None else min(Decimal(amount_paid), sale.total)
    if paid < 0:
        raise SaleError("Amount paid cannot be negative.")
    on_account = sale.total - paid
    if on_account > 0:
        if not customer:
            raise SaleError("Choose a customer to put the unpaid amount on their account.")
        if customer.credit_limit <= 0:
            raise SaleError(f"{customer.name} has no credit account. Take full payment or set a credit limit.")
        projected = customer.balance - sale.total + on_account  # balance already counts this sale in full
        if projected > customer.credit_limit:
            raise SaleError(f"This would take {customer.name} over their credit limit of {customer.credit_limit}.")
    if paid > 0:
        method = M.CASH if payment_method == M.CREDIT else payment_method
        Payment.objects.create(sale=sale, amount=paid, method=method, created_by=user)
    sale.amount_paid = paid
    sale.save(update_fields=["amount_paid"])
    return sale


@audit_actor
@transaction.atomic
def add_payment(*, sale, amount, method, user, reference=""):
    check_staff_branch(user, sale.branch)
    sale = Sale.objects.select_for_update().get(pk=sale.pk)
    if user.role not in ("owner", "manager", "counter") or (user.role == "counter" and sale.created_by_id != user.pk):
        raise SaleError("You are not authorized to pay this invoice.")
    amount = sale_number(amount, label="Payment", minimum=Decimal("0.01"))
    if method not in M.values or method == M.CREDIT:
        raise SaleError("Choose how the customer paid.")
    if amount <= 0:
        raise SaleError("Enter an amount above zero.")
    if amount > sale.balance_due:
        raise SaleError("That is more than the balance due on this invoice.")
    Payment.objects.create(sale=sale, amount=amount, method=method, reference=reference, created_by=user)
    sale.amount_paid += amount
    sale.save(update_fields=["amount_paid"])
    return sale


@audit_actor
@transaction.atomic
def process_return(*, sale, user, items, reason="", restock=True, refund_method=M.CASH):
    check_staff_branch(user, sale.branch)
    """items: {sale_line_id: quantity}. Credits the invoice, optionally restocks, and refunds any overpayment."""
    if refund_method != "account" and refund_method not in M.values:
        raise SaleError("Choose how to refund the customer.")
    sale = Sale.objects.select_for_update().get(pk=sale.pk)
    lines = {l.id: l for l in sale.lines.select_related("part")}
    ret = SaleReturn.objects.create(
        number=Sequence.next(sale.branch, "return", "RET"), sale=sale, reason=reason[:200], restocked=restock, created_by=user)
    total = ZERO
    for line_id, qty in items.items():
        qty = sale_number(qty, label="Return quantity", minimum=0)
        if qty <= 0:
            continue
        line = lines.get(line_id)
        if line is None:
            raise SaleError("That item is not on this invoice.")
        if qty > line.returnable:
            raise SaleError(f"Only {line.returnable} of '{line.description}' can still be returned.")
        amount = q2(line.total_incl_tax * qty / line.quantity)
        SaleReturnLine.objects.create(sale_return=ret, sale_line=line, quantity=qty, amount=amount)
        line.returned_qty += qty
        line.save(update_fields=["returned_qty"])
        if restock and line.part:
            record_movement(line.part, qty, "return_in", user, reference=ret.number, branch=sale.branch)
        total += amount
    if total <= 0:
        raise SaleError("Enter a quantity for at least one item.")
    ret.total = total
    ret.save(update_fields=["total"])

    sale.returned_total += total
    fully = all(l.returned_qty >= l.quantity for l in lines.values())
    sale.status = Sale.Status.RETURNED if fully else Sale.Status.PART_RETURNED
    overpaid = sale.amount_paid - (sale.total - sale.returned_total)
    if overpaid > 0 and not (refund_method == "account" and sale.customer_id):
        method = M.CASH if refund_method == "account" else refund_method
        Payment.objects.create(sale=sale, amount=-overpaid, method=method, created_by=user, reference=ret.number)
        sale.amount_paid -= overpaid
    sale.save()
    audit(user, "sale.return", sale.number, f"{ret.number}: {total} ({'restocked' if restock else 'not restocked'})", branch=sale.branch)
    return ret
