from core.tracked import TrackedModel
from decimal import Decimal

from django.contrib.auth.models import AbstractUser
from django.db import models, transaction


class Tenant(TrackedModel):
    name = models.CharField(max_length=120)
    slug = models.SlugField(unique=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Branch(TrackedModel):
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="branches")
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=16)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "code"], name="branch_tenant_code")]
        ordering = ["name"]

    def __str__(self):
        return self.name


class User(AbstractUser):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        MANAGER = "manager", "Manager"
        COUNTER = "counter", "Counter sales"
        MECHANIC = "mechanic", "Mechanic"
        STOREKEEPER = "storekeeper", "Storekeeper"

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.COUNTER)
    tenant = models.ForeignKey(Tenant, null=True, blank=True, on_delete=models.PROTECT, related_name="users")
    branches = models.ManyToManyField(Branch, blank=True, related_name="staff")

    @transaction.atomic
    def save(self, *args, **kwargs):
        if self.is_superuser:  # e.g. created with `createsuperuser`
            self.role = self.Role.OWNER
        # A shop owner must never gain platform-wide Django admin privileges.
        self.is_staff = self.is_superuser
        super().save(*args, **kwargs)

    @property
    def display_name(self):
        return self.get_full_name() or self.username

    @property
    def can_see_costs(self):
        return self.role in (self.Role.OWNER, self.Role.MANAGER, self.Role.STOREKEEPER)


class ShopSettings(TrackedModel):
    """Tenant-wide business configuration."""
    tenant = models.OneToOneField(Tenant, on_delete=models.CASCADE, related_name="settings")

    shop_name = models.CharField(max_length=120, default="My Spare Parts Shop")
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=60, blank=True)
    email = models.EmailField(blank=True)
    tax_id = models.CharField("Tax / TPIN number", max_length=60, blank=True)
    currency_symbol = models.CharField(max_length=8, default="$")
    tax_name = models.CharField(max_length=20, default="VAT")
    tax_rate = models.DecimalField("Tax rate %", max_digits=5, decimal_places=2, default=Decimal("0"))
    prices_include_tax = models.BooleanField(default=True, help_text="Untick if prices are entered before tax.")
    counter_max_discount = models.DecimalField(
        "Max discount % for counter staff", max_digits=5, decimal_places=2, default=Decimal("10"),
        help_text="Managers and the owner can exceed this.")
    default_labour_rate = models.DecimalField("Default labour rate per hour", max_digits=10, decimal_places=2, default=Decimal("0"))
    invoice_footer = models.CharField(max_length=200, blank=True, default="Thank you for your business.")

    class Meta:
        verbose_name_plural = "shop settings"

    def __str__(self):
        return self.shop_name

    @classmethod
    def get(cls, tenant):
        obj, _ = cls.objects.get_or_create(tenant=tenant)
        return obj


class Sequence(models.Model):
    """Gap-free counters for INV-000001 / PO-000001 / JOB-000001 style numbers."""

    branch = models.ForeignKey(Branch, on_delete=models.CASCADE)
    name = models.CharField(max_length=30)
    last = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["branch", "name"], name="sequence_branch_name")]

    @classmethod
    def next(cls, branch, name, prefix, width=6):
        with transaction.atomic():
            seq, _ = cls.objects.select_for_update().get_or_create(branch=branch, name=name)
            seq.last += 1
            seq.save(update_fields=["last"])
            return f"{prefix}-{seq.last:0{width}d}"


class AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError("Audit records cannot be edited.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValueError("Audit records cannot be edited.")

    def delete(self):
        raise ValueError("Audit records cannot be deleted.")


class AppendOnlyModel(models.Model):
    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("Audit records cannot be edited.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Audit records cannot be deleted.")


class AuditLog(AppendOnlyModel):
    tenant = models.ForeignKey(Tenant, null=True, on_delete=models.PROTECT)
    branch = models.ForeignKey(Branch, null=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.ForeignKey(User, null=True, on_delete=models.PROTECT)
    action = models.CharField(max_length=40)
    target = models.CharField(max_length=200, blank=True)
    detail = models.TextField(blank=True)
    actor_name = models.CharField(max_length=150, blank=True)
    kind = models.CharField(max_length=20, default="business", db_index=True)
    outcome = models.CharField(max_length=12, default="success", db_index=True)
    changes = models.JSONField(default=dict, blank=True)
    request_id = models.CharField(max_length=36, blank=True, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    route = models.CharField(max_length=100, blank=True)
    method = models.CharField(max_length=10, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]


def audit(user, action, target="", detail="", *, branch=None, **kwargs):
    from .audit import write_event
    return write_event(user, action, target, detail, branch=branch, **kwargs)


class RequestLog(AppendOnlyModel):
    tenant = models.ForeignKey(Tenant, null=True, on_delete=models.PROTECT)
    branch = models.ForeignKey(Branch, null=True, on_delete=models.PROTECT)
    user = models.ForeignKey(User, null=True, on_delete=models.PROTECT)
    actor_name = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    request_id = models.CharField(max_length=36, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    method = models.CharField(max_length=10)
    route = models.CharField(max_length=100, blank=True)
    target = models.CharField(max_length=200, blank=True)
    status_code = models.PositiveSmallIntegerField()
    outcome = models.CharField(max_length=12, db_index=True)
    duration_ms = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at", "-id"]
