from core.tracked import TrackedModel
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.conf import settings
from django.utils import timezone
from core.models import Tenant, Branch


class Category(TrackedModel):
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT)
    name = models.CharField(max_length=80)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "categories"
        constraints = [models.UniqueConstraint(fields=["tenant", "name"], name="category_tenant_name")]

    def __str__(self):
        return self.name


class Supplier(TrackedModel):
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT)
    name = models.CharField(max_length=120)
    contact_person = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=60, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    payment_terms = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["tenant", "name"], name="supplier_tenant_name")]

    def __str__(self):
        return self.name


class Vehicle(TrackedModel):
    """A vehicle *application* a part can fit, e.g. Toyota Corolla 2007-2012 1.8L."""

    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT)
    make = models.CharField(max_length=60)
    model = models.CharField(max_length=60)
    year_from = models.PositiveSmallIntegerField()
    year_to = models.PositiveSmallIntegerField()
    engine = models.CharField(max_length=60, blank=True)

    class Meta:
        ordering = ["make", "model", "year_from"]
        constraints = [models.UniqueConstraint(fields=["tenant", "make", "model", "year_from", "year_to", "engine"], name="vehicle_tenant_identity")]

    def clean(self):
        if self.year_from and self.year_to and self.year_to < self.year_from:
            raise ValidationError("'Year to' cannot be before 'year from'.")

    def __str__(self):
        years = str(self.year_from) if self.year_from == self.year_to else f"{self.year_from}-{self.year_to}"
        return " ".join(filter(None, [self.make, self.model, years, self.engine]))


class Part(TrackedModel):
    class Condition(models.TextChoices):
        NEW = "new", "New"
        USED = "used", "Used"
        REFURBISHED = "refurbished", "Refurbished"

    class PartType(models.TextChoices):
        OEM = "oem", "Genuine (OEM)"
        AFTERMARKET = "aftermarket", "Aftermarket"

    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT)
    sku = models.CharField("Part code (SKU)", max_length=40)
    barcode = models.CharField(max_length=64, blank=True, null=True)
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL)
    brand = models.CharField(max_length=60, blank=True)
    part_type = models.CharField(max_length=20, choices=PartType.choices, default=PartType.AFTERMARKET)
    condition = models.CharField(max_length=20, choices=Condition.choices, default=Condition.NEW)
    oem_number = models.CharField("OEM part number", max_length=60, blank=True, db_index=True)
    unit = models.CharField(max_length=20, default="each", help_text="each, set, pair, litre…")

    cost_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    sell_price = models.DecimalField("Retail price", max_digits=12, decimal_places=2, default=Decimal("0"))
    trade_price = models.DecimalField("Trade price", max_digits=12, decimal_places=2, null=True, blank=True,
                                      help_text="Used for trade customers (garages, mechanics). Optional.")

    preferred_supplier = models.ForeignKey(Supplier, null=True, blank=True, on_delete=models.SET_NULL)

    fits = models.ManyToManyField(Vehicle, blank=True, related_name="parts")
    is_universal = models.BooleanField(default=False, help_text="Fits any vehicle (oil, bulbs, wipers…)")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "sku"], name="part_tenant_sku"),
            models.UniqueConstraint(fields=["tenant", "barcode"], name="part_tenant_barcode"),
        ]

    def __str__(self):
        return f"{self.sku} – {self.name}"

    def get_absolute_url(self):
        return reverse("part_detail", args=[self.pk])

    @property
    def margin_pct(self):
        if not self.sell_price:
            return None
        return (self.sell_price - self.cost_price) / self.sell_price * 100

    def price_for(self, is_trade=False):
        if is_trade and self.trade_price:
            return self.trade_price
        return self.sell_price


class PartNumber(TrackedModel):
    """Cross-references: OEM numbers, interchangeable brands, and superseded numbers."""

    class Kind(models.TextChoices):
        OEM = "oem", "OEM / genuine number"
        ALT = "alt", "Interchange (other brand)"
        OLD = "old", "Superseded (old number)"

    part = models.ForeignKey(Part, on_delete=models.CASCADE, related_name="cross_refs")
    number = models.CharField(max_length=60, db_index=True)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.ALT)
    note = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["kind", "number"]

    def __str__(self):
        return f"{self.number} ({self.get_kind_display()})"


class BranchStock(TrackedModel):
    """Branch-specific stock cache and replenishment settings for a shared catalog item."""
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="stock_items")
    part = models.ForeignKey(Part, on_delete=models.PROTECT, related_name="branch_stocks")
    quantity_on_hand = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    cost_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    bin_location = models.CharField(max_length=40, blank=True)
    reorder_level = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    reorder_qty = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))

    class Meta:
        constraints = [models.UniqueConstraint(fields=["branch", "part"], name="stock_branch_part")]

    @property
    def is_out(self):
        return self.quantity_on_hand <= 0

    @property
    def is_low(self):
        return self.reorder_level > 0 and self.quantity_on_hand <= self.reorder_level


class CatalogueImport(TrackedModel):
    class Status(models.TextChoices):
        READY = 'ready', 'Ready to import'
        IMPORTED = 'imported', 'Imported'
        CANCELLED = 'cancelled', 'Cancelled'

    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    file_name = models.CharField(max_length=255)
    mode = models.CharField(max_length=10, choices=[('create', 'Add new parts only'), ('update', 'Add and update matching SKUs')])
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.READY)
    rows = models.JSONField(default=list)
    result = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-pk']
