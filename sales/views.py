import json
import hashlib
from uuid import UUID, uuid4
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import F, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, ListView, UpdateView

from catalog.models import Part
from core.models import ShopSettings
from core.form_mixins import TenantFormMixin
from core.permissions import FRONT, MGMT, SALES, RoleRequiredMixin, role_required
from core.utils import optional_id, to_decimal
from stock.services import StockError

from .forms import CustomerForm, PaymentForm, VehicleForm
from .models import Customer, CustomerVehicle, Sale
from .services import CheckoutConflict, SaleError, add_payment, checkout_once, create_sale, process_return


@role_required(SALES)
def pos(request):
    cfg = ShopSettings.get(request.tenant)
    counter = request.user.role == "counter"
    return render(request, "sales/pos.html", {
        "page_title": "Point of sale", "cfg": cfg,
        "max_discount": cfg.counter_max_discount if counter else "",
        "can_override": "1" if request.user.role in MGMT else "0",
        "methods": Sale.Method.choices,
        "request_id": str(uuid4()),
    })


@role_required(SALES)
@require_POST
def pos_checkout(request):
    def fail(msg, status=400):
        return JsonResponse({"ok": False, "error": msg}, status=status)

    try:
        data = json.loads(request.body)
        if not isinstance(data, dict):
            return fail("Checkout must be a JSON object.")
        rows = data.get("lines")
        if not isinstance(rows, list) or not rows or len(rows) > 500 or any(not isinstance(row, dict) for row in rows):
            return fail("Add valid item lines to the ticket (maximum 500).")
        if not isinstance(data.get("notes", ""), str):
            return fail("Notes must be text.")
        key = UUID(str(data.get("request_id", "")))
        payload_hash = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        sale = checkout_once(user=request.user, branch=request.branch, key=key, payload_hash=payload_hash,
                             create=lambda: _checkout_sale(request, data))
    except CheckoutConflict as exc:
        return fail(str(exc), 409)
    except (ValueError, SaleError, StockError) as exc:
        return fail(str(exc))
    return JsonResponse({"ok": True, "url": reverse("sale_detail", args=[sale.pk]) + "?print=1"})


def _checkout_sale(request, data):
    """Resolve the ticket only once; retries return the original persisted invoice."""
    try:
        customer = None
        if data.get("customer_id"):
            customer = Customer.objects.filter(pk=optional_id(data["customer_id"]), tenant=request.tenant, is_active=True).first()
            if customer is None:
                raise SaleError("That customer no longer exists.")
        is_trade = bool(customer and customer.is_trade)
        lines = []
        for row in data.get("lines", []):
            part = Part.objects.filter(pk=optional_id(row.get("part_id")), tenant=request.tenant, is_active=True).first()
            if part is None:
                raise SaleError("One of the parts is no longer available.")
            price = part.price_for(is_trade)  # server decides the price...
            if request.user.role in MGMT and row.get("unit_price") not in (None, ""):
                price = to_decimal(row["unit_price"])  # ...unless a manager overrides it
            lines.append(dict(part=part, quantity=to_decimal(row.get("quantity")), unit_price=price,
                              discount_pct=to_decimal(row.get("discount_pct"), default=Decimal("0"))))
        paid = data.get("amount_paid")
        return create_sale(
            user=request.user, branch=request.branch, lines=lines, customer=customer,
            payment_method=data.get("payment_method", "cash"),
            amount_paid=to_decimal(paid) if paid not in (None, "") else None, notes=data.get("notes", ""))
    except ValueError as exc:
        raise SaleError(str(exc)) from exc


@role_required(FRONT)
def customer_api(request):
    q = request.GET.get("q", "").strip()
    if not q:
        return JsonResponse({"results": []})
    qs = Customer.objects.filter(tenant=request.tenant, is_active=True)
    for t in q.split():
        qs = qs.filter(Q(name__icontains=t) | Q(phone__icontains=t) | Q(vehicles__reg_number__icontains=t))
    results = [{
        "id": c.pk, "name": c.name, "phone": c.phone, "type": c.customer_type,
        "credit_limit": float(c.credit_limit), "balance": float(c.balance),
        "label": f"{c.name}" + (f" ({c.phone})" if c.phone else ""),
        "vehicles": [{"id": v.pk, "label": str(v)} for v in c.vehicles.all()],
    } for c in qs.distinct()[:15]]
    return JsonResponse({"results": results})


class SaleList(RoleRequiredMixin, ListView):
    allowed_roles = SALES
    template_name = "sales/sale_list.html"
    context_object_name = "sales"
    paginate_by = 30

    def get_queryset(self):
        qs = Sale.objects.filter(branch=self.request.branch).select_related("customer", "created_by")
        g = self.request.GET
        if self.request.user.role == "counter":
            qs = qs.filter(created_by=self.request.user)
        for t in g.get("q", "").split():
            qs = qs.filter(Q(number__icontains=t) | Q(customer__name__icontains=t))
        if g.get("due"):
            qs = qs.annotate(due=F("total") - F("returned_total") - F("amount_paid")).filter(due__gt=0)
        return qs

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx.update(page_title="Invoices", g=self.request.GET)
        return ctx


