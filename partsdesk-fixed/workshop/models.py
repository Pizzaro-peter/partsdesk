from core.tracked import TrackedModel
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone

from catalog.models import Part
from core.models import Branch
from core.utils import q2
from sales.models import Customer, CustomerVehicle, Sale


class JobCard(TrackedModel):
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    class Status(models.TextChoices):
        OPEN = "open", "Checked in"
        DIAGNOSING = "diagnosing", "Diagnosing"
        AWAITING_PARTS = "awaiting_parts", "Awaiting parts"
        IN_PROGRESS = "in_progress", "In progress"
        READY = "ready", "Ready for pickup"
        INVOICED = "invoiced", "Invoiced"
        CANCELLED = "cancelled", "Cancelled"

    number = models.CharField(max_length=20)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="jobs")
    vehicle = models.ForeignKey(CustomerVehicle, on_delete=models.PROTECT)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    complaint = models.TextField("Customer's complaint / work requested")
    diagnosis = models.TextField("Diagnosis / work notes", blank=True)
    odometer = models.PositiveIntegerField("Odometer (km)", null=True, blank=True)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="assigned_jobs", verbose_name="Mechanic")
    promised_date = models.DateField(null=True, blank=True)
    invoice = models.OneToOneField(Sale, null=True, blank=True, on_delete=models.SET_NULL, related_name="job")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["branch", "number"], name="job_branch_number")]

    def __str__(self):
        return self.number

    def get_absolute_url(self):
        return reverse("job_detail", args=[self.pk])

    @property
    def is_locked(self):
        return self.status in (self.Status.INVOICED, self.Status.CANCELLED)

    @property
    def parts_total(self):
        return q2(sum((p.line_total for p in self.parts.all()), Decimal("0")))

    @property
    def labour_total(self):
        return q2(sum((l.amount for l in self.labour.all()), Decimal("0")))

    @property
    def total(self):
        return self.parts_total + self.labour_total


class JobPart(TrackedModel):
    job = models.ForeignKey(JobCard, on_delete=models.CASCADE, related_name="parts")
    part = models.ForeignKey(Part, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["id"]

    @property
    def line_total(self):
        return q2(self.quantity * self.unit_price)


class JobLabour(TrackedModel):
    job = models.ForeignKey(JobCard, on_delete=models.CASCADE, related_name="labour")
    description = models.CharField(max_length=200)
    hours = models.DecimalField(max_digits=6, decimal_places=2)
    rate = models.DecimalField("Rate per hour", max_digits=10, decimal_places=2)

    class Meta:
        ordering = ["id"]

    @property
    def amount(self):
        return q2(self.hours * self.rate)
