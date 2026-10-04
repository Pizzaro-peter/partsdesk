import re

from django.db.models import Q
from django.db.models import DecimalField, OuterRef, Subquery, Value, CharField
from django.db.models.functions import Coalesce

from .models import BranchStock, Part

YEAR = re.compile(r"(19|20)\d{2}")


def with_stock(qs, branch):
    stock = BranchStock.objects.filter(branch=branch, part_id=OuterRef("pk"))
    decimal = DecimalField(max_digits=12, decimal_places=2)
    return qs.annotate(
        branch_quantity=Coalesce(Subquery(stock.values("quantity_on_hand")[:1]), Value(0), output_field=decimal),
        branch_reorder_level=Coalesce(Subquery(stock.values("reorder_level")[:1]), Value(0), output_field=decimal),
        branch_reorder_qty=Coalesce(Subquery(stock.values("reorder_qty")[:1]), Value(0), output_field=decimal),
        branch_bin=Coalesce(Subquery(stock.values("bin_location")[:1]), Value(""), output_field=CharField()),
    )


def search_parts(query, tenant, active_only=True):
    """Multi-word search. Every word must match somewhere, so counter staff can type
    'brake pad corolla 2010' and get pads that fit a 2010 Corolla."""
    qs = Part.objects.filter(tenant=tenant)
    if active_only:
        qs = qs.filter(is_active=True)
    for token in (query or "").split():
        cond = (
            Q(sku__icontains=token) | Q(barcode__iexact=token) | Q(name__icontains=token)
            | Q(brand__icontains=token) | Q(oem_number__icontains=token)
            | Q(category__name__icontains=token) | Q(description__icontains=token)
            | Q(cross_refs__number__icontains=token)
            | Q(fits__make__icontains=token) | Q(fits__model__icontains=token)
            | Q(fits__engine__icontains=token)
        )
        if YEAR.fullmatch(token):
            year = int(token)
            cond |= Q(fits__year_from__lte=year, fits__year_to__gte=year)
        qs = qs.filter(cond)
    return qs.distinct()
