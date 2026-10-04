from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from catalog.models import Part
from core.models import ShopSettings
from core.permissions import FRONT, SALES, role_required
from core.utils import optional_id
from sales.models import Customer
from sales.services import SaleError
from stock.services import StockError

from . import services
from .forms import (JobCreateForm, JobInvoiceForm, JobLabourForm, JobPartForm, JobUpdateForm)
from .models import JobCard, JobLabour, JobPart

S = JobCard.Status


@role_required(FRONT)
def job_list(request):
    g = request.GET
    qs = JobCard.objects.filter(branch=request.branch).select_related("customer", "vehicle", "assigned_to")
    status = g.get("status", "active")
    if status == "active":
        qs = qs.exclude(status__in=[S.INVOICED, S.CANCELLED])
    elif status in S.values:
        qs = qs.filter(status=status)
    if g.get("mine"):
        qs = qs.filter(assigned_to=request.user)
    for t in g.get("q", "").split():
        qs = qs.filter(Q(number__icontains=t) | Q(customer__name__icontains=t) | Q(vehicle__reg_number__icontains=t))
    counts = dict(JobCard.objects.filter(branch=request.branch).exclude(status__in=[S.INVOICED, S.CANCELLED]).values_list("status").annotate(n=Count("id")))
    return render(request, "workshop/job_list.html", {
        "page_title": "Job cards", "jobs": qs[:200], "g": g, "status": status, "counts": counts,
        "statuses": [c for c in S.choices if c[0] not in ("invoiced", "cancelled")]})


@role_required(FRONT)
def job_create(request):
    initial = {"customer": optional_id(request.GET.get("customer"))}
    form = JobCreateForm(request.POST or None, initial=initial, tenant=request.tenant, branch=request.branch)
    customer = Customer.objects.filter(pk=optional_id(request.POST.get("customer") or request.GET.get("customer")), tenant=request.tenant).first()
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        job = services.create_job(user=request.user, branch=request.branch, customer=cd["customer"], vehicle=cd["vehicle"],
                                  complaint=cd["complaint"], odometer=cd["odometer"],
                                  assigned_to=cd["assigned_to"], promised_date=cd["promised_date"])
        messages.success(request, f"Job card {job.number} opened.")
        return redirect(job)
    return render(request, "workshop/job_form.html", {"page_title": "New job card", "form": form, "customer": customer})


@role_required(FRONT)
def job_detail(request, pk):
    job = get_object_or_404(JobCard.objects.select_related("customer", "vehicle", "assigned_to", "invoice"), pk=pk, branch=request.branch)
    if request.method == "POST":  # job details form
        form = JobUpdateForm(request.POST, instance=job, tenant=request.tenant, branch=request.branch)
        if not job.is_locked and form.is_valid():
            form.save()
            messages.success(request, "Job updated.")
            return redirect(job)
    else:
        form = JobUpdateForm(instance=job, tenant=request.tenant, branch=request.branch)
    cfg = ShopSettings.get(request.tenant)
    return render(request, "workshop/job_detail.html", {
        "page_title": job.number, "job": job, "form": form, "parts": job.parts.select_related("part"),
        "labour": job.labour.all(), "part_form": JobPartForm(tenant=request.tenant), "labour_form": JobLabourForm(initial={"rate": cfg.default_labour_rate}),
        "invoice_form": JobInvoiceForm(), "can_invoice": request.user.role in SALES})


def _run(request, job, fn, *args):
    try:
        fn(*args)
    except (SaleError, StockError) as exc:
        messages.error(request, str(exc))
    return redirect(job)


@role_required(FRONT)
@require_POST
def job_add_part(request, pk):
    job = get_object_or_404(JobCard, pk=pk, branch=request.branch)
    form = JobPartForm(request.POST, tenant=request.tenant)
    if not form.is_valid():
        messages.error(request, "Pick a part from the list and enter a quantity.")
        return redirect(job)
    return _run(request, job, services.add_part, job, form.cleaned_data["part"], form.cleaned_data["quantity"], request.user)


@role_required(FRONT)
@require_POST
def job_remove_part(request, pk, item_id):
    job = get_object_or_404(JobCard, pk=pk, branch=request.branch)
    return _run(request, job, services.remove_part, get_object_or_404(JobPart, pk=item_id, job=job), request.user)


@role_required(FRONT)
@require_POST
def job_add_labour(request, pk):
    job = get_object_or_404(JobCard, pk=pk, branch=request.branch)
    form = JobLabourForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Enter a description, hours and rate.")
        return redirect(job)
    cd = form.cleaned_data
    return _run(request, job, services.add_labour, job, cd["description"], cd["hours"], cd["rate"])


@role_required(FRONT)
@require_POST
def job_remove_labour(request, pk, item_id):
    job = get_object_or_404(JobCard, pk=pk, branch=request.branch)
    return _run(request, job, services.remove_labour, get_object_or_404(JobLabour, pk=item_id, job=job))


@role_required(SALES)
@require_POST
def job_invoice(request, pk):
    job = get_object_or_404(JobCard, pk=pk, branch=request.branch)
    form = JobInvoiceForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Choose a payment method.")
        return redirect(job)
    try:
        sale = services.invoice_job(job, request.user, form.cleaned_data["payment_method"], form.cleaned_data["amount_paid"])
    except (SaleError, StockError) as exc:
        messages.error(request, str(exc))
        return redirect(job)
    messages.success(request, f"Invoice {sale.number} created.")
    return redirect(sale)


@role_required(SALES)
@require_POST
def job_cancel(request, pk):
    job = get_object_or_404(JobCard, pk=pk, branch=request.branch)
    return _run(request, job, services.cancel_job, job, request.user)
