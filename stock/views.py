from decimal import Decimal

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, ListView

from catalog.models import BranchStock, Part
from core.models import Sequence
from core.form_mixins import TenantFormMixin
from core.permissions import STOCK, RoleRequiredMixin, role_required
from core.utils import optional_id, to_decimal

from .forms import AdjustForm, POForm, POLineForm
from .models import PurchaseOrder, StockMovement
from .services import (StockError, add_po_line, adjust_to_count, draft_pos_from_low_stock,
                       receive_po)

S = PurchaseOrder.Status


class MovementList(RoleRequiredMixin, ListView):
    allowed_roles = STOCK
    template_name = "stock/movement_list.html"
    context_object_name = "movements"
    paginate_by = 40

    def get_queryset(self):
        qs = StockMovement.objects.filter(branch=self.request.branch).select_related("part", "user")
        g = self.request.GET
        if optional_id(g.get("part")):
            qs = qs.filter(part_id=optional_id(g["part"]))
        if g.get("reason") in StockMovement.Reason.values:
            qs = qs.filter(reason=g["reason"])
        return qs

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        part = Part.objects.filter(pk=optional_id(self.request.GET.get("part")), tenant=self.request.tenant).first()
        ctx.update(page_title="Stock ledger", reasons=StockMovement.Reason.choices, part=part,
                   g=self.request.GET)
        return ctx


@role_required(STOCK)
def adjust(request):
    part = Part.objects.filter(pk=optional_id(request.GET.get("part") or request.POST.get("part")), tenant=request.tenant).first()
    form = AdjustForm(request.POST or None, initial={"part": part}, tenant=request.tenant)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        note = cd["reason"] + (f": {cd['note']}" if cd["note"] else "")
        move = adjust_to_count(cd["part"], cd["counted"], request.user, note, branch=request.branch)
        messages.success(request, "Stock adjusted." if move else "Count matches the system — nothing to change.")
        return redirect(cd["part"])
    return render(request, "stock/adjust.html", {"page_title": "Adjust stock", "form": form, "part": part})


class POList(RoleRequiredMixin, ListView):
    allowed_roles = STOCK
    template_name = "stock/po_list.html"
    context_object_name = "orders"
    paginate_by = 30

    def get_queryset(self):
        qs = PurchaseOrder.objects.filter(branch=self.request.branch).select_related("supplier").prefetch_related("lines")
        status = self.request.GET.get("status")
        return qs.filter(status=status) if status in S.values else qs

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx.update(page_title="Purchase orders", statuses=S.choices, g=self.request.GET)
        return ctx


class POCreate(TenantFormMixin, RoleRequiredMixin, CreateView):
    allowed_roles = STOCK
    form_class = POForm
    template_name = "form.html"
    extra_context = {"page_title": "New purchase order", "cancel_url": "/stock/purchase-orders/"}

    def form_valid(self, form):
        form.instance.number = Sequence.next(self.request.branch, "po", "PO")
        form.instance.created_by = self.request.user
        return super().form_valid(form)


@role_required(STOCK)
def po_detail(request, pk):
    po = get_object_or_404(PurchaseOrder.objects.select_related("supplier"), pk=pk, branch=request.branch)
    return render(request, "stock/po_detail.html", {
        "page_title": po.number, "po": po, "lines": po.lines.select_related("part"),
        "line_form": POLineForm(tenant=request.tenant), "S": S})


@role_required(STOCK)
@require_POST
def po_add_line(request, pk):
    po = get_object_or_404(PurchaseOrder, pk=pk, branch=request.branch)
    form = POLineForm(request.POST, tenant=request.tenant)
    if form.is_valid():
        cd = form.cleaned_data
        item = BranchStock.objects.filter(branch=request.branch, part=cd["part"]).first()
        cost = cd["unit_cost"] if cd["unit_cost"] is not None else (item.cost_price if item else cd["part"].cost_price)
        try:
            add_po_line(po, cd["part"], cd["quantity"], cost)
        except StockError as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, "Pick a part from the list and enter a quantity.")
    return redirect(po)


@role_required(STOCK)
@require_POST
def po_del_line(request, pk, line_id):
    po = get_object_or_404(PurchaseOrder, pk=pk, branch=request.branch, status=S.DRAFT)
    po.lines.filter(pk=line_id).delete()
    return redirect(po)


@role_required(STOCK)
@require_POST
def po_status(request, pk):
    po = get_object_or_404(PurchaseOrder, pk=pk, branch=request.branch)
    action = request.POST.get("action")
    if action == "order" and po.status == S.DRAFT:
        if not po.lines.exists():
            messages.error(request, "Add at least one line before ordering.")
        else:
            po.status = S.ORDERED
            po.save(update_fields=["status"])
            messages.success(request, "Marked as ordered.")
    elif action == "cancel" and po.status in (S.DRAFT, S.ORDERED):
        po.status = S.CANCELLED
        po.save(update_fields=["status"])
    return redirect(po)


@role_required(STOCK)
@require_POST
def po_receive(request, pk):
    po = get_object_or_404(PurchaseOrder, pk=pk, branch=request.branch)
    quantities = {}
    try:
        for line in po.lines.all():
            raw = request.POST.get(f"recv_{line.id}", "")
            quantities[line.id] = to_decimal(raw, default=Decimal("0"))
            if quantities[line.id] < 0:
                raise ValueError("Quantities cannot be negative.")
        receive_po(po, quantities, request.user, request.POST.get("supplier_ref", "").strip())
        messages.success(request, "Stock received and added to inventory.")
    except (ValueError, StockError) as exc:
        messages.error(request, str(exc))
    return redirect(po)


@role_required(STOCK)
@require_POST
def po_from_low_stock(request):
    pos = draft_pos_from_low_stock(request.user, request.branch)
    if not pos:
        messages.info(request, "Nothing to order: no low-stock parts with a preferred supplier that aren't already on order.")
        return redirect("po_list")
    messages.success(request, f"Created or updated {len(pos)} draft purchase order(s). Review and mark them as ordered.")
    return redirect(pos[0]) if len(pos) == 1 else redirect("po_list")
