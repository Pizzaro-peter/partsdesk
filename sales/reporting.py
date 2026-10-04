"""Profit figures. Revenue is ex-tax; returned quantities are excluded."""
from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.utils import timezone

from core.utils import ZERO

from .models import Sale, SaleLine


def _bounds(start, end):
    tz = timezone.get_current_timezone()
    return (timezone.make_aware(datetime.combine(start, time.min), tz),
            timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min), tz))


def line_figures(line):
    """(revenue_ex_tax, tax, cost) for the unreturned part of a line."""
    net_qty = line.quantity - line.returned_qty
    if net_qty <= 0:
        return ZERO, ZERO, ZERO
    paid = net_qty * line.unit_price * (100 - line.discount_pct) / 100
    sale = line.sale
    if sale.tax_inclusive:
        revenue = paid * 100 / (100 + sale.tax_rate)
        tax = paid - revenue
    else:
        revenue, tax = paid, paid * sale.tax_rate / 100
    return revenue, tax, net_qty * line.unit_cost


def _lines(start, end, branch):
    s, e = _bounds(start, end)
    return SaleLine.objects.filter(sale__branch=branch, sale__created_at__gte=s, sale__created_at__lt=e).select_related("sale", "part")


def daily_figures(start, end, branch):
    """Zero-filled {date: {invoices, revenue, tax, cost, profit}} for every day in the range."""
    days = {}
    d = start
    while d <= end:
        days[d] = dict(date=d, invoices=0, revenue=ZERO, tax=ZERO, cost=ZERO, profit=ZERO)
        d += timedelta(days=1)
    s, e = _bounds(start, end)
    for created in Sale.objects.filter(branch=branch, created_at__gte=s, created_at__lt=e).values_list("created_at", flat=True):
        days[timezone.localtime(created).date()]["invoices"] += 1
    for line in _lines(start, end, branch):
        rev, tax, cost = line_figures(line)
        row = days[timezone.localtime(line.sale.created_at).date()]
        row["revenue"] += rev
        row["tax"] += tax
        row["cost"] += cost
        row["profit"] += rev - cost
    return days


def part_figures(start, end, branch):
    """Per-part sales, sorted by revenue."""
    acc = defaultdict(lambda: dict(part=None, qty=ZERO, revenue=ZERO, profit=ZERO))
    for line in _lines(start, end, branch):
        if not line.part_id:
            continue
        rev, _, cost = line_figures(line)
        row = acc[line.part_id]
        row["part"] = line.part
        row["qty"] += line.quantity - line.returned_qty
        row["revenue"] += rev
        row["profit"] += rev - cost
    return sorted(acc.values(), key=lambda r: r["revenue"], reverse=True)