@role_required(SALES)
def sale_detail(request, pk):
    sale = get_object_or_404(Sale.objects.select_related("customer", "vehicle", "created_by"), pk=pk, branch=request.branch)
    if request.user.role == "counter" and sale.created_by_id != request.user.pk:
        return redirect("sale_list")
    job = getattr(sale, "job", None)
    return render(request, "sales/sale_detail.html", {
        "page_title": sale.number, "sale": sale, "lines": sale.lines.all(), "payments": sale.payments.all(),
        "returns": sale.returns.all(), "cfg": ShopSettings.get(request.tenant), "job": job,
        "pay_form": PaymentForm(), "can_return": request.user.role in MGMT and sale.status != "returned",
        "auto_print": request.GET.get("print") == "1"})


@role_required(SALES)
@require_POST
def sale_payment(request, pk):
    sale = get_object_or_404(Sale, pk=pk, branch=request.branch)
    if request.user.role == "counter" and sale.created_by_id != request.user.pk:
        raise PermissionDenied("You cannot pay another user's invoice.")
    form = PaymentForm(request.POST)
    if form.is_valid():
        cd = form.cleaned_data
        try:
            add_payment(sale=sale, amount=cd["amount"], method=cd["method"], user=request.user, reference=cd["reference"])
            messages.success(request, "Payment recorded.")
        except SaleError as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, "Enter a valid amount and method.")
    return redirect(sale)


@role_required(MGMT)
def sale_return(request, pk):
    sale = get_object_or_404(Sale.objects.select_related("customer"), pk=pk, branch=request.branch)
    lines = list(sale.lines.all())
    if request.method == "POST":
        try:
            items = {}
            for line in lines:
                raw = request.POST.get(f"qty_{line.id}", "").strip()
                if raw:
                    items[line.id] = to_decimal(raw)
            ret = process_return(sale=sale, user=request.user, items=items, reason=request.POST.get("reason", ""),
                                 restock=bool(request.POST.get("restock")),
                                 refund_method=request.POST.get("refund_method", "cash"))
            messages.success(request, f"Return {ret.number} recorded for {ret.total}.")
            return redirect(sale)
        except (ValueError, SaleError, StockError) as exc:
            messages.error(request, str(exc))
    return render(request, "sales/sale_return.html", {
        "page_title": f"Return items · {sale.number}", "sale": sale, "lines": lines,
        "methods": [m for m in Sale.Method.choices if m[0] != "credit"]})


class CustomerList(RoleRequiredMixin, ListView):
    allowed_roles = FRONT
    template_name = "sales/customer_list.html"
    context_object_name = "customers"
    paginate_by = 30

    def get_queryset(self):
        qs = Customer.objects.filter(tenant=self.request.tenant)
        for t in self.request.GET.get("q", "").split():
            qs = qs.filter(Q(name__icontains=t) | Q(phone__icontains=t) | Q(vehicles__reg_number__icontains=t))
        return qs.distinct()

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx.update(page_title="Customers", g=self.request.GET)
        return ctx


@role_required(FRONT)
def customer_detail(request, pk):
    customer = get_object_or_404(Customer, pk=pk, tenant=request.tenant)
    sales = customer.sales.filter(branch=request.branch)
    return render(request, "sales/customer_detail.html", {
        "page_title": customer.name, "customer": customer, "vehicles": customer.vehicles.all(),
        "sales": sales[:15], "owing": [s for s in sales if s.balance_due > 0],
        "jobs": customer.jobs.filter(branch=request.branch)[:10], "can_sell": request.user.role in SALES})


class CustomerCreate(TenantFormMixin, RoleRequiredMixin, CreateView):
    allowed_roles = FRONT
    form_class = CustomerForm
    template_name = "form.html"
    extra_context = {"page_title": "New customer", "cancel_url": "/sales/customers/"}


class CustomerUpdate(TenantFormMixin, RoleRequiredMixin, UpdateView):
    allowed_roles = FRONT
    model = Customer
    form_class = CustomerForm
    template_name = "form.html"

    def get_queryset(self):
        return Customer.objects.filter(tenant=self.request.tenant)

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx.update(page_title=f"Edit {self.object.name}", cancel_url=self.object.get_absolute_url())
        return ctx


@role_required(FRONT)
def vehicle_create(request, pk):
    customer = get_object_or_404(Customer, pk=pk, tenant=request.tenant)
    form = VehicleForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        v = form.save(commit=False)
        v.customer = customer
        v.save()
        messages.success(request, "Vehicle added.")
        return redirect(customer)
    return render(request, "form.html", {
        "page_title": f"Add vehicle for {customer.name}", "form": form, "cancel_url": customer.get_absolute_url()})
