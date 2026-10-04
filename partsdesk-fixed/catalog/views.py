from django.contrib import messages
from django.db.models import F
from django.http import Http404, JsonResponse
from django.http import FileResponse
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, ListView, UpdateView

from core.models import audit
from core.permissions import STOCK, RoleRequiredMixin, role_required
from stock.services import record_movement

from .forms import (CategoryForm, CrossRefForm, FitmentForm, PartCreateForm, PartForm,
                    SupplierForm, VehicleForm)
from .models import BranchStock, Category, Part, PartNumber, Supplier, Vehicle
from .search import search_parts, with_stock
from core.form_mixins import TenantFormMixin
from .forms import CatalogueUploadForm
from .models import CatalogueImport
from .excel_import import ImportProblem, confirm_import, import_plan, parse_workbook, validate_rows
from stock.services import StockError


class PartList(RoleRequiredMixin, ListView):
    template_name = "catalog/part_list.html"
    context_object_name = "parts"
    paginate_by = 25

    def get_queryset(self):
        g = self.request.GET
        qs = with_stock(search_parts(g.get("q", ""), self.request.tenant,
                                     active_only=not g.get("inactive")), self.request.branch).select_related("category")
        if g.get("category", "").isdigit():
            qs = qs.filter(category_id=g["category"])
        if g.get("condition") in Part.Condition.values:
            qs = qs.filter(condition=g["condition"])
        if g.get("stock") == "out":
            qs = qs.filter(branch_quantity__lte=0)
        elif g.get("stock") == "low":
            qs = qs.filter(branch_reorder_level__gt=0, branch_quantity__lte=F("branch_reorder_level"))
        return qs.order_by("name")

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx.update(page_title="Parts", categories=Category.objects.filter(tenant=self.request.tenant), conditions=Part.Condition.choices,
                   can_edit=self.request.user.role in STOCK, g=self.request.GET)
        return ctx


@role_required()
def part_detail(request, pk):
    part = get_object_or_404(Part.objects.select_related("category", "preferred_supplier"), pk=pk, tenant=request.tenant)
    stock, _ = BranchStock.objects.get_or_create(branch=request.branch, part=part,
                                                 defaults={"cost_price": part.cost_price})
    can_edit = request.user.role in STOCK
    return render(request, "catalog/part_detail.html", {
        "page_title": part.name, "part": part, "stock": stock, "can_edit": can_edit,
        "margin_pct": (part.sell_price - stock.cost_price) / part.sell_price * 100 if part.sell_price else None,
        "fits": part.fits.all(), "cross_refs": part.cross_refs.all(),
        "movements": part.movements.filter(branch=request.branch).select_related("user")[:10] if can_edit else [],
        "fit_form": FitmentForm(tenant=request.tenant), "xref_form": CrossRefForm(),
    })


class PartCreate(TenantFormMixin, RoleRequiredMixin, CreateView):
    allowed_roles = STOCK
    form_class = PartCreateForm
    template_name = "form.html"
    extra_context = {"page_title": "New part", "cancel_url": reverse_lazy("part_list")}

    def form_valid(self, form):
        resp = super().form_valid(form)
        opening = form.cleaned_data.get("opening_qty")
        if opening and opening > 0:
            record_movement(self.object, opening, "opening", self.request.user, note="Opening stock", branch=self.request.branch)
        messages.success(self.request, f"Added {self.object.name}.")
        return resp


class PartUpdate(TenantFormMixin, RoleRequiredMixin, UpdateView):
    allowed_roles = STOCK
    model = Part
    form_class = PartForm
    template_name = "form.html"

    def get_queryset(self):
        return Part.objects.filter(tenant=self.request.tenant)

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx.update(page_title=f"Edit {self.object.name}", cancel_url=self.object.get_absolute_url())
        return ctx

    def form_valid(self, form):
        changed = [f for f in ("cost_price", "sell_price", "trade_price") if f in form.changed_data]
        resp = super().form_valid(form)
        if changed:
            detail = "; ".join(f"{f}: {form.initial.get(f)} → {form.cleaned_data[f]}" for f in changed)
            audit(self.request.user, "part.price", self.object.sku, detail, branch=self.request.branch)
        messages.success(self.request, "Part saved.")
        return resp


