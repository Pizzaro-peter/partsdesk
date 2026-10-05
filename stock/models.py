from core.tracked import TrackedModel
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone

from catalog.models import Part, Supplier
from core.models import Branch


class StockMovement(TrackedModel):
    """Append-only branch ledger. BranchStock is its quantity cache."""

    class Reason(models.TextChoices):
        OPENING = "opening", "Opening stock"
        RECEIPT = "receipt", "Goods received"
        SALE = "sale", "Sale"
        RETURN_IN = "return_in", "Customer return"
        JOB_USE = "job_use", "Used on job card"
        JOB_RETURN = "job_return", "Returned from job card"
        ADJUSTMENT = "adjustment", "Stock adjustment"

    part = models.ForeignKey(Part, on_delete=models.PROTECT, related_name="movements")
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=12, decimal_places=2, help_text="Signed: negative = stock out")
    quantity_after = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.CharField(max_length=20, choices=Reason.choices)
    reference = models.CharField(max_length=40, blank=True)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    note = models.CharField(max_length=200, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class PurchaseOrder(TrackedModel):
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ORDERED = "ordered", "Ordered"
        PARTIAL = "partial", "Partly received"
        RECEIVED = "received", "Received"
        CANCELLED = "cancelled", "Cancelled"

    number = models.CharField(max_length=20)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="purchase_orders")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    order_date = models.DateField(default=timezone.localdate)
    expected_date = models.DateField(null=True, blank=True)
    supplier_ref = models.CharField("Supplier invoice / delivery note", max_length=60, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["branch", "number"], name="po_branch_number")]

    def __str__(self):
        return self.number

    def get_absolute_url(self):
        return reverse("po_detail", args=[self.pk])

    @property
    def total(self):
        return sum((l.qty_ordered * l.unit_cost for l in self.lines.all()), Decimal("0"))

    @property
    def can_receive(self):
        return self.status in (self.Status.ORDERED, self.Status.PARTIAL)


class PurchaseOrderLine(TrackedModel):
    po = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="lines")
    part = models.ForeignKey(Part, on_delete=models.PROTECT)
    qty_ordered = models.DecimalField(max_digits=12, decimal_places=2)
    qty_received = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        unique_together = [("po", "part")]
        ordering = ["id"]

    @property
    def outstanding(self):
        return max(self.qty_ordered - self.qty_received, Decimal("0"))

    @property
    def line_total(self):
        return self.qty_ordered * self.unit_cost
