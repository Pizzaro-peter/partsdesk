from core.tracked import TrackedModel
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import F, Sum
from django.urls import reverse
from django.utils import timezone

from catalog.models import Part
from core.models import Branch, Tenant
from core.utils import TWO, q2


class Customer(TrackedModel):
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT)
    class Type(models.TextChoices):
        RETAIL = "retail", "Retail"
        TRADE = "trade", "Trade (garage / mechanic)"

    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=60, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    customer_type = models.CharField(max_length=10, choices=Type.choices, default=Type.RETAIL)
    credit_limit = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"),
                                       help_text="0 = no credit. Above 0 lets them buy on account up to this amount.")
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("customer_detail", args=[self.pk])

    @property
    def is_trade(self):
        return self.customer_type == self.Type.TRADE

    @property
    def balance(self):
        """What the customer owes us (negative = we owe them a credit)."""
        v = self.sales.aggregate(v=Sum(F("total") - F("returned_total") - F("amount_paid")))["v"]
        return v or Decimal("0")


class CustomerVehicle(TrackedModel):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="vehicles")
    reg_number = models.CharField("Registration / plate", max_length=20)
    make = models.CharField(max_length=60)
    model = models.CharField(max_length=60)
    year = models.PositiveSmallIntegerField(null=True, blank=True)
    engine = models.CharField(max_length=60, blank=True)
    vin = models.CharField("VIN / chassis no.", max_length=40, blank=True)
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["reg_number"]

    def __str__(self):
        return f"{self.reg_number} – {' '.join(filter(None, [self.make, self.model, str(self.year or '')]))}"


class Sale(TrackedModel):
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    class Status(models.TextChoices):
        COMPLETED = "completed", "Completed"
        PART_RETURNED = "part_returned", "Partly returned"
        RETURNED = "returned", "Returned"

    class Method(models.TextChoices):
        CASH = "cash", "Cash"
        MOBILE = "mobile", "Mobile money"
        CARD = "card", "Card"
        BANK = "bank", "Bank transfer"
        CREDIT = "credit", "On account"

    number = models.CharField(max_length=20)
    customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.PROTECT, related_name="sales")
    vehicle = models.ForeignKey(CustomerVehicle, null=True, blank=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.COMPLETED)
    payment_method = models.CharField(max_length=10, choices=Method.choices, default=Method.CASH)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))
    tax_inclusive = models.BooleanField(default=True)
    gross_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    tax_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    returned_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    notes = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["branch", "number"], name="sale_branch_number")]

    def __str__(self):
        return self.number

    def get_absolute_url(self):
        return reverse("sale_detail", args=[self.pk])

    @property
    def balance_due(self):
        return self.total - self.returned_total - self.amount_paid

    @property
    def payment_status(self):
        due = self.balance_due
        if due > 0:
            return "Part paid" if self.amount_paid > 0 else "Unpaid"
        return "Credit owed to customer" if due < 0 else "Paid"

    @property
    def net_total(self):
        return self.total - self.returned_total

    @property
    def subtotal(self):
        return self.net_total + self.discount_total

    def recalculate(self):
        gross = discount = net = Decimal("0")
        for line in self.lines.all():
            gross += line.quantity * line.unit_price
            net += line.line_total
        gross, net = q2(gross), q2(net)
        rate = self.tax_rate
        if self.tax_inclusive:
            tax, total = q2(net * rate / (100 + rate)), net
        else:
            tax = q2(net * rate / 100)
            total = net + tax
        self.gross_total, self.discount_total = gross, gross - net
        self.tax_total, self.total = tax, total
        self.save(update_fields=["gross_total", "discount_total", "tax_total", "total"])


class CheckoutRequest(TrackedModel):
    """Persistent identity for one POS submission, shared by its retries."""
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    key = models.UUIDField()
    payload_hash = models.CharField(max_length=64)
    sale = models.OneToOneField(Sale, null=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["branch", "user", "key"], name="checkout_branch_user_key")]


class SaleLine(TrackedModel):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="lines")
    part = models.ForeignKey(Part, null=True, blank=True, on_delete=models.PROTECT)  # null = labour / misc
    description = models.CharField(max_length=200)
    is_labour = models.BooleanField(default=False)
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    discount_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))  # snapshot for profit
    returned_qty = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))

    class Meta:
        ordering = ["id"]

    @property
    def line_total(self):
        return q2(self.quantity * self.unit_price * (Decimal(100) - self.discount_pct) / Decimal(100))

    @property
    def total_incl_tax(self):
        total = self.line_total
        if not self.sale.tax_inclusive and self.sale.tax_rate:
            total = q2(total * (100 + self.sale.tax_rate) / 100)
        return total

    @property
    def returnable(self):
        return self.quantity - self.returned_qty


class Payment(TrackedModel):
    """Signed: positive = money in, negative = refund."""

    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=10, choices=Sale.Method.choices)
    reference = models.CharField(max_length=60, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at", "id"]


class SaleReturn(TrackedModel):
    number = models.CharField(max_length=20)
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="returns")
    reason = models.CharField(max_length=200, blank=True)
    restocked = models.BooleanField(default=True)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["sale", "number"], name="return_sale_number")]


class SaleReturnLine(TrackedModel):
    sale_return = models.ForeignKey(SaleReturn, on_delete=models.CASCADE, related_name="lines")
    sale_line = models.ForeignKey(SaleLine, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