@role_required(STOCK)
@require_POST
def part_fitment_add(request, pk):
    part = get_object_or_404(Part, pk=pk, tenant=request.tenant)
    form = FitmentForm(request.POST, tenant=request.tenant)
    if form.is_valid():
        part.fits.add(form.cleaned_data["vehicle"])
    return redirect(part)


@role_required(STOCK)
@require_POST
def part_fitment_remove(request, pk, vehicle_id):
    part = get_object_or_404(Part, pk=pk, tenant=request.tenant)
    if not Vehicle.objects.filter(pk=vehicle_id, tenant=request.tenant).exists():
        raise Http404
    part.fits.remove(vehicle_id)
    return redirect(part)


@role_required(STOCK)
@require_POST
def part_xref_add(request, pk):
    part = get_object_or_404(Part, pk=pk, tenant=request.tenant)
    form = CrossRefForm(request.POST)
    if form.is_valid():
        xref = form.save(commit=False)
        xref.part = part
        xref.save()
    else:
        messages.error(request, "Enter a part number for the cross-reference.")
    return redirect(part)


@role_required(STOCK)
@require_POST
def part_xref_remove(request, pk, xref_id):
    part = get_object_or_404(Part, pk=pk, tenant=request.tenant)
    PartNumber.objects.filter(pk=xref_id, part=part).delete()
    return redirect(part)


@role_required()
def part_search_api(request):
    """JSON search used by the point of sale and part pickers."""
    q = request.GET.get("q", "").strip()
    if not q:
        return JsonResponse({"results": []})
    results = []
    for p in with_stock(search_parts(q, request.tenant), request.branch).select_related("category")[:30]:
        results.append({
            "id": p.pk, "sku": p.sku, "barcode": p.barcode or "", "name": p.name, "brand": p.brand,
            "condition": p.get_condition_display(), "part_type": p.get_part_type_display(),
            "bin": p.branch_bin, "unit": p.unit, "qty": float(p.branch_quantity),
            "price": float(p.sell_price), "trade_price": float(p.trade_price) if p.trade_price else None,
            "label": f"{p.sku} — {p.name}",
        })
    needle = q.lower()
    results.sort(key=lambda r: (needle not in (r["sku"].lower(), r["barcode"].lower()), r["name"]))
    return JsonResponse({"results": results})


@role_required(STOCK)
def category_list(request):
    form = CategoryForm(request.POST or None, tenant=request.tenant)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Category added.")
        return redirect("category_list")
    return render(request, "catalog/category_list.html", {
        "page_title": "Categories", "form": form, "categories": Category.objects.filter(tenant=request.tenant)})


@role_required(STOCK)
def catalogue_upload(request):
    form = CatalogueUploadForm(request.POST or None, request.FILES or None)
    errors = []
    if request.method == 'POST' and form.is_valid():
        try:
            rows = parse_workbook(form.cleaned_data['file'])
            rows = validate_rows(rows, request.tenant, mode=form.cleaned_data['mode'])
            batch = CatalogueImport.objects.create(tenant=request.tenant, branch=request.branch,
                created_by=request.user, file_name=form.cleaned_data['file'].name[:255],
                mode=form.cleaned_data['mode'], rows=rows)
            return redirect('catalogue_preview', pk=batch.pk)
        except ImportProblem as exc:
            errors = exc.errors
            if exc.total_errors > len(errors):
                errors.append(f'{exc.total_errors} errors in total. Showing the first 100; correct the file and upload it again.')
    return render(request, 'catalog/catalogue_upload.html', {'page_title': 'Upload catalogue', 'form': form,
        'errors': errors, 'recent_imports': CatalogueImport.objects.filter(tenant=request.tenant,
            branch=request.branch, created_by=request.user)[:10]})


@role_required(STOCK)
def catalogue_template(request):
    path = settings.BASE_DIR / 'catalog' / 'resources' / 'PartsDesk-Catalogue-Template.xlsx'
    return FileResponse(path.open('rb'), as_attachment=True, filename=path.name,
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


def _catalogue_batch(request, pk):
    return get_object_or_404(CatalogueImport.objects.select_related('tenant', 'branch'), pk=pk,
        tenant=request.tenant, branch=request.branch, created_by=request.user)


@role_required(STOCK)
def catalogue_preview(request, pk):
    batch = _catalogue_batch(request, pk)
    rows = import_plan(batch.rows, request.tenant, request.branch)
    expired = timezone.now() > batch.created_at + timedelta(hours=1)
    summary = {'created': sum(r['action'] == 'Add' for r in rows), 'updated': sum(r['action'] == 'Update' for r in rows),
               'openings': sum(Decimal(r['opening']) > 0 for r in rows), 'ignored_openings': sum(r['ignored_opening'] for r in rows)}
    return render(request, 'catalog/catalogue_preview.html', {'page_title': 'Review catalogue upload',
        'batch': batch, 'rows': rows[:100], 'row_count': len(rows), 'summary': summary,
        'can_import': batch.status == CatalogueImport.Status.READY and not expired, 'expired': expired})


@role_required(STOCK)
@require_POST
def catalogue_confirm(request, pk):
    batch = _catalogue_batch(request, pk)
    try:
        result = confirm_import(batch, request.user)
        messages.success(request, f"Catalogue imported: {result['created']} added, {result['updated']} updated, "
            f"{result['openings']} opening stock entries. {result['ignored_openings']} opening quantities skipped for parts with existing stock/history.")
    except (ImportProblem, ValidationError, IntegrityError, StockError) as exc:
        messages.error(request, 'No catalogue changes were saved. ' + (str(exc) if not isinstance(exc, IntegrityError)
            else 'A conflicting SKU or barcode was found. Upload the corrected file again.'))
    return redirect('catalogue_preview', pk=batch.pk)


@role_required(STOCK)
@require_POST
def catalogue_cancel(request, pk):
    batch = _catalogue_batch(request, pk)
    with transaction.atomic():
        batch = CatalogueImport.objects.select_for_update().get(pk=batch.pk)
        if batch.status == CatalogueImport.Status.READY:
            batch.status = CatalogueImport.Status.CANCELLED
            batch.save(update_fields=['status'])
    return redirect('catalogue_upload')


class VehicleList(RoleRequiredMixin, ListView):
    template_name = "catalog/vehicle_list.html"
    paginate_by = 40
    context_object_name = "vehicles"

    def get_queryset(self):
        qs = Vehicle.objects.filter(tenant=self.request.tenant)
        for token in self.request.GET.get("q", "").split():
            qs = qs.filter(make__icontains=token) | qs.filter(model__icontains=token)
        return qs

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx.update(page_title="Vehicles", can_edit=self.request.user.role in STOCK)
        return ctx


class VehicleCreate(TenantFormMixin, RoleRequiredMixin, CreateView):
    allowed_roles = STOCK
    form_class = VehicleForm
    template_name = "form.html"
    success_url = reverse_lazy("vehicle_list")
    extra_context = {"page_title": "New vehicle", "cancel_url": reverse_lazy("vehicle_list")}


class VehicleUpdate(TenantFormMixin, RoleRequiredMixin, UpdateView):
    allowed_roles = STOCK
    model = Vehicle
    form_class = VehicleForm
    template_name = "form.html"
    success_url = reverse_lazy("vehicle_list")
    extra_context = {"page_title": "Edit vehicle", "cancel_url": reverse_lazy("vehicle_list")}

    def get_queryset(self):
        return Vehicle.objects.filter(tenant=self.request.tenant)


class SupplierList(RoleRequiredMixin, ListView):
    allowed_roles = STOCK
    model = Supplier
    template_name = "catalog/supplier_list.html"
    context_object_name = "suppliers"
    extra_context = {"page_title": "Suppliers"}

    def get_queryset(self):
        return Supplier.objects.filter(tenant=self.request.tenant)


class SupplierCreate(TenantFormMixin, RoleRequiredMixin, CreateView):
    allowed_roles = STOCK
    form_class = SupplierForm
    template_name = "form.html"
    success_url = reverse_lazy("supplier_list")
    extra_context = {"page_title": "New supplier", "cancel_url": reverse_lazy("supplier_list")}


class SupplierUpdate(TenantFormMixin, RoleRequiredMixin, UpdateView):
    allowed_roles = STOCK
    model = Supplier
    form_class = SupplierForm
    template_name = "form.html"
    success_url = reverse_lazy("supplier_list")
    extra_context = {"page_title": "Edit supplier", "cancel_url": reverse_lazy("supplier_list")}

    def get_queryset(self):
        return Supplier.objects.filter(tenant=self.request.tenant)
