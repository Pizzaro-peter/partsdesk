from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, F, Max, Q, Sum
from django.shortcuts import render
from django.utils import timezone
from django.views.generic import UpdateView
from django.core.paginator import Paginator
from django import forms
from django.contrib.auth import get_user_model
from django.contrib import messages
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from catalog.models import BranchStock, Part
from core.models import AuditLog, Branch, ShopSettings
from core.permissions import FRONT, MGMT, OWNER_ONLY, SALES, RoleRequiredMixin, role_required
from core.tenancy import allowed_branches
from core.utils import csv_response, money_str, optional_id, qty_str
from sales.models import Customer, Sale
from sales.reporting import daily_figures, part_figures
from workshop.models import JobCard

ZERO = Decimal("0")
DEC = DecimalField(max_digits=16, decimal_places=2)


def _low_stock_qs(branch):
    return BranchStock.objects.filter(branch=branch, part__is_active=True, reorder_level__gt=0,
                                      quantity_on_hand__lte=F("reorder_level")).select_related("part", "part__preferred_supplier")


@role_required()
def dashboard(request):
    user, today = request.user, timezone.localdate()
    kpis, ctx = [], {"page_title": "Dashboard"}

    if user.role in MGMT:
        week = list(daily_figures(today - timedelta(days=6), today, request.branch).values())
        month = list(daily_figures(today.replace(day=1), today, request.branch).values())
        stock_value = BranchStock.objects.filter(branch=request.branch).aggregate(v=Sum(F("quantity_on_hand") * F("cost_price"), output_field=DEC))["v"] or ZERO
        owed = Sale.objects.filter(branch=request.branch, total__gt=F("returned_total") + F("amount_paid")).aggregate(
            v=Sum(F("total") - F("returned_total") - F("amount_paid")))["v"] or ZERO
        today_row = week[-1]
        kpis = [
            dict(label="Sales today (excl. tax)", value=today_row["revenue"], kind="money", sub=f"{today_row['invoices']} invoices"),
            dict(label="Sales this month", value=sum(r["revenue"] for r in month), kind="money",
                 sub=f"{sum(r['invoices'] for r in month)} invoices"),
            dict(label="Gross profit this month", value=sum(r["profit"] for r in month), kind="money", sub="after cost of parts"),
            dict(label="Owed by customers", value=owed, kind="money", sub="unpaid invoices", href="/sales/invoices/?due=1"),
            dict(label="Stock value at cost", value=stock_value, kind="money", sub="on the shelves"),
        ]
        peak = max((r["revenue"] for r in week), default=ZERO) or Decimal("1")
        ctx["bars"] = [dict(day=r["date"], revenue=r["revenue"], pct=int(r["revenue"] / peak * 100)) for r in week]
    elif user.role in SALES:
        mine = Sale.objects.filter(branch=request.branch, created_by=user, created_at__date=today)
        agg = mine.aggregate(n=Count("id"), v=Sum("total"))
        kpis = [dict(label="Your sales today", value=agg["v"] or ZERO, kind="money", sub=f"{agg['n']} invoices")]

    open_jobs = JobCard.objects.filter(branch=request.branch).exclude(status__in=["invoiced", "cancelled"])
    if user.role not in FRONT:
        open_jobs = open_jobs.none()
    if user.role == "mechanic":
        open_jobs = open_jobs.filter(assigned_to=user)
    if user.role in FRONT:
        kpis.append(dict(label="Open job cards" if user.role != "mechanic" else "Your open jobs",
                         value=open_jobs.count(), kind="num", sub="in the workshop", href="/workshop/"))
    low = _low_stock_qs(request.branch)
    kpis.append(dict(label="Low-stock parts", value=low.count(), kind="num", sub="at or below reorder level",
                     href="/catalog/?stock=low"))

    ctx.update(
        kpis=kpis, low_parts=low.order_by("quantity_on_hand")[:8], can_view_jobs=user.role in FRONT,
        jobs=open_jobs.select_related("customer", "vehicle").order_by("promised_date", "created_at")[:8],
        recent_sales=Sale.objects.filter(branch=request.branch).select_related("customer").filter(
            **({"created_by": user} if user.role == "counter" else {}))[:8] if user.role in SALES else [],
    )
    return render(request, "core/dashboard.html", ctx)


# ---------------------------------------------------------------- reports

def _range(request):
    today = timezone.localdate()
    try:
        start = date.fromisoformat(request.GET.get("start", ""))
    except ValueError:
        start = today.replace(day=1)
    try:
        end = date.fromisoformat(request.GET.get("end", ""))
    except ValueError:
        end = today
    return start, min(max(end, start), start + timedelta(days=min(370, (date.max - start).days)))


def _report(request, title, columns, rows, *, csv_name, totals=None, dates=None, note="", post_action=None, extra_filter=None):
    """columns: [(label, kind)] with kind in text|money|qty. rows: raw values."""
    if request.GET.get("csv"):
        return csv_response(csv_name, [c[0] for c in columns], rows)
    sym = ShopSettings.get(request.tenant).currency_symbol

    def fmt(v, kind):
        if kind == "money":
            return money_str(v, sym)
        if kind == "qty":
            return qty_str(v)
        return "" if v is None else str(v)

    def build(row):
        return [dict(v=fmt(v, k), num=k != "text") for v, (_, k) in zip(row, columns)]

    return render(request, "core/report.html", {
        "page_title": title, "columns": [dict(label=l, num=k != "text") for l, k in columns],
        "rows": [build(r) for r in rows], "totals": build(totals) if totals else None,
        "dates": dates, "note": note, "post_action": post_action, "extra_filter": extra_filter, "empty": not rows,
    })


@role_required(MGMT)
def reports_home(request):
    cards = [
        ("Sales & profit by day", "report_sales", "Revenue, tax, cost of parts and gross profit for any date range."),
        ("Top sellers", "report_top", "Which parts earn the most revenue and profit."),
        ("Low stock & reorder", "report_low_stock", "What to buy now, with one click to draft purchase orders."),
        ("Slow movers", "report_slow", "Stock that hasn't sold lately and ties up cash."),
        ("Stock value", "report_stock_value", "What's on the shelves, at cost and at retail, by category."),
        ("Customers who owe", "report_receivables", "Outstanding balances on credit accounts."),
    ]
    return render(request, "core/reports_home.html", {"page_title": "Reports", "cards": cards})


@role_required(MGMT)
def report_sales(request):
    start, end = _range(request)
    days = [r for r in daily_figures(start, end, request.branch).values()]
    rows = [[r["date"], r["invoices"], r["revenue"], r["tax"], r["cost"], r["profit"]] for r in days]
    totals = ["Total", sum(r[1] for r in rows)] + [sum(r[i] for r in rows) for i in range(2, 6)]
    cols = [("Date", "text"), ("Invoices", "text"), ("Revenue (excl. tax)", "money"), ("Tax", "money"),
            ("Cost of parts", "money"), ("Gross profit", "money")]
    return _report(request, "Sales & profit by day", cols, [r for r in rows if r[1]], totals=totals,
                   dates=(start, end), csv_name="sales.csv", note="Returned items are excluded.")


@role_required(MGMT)
def report_top(request):
    start, end = _range(request)
    data = part_figures(start, end, request.branch)[:50]
    rows = [[d["part"].sku, d["part"].name, d["qty"], d["revenue"], d["profit"]] for d in data]
    cols = [("Code", "text"), ("Part", "text"), ("Qty sold", "qty"), ("Revenue (excl. tax)", "money"), ("Gross profit", "money")]
    return _report(request, "Top sellers", cols, rows, dates=(start, end), csv_name="top-sellers.csv", note="Top 50 by revenue.")


@role_required(MGMT)
def report_low_stock(request):
    rows = [[s.part.sku, s.part.name, s.quantity_on_hand, s.reorder_level, s.reorder_qty,
             s.part.preferred_supplier.name if s.part.preferred_supplier else "— none —", s.bin_location]
            for s in _low_stock_qs(request.branch).order_by("part__preferred_supplier__name", "part__name")]
    cols = [("Code", "text"), ("Part", "text"), ("On hand", "qty"), ("Reorder level", "qty"), ("Usual order qty", "qty"),
            ("Supplier", "text"), ("Bin", "text")]
    return _report(request, "Low stock & reorder", cols, rows, csv_name="low-stock.csv",
                   note="Parts with no preferred supplier are skipped when drafting purchase orders.",
                   post_action=dict(url="/stock/purchase-orders/from-low-stock/", label="Draft purchase orders") if rows else None)


@role_required(MGMT)
def report_slow(request):
    raw_days = request.GET.get("days", "")
    days = min(int(raw_days), 3650) if raw_days.isascii() and raw_days.isdecimal() and len(raw_days) <= 9 and int(raw_days) > 0 else 90
    cutoff = timezone.now() - timedelta(days=days)
    parts = (BranchStock.objects.filter(branch=request.branch, part__is_active=True, quantity_on_hand__gt=0, part__created_at__lt=cutoff)
             .annotate(last_sale=Max("part__movements__created_at", filter=Q(part__movements__reason="sale", part__movements__branch=request.branch)))
             .filter(Q(last_sale__lt=cutoff) | Q(last_sale__isnull=True)).order_by("last_sale"))
    rows = [[s.part.sku, s.part.name, s.quantity_on_hand, s.quantity_on_hand * s.cost_price,
             timezone.localtime(s.last_sale).date() if s.last_sale else "never"] for s in parts]
    totals = ["Total", "", sum(r[2] for r in rows), sum(r[3] for r in rows), ""]
    cols = [("Code", "text"), ("Part", "text"), ("On hand", "qty"), ("Cash tied up (cost)", "money"), ("Last sold", "text")]
    return _report(request, "Slow movers", cols, rows, totals=totals if rows else None, csv_name="slow-movers.csv",
                   note=f"In stock, but not sold in the last {days} days.",
                   extra_filter=dict(name="days", label="Days without a sale", value=days))


@role_required(MGMT)
def report_stock_value(request):
    data = (BranchStock.objects.filter(branch=request.branch, part__is_active=True).values("part__category__name")
            .annotate(items=Count("id"), units=Sum("quantity_on_hand"),
                      cost=Sum(F("quantity_on_hand") * F("cost_price"), output_field=DEC),
                      retail=Sum(F("quantity_on_hand") * F("part__sell_price"), output_field=DEC)).order_by("part__category__name"))
    rows = [[d["part__category__name"] or "Uncategorised", d["items"], d["units"] or ZERO, d["cost"] or ZERO, d["retail"] or ZERO] for d in data]
    totals = ["Total", sum(r[1] for r in rows), sum(r[2] for r in rows), sum(r[3] for r in rows), sum(r[4] for r in rows)]
    cols = [("Category", "text"), ("Part lines", "text"), ("Units", "qty"), ("Value at cost", "money"), ("Value at retail", "money")]
    return _report(request, "Stock value", cols, rows, totals=totals, csv_name="stock-value.csv")


@role_required(MGMT)
def report_receivables(request):
    owing = (Customer.objects.filter(tenant=request.tenant, sales__branch=request.branch).annotate(due=Sum(F("sales__total") - F("sales__returned_total") - F("sales__amount_paid")))
             .filter(due__gt=0).order_by("-due"))
    rows = [[c.name, c.phone, c.credit_limit, c.due] for c in owing]
    totals = ["Total", "", None, sum(r[3] for r in rows)]
    cols = [("Customer", "text"), ("Phone", "text"), ("Credit limit", "money"), ("Owes", "money")]
    return _report(request, "Customers who owe", cols, rows, totals=totals if rows else None, csv_name="receivables.csv")


@role_required(MGMT)
def audit_list(request):
    from .models import RequestLog, User
    from zoneinfo import ZoneInfo
    from datetime import datetime, time
    log_view = 'requests' if request.GET.get('view') == 'requests' else 'events'
    model = RequestLog if log_view == 'requests' else AuditLog
    allowed = allowed_branches(request.user)
    qs = model.objects.filter(tenant=request.tenant).select_related('user', 'branch')
    branch_value = request.GET.get('branch', str(request.branch.pk))
    if branch_value == 'all':
        scope = Q(branch__in=allowed)
    else:
        chosen = allowed.filter(pk=optional_id(branch_value)).first()
        scope = Q(branch=chosen) if chosen else Q(pk__in=[])
    if request.user.role == 'owner':
        scope |= Q(branch__isnull=True)
    qs = qs.filter(scope)
    if request.GET.get('user'):
        selected = User.objects.filter(tenant=request.tenant, pk=optional_id(request.GET['user'])).first()
        qs = qs.filter(Q(user=selected) | Q(user__isnull=True, actor_name=selected.username)) if selected else qs.none()
    outcome = request.GET.get('outcome', '')
    if outcome in ('success', 'failure', 'denied'): qs = qs.filter(outcome=outcome)
    action = request.GET.get('action', '')[:100]
    if action: qs = qs.filter(**{('route__icontains' if log_view == 'requests' else 'action__icontains'): action})
    search = request.GET.get('q', '')[:200]
    if search:
        qs = qs.filter(Q(target__icontains=search) | Q(actor_name__icontains=search) | Q(request_id__icontains=search))
    errors = []
    zambia = ZoneInfo('Africa/Lusaka')
    for key in ('start', 'end'):
        value = request.GET.get(key, '')
        if value:
            try:
                day = date.fromisoformat(value)
                if day.year < 1900 or day.year > 9998:
                    raise ValueError("Date outside supported range")
                bound = datetime.combine(day, time.min if key == 'start' else time.max, tzinfo=zambia)
                qs = qs.filter(**{('created_at__gte' if key == 'start' else 'created_at__lte'): bound})
            except ValueError:
                errors.append(f'Invalid {key} date. Use YYYY-MM-DD.')
                qs = qs.none()
    if request.GET.get('csv'):
        if log_view == 'requests':
            headers = ['Zambia time','User','Branch','Method','Route','Record IDs','HTTP status','Result','Request ID','IP address','Duration ms']
            rows = ([e.created_at.astimezone(zambia).isoformat(), e.actor_name, e.branch.code if e.branch else 'Business-wide', e.method,e.route,e.target,e.status_code,e.outcome,e.request_id,e.ip_address,e.duration_ms] for e in qs.iterator())
        else:
            import json
            headers = ['Zambia time','User','Branch','Kind','Action','Target','Result','Detail','Changes','Request ID','IP address']
            rows = ([e.created_at.astimezone(zambia).isoformat(),e.actor_name,e.branch.code if e.branch else 'Business-wide',e.kind,e.action,e.target,e.outcome,e.detail,json.dumps(e.changes,ensure_ascii=False),e.request_id,e.ip_address] for e in qs.iterator())
        return csv_response('request-log.csv' if log_view == 'requests' else 'audit-log.csv',headers,rows)
    page = Paginator(qs, 50).get_page(request.GET.get('page'))
    return render(request, 'core/audit_list.html', {'page_title': 'Request log' if log_view == 'requests' else 'Audit log',
        'page_obj':page,'entries':page,'log_view':log_view,'g':request.GET,'errors':errors,
        'audit_branches':allowed,'branch_value':branch_value,'audit_users':User.objects.filter(tenant=request.tenant)})


class ShopSettingsForm(forms.ModelForm):
    tax_rate = forms.DecimalField(min_value=0, max_value=100, max_digits=5, decimal_places=2)
    counter_max_discount = forms.DecimalField(min_value=0, max_value=100, max_digits=5, decimal_places=2)
    default_labour_rate = forms.DecimalField(min_value=0, max_digits=12, decimal_places=2)
    class Meta:
        model = ShopSettings
        fields = ["shop_name", "address", "phone", "email", "tax_id", "currency_symbol",
                  "tax_name", "tax_rate", "prices_include_tax", "counter_max_discount",
                  "default_labour_rate", "invoice_footer"]
        widgets = {"address": forms.Textarea(attrs={"rows": 2})}


class ShopSettingsView(RoleRequiredMixin, UpdateView):
    allowed_roles = OWNER_ONLY
    form_class = ShopSettingsForm
    template_name = "form.html"
    success_url = "/settings/"
    extra_context = {"page_title": "Shop settings", "cancel_url": "/", "submit_label": "Save settings"}

    def get_object(self, queryset=None):
        return ShopSettings.get(self.request.tenant)


@role_required()
@require_POST
def switch_branch(request):
    branch = allowed_branches(request.user).filter(pk=optional_id(request.POST.get("branch_id"))).first()
    if branch is None:
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied("Branch is not assigned to this account.")
    previous = request.branch.pk
    request.session["branch_id"] = branch.pk
    from .models import audit
    audit(request.user, 'branch.switch', branch.code, 'Active branch changed', branch=branch,
          changes={'branch_id': {'before': previous, 'after': branch.pk}}, kind='activity')
    return redirect("dashboard")


class BranchForm(forms.ModelForm):
    class Meta:
        model = Branch
        fields = ["name", "code", "is_active"]


@role_required(OWNER_ONLY)
def branches(request):
    form = BranchForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        branch = form.save(commit=False)
        branch.tenant = request.tenant
        if Branch.objects.filter(tenant=request.tenant, code=branch.code).exists():
            form.add_error("code", "Code already exists for this business.")
        else:
            branch.save()
            BranchStock.objects.bulk_create(
                [BranchStock(branch=branch, part=p, cost_price=p.cost_price)
                 for p in Part.objects.filter(tenant=request.tenant)],
                ignore_conflicts=True)
            messages.success(request, "Branch created.")
            return redirect("branches")
    return render(request, "core/branches.html", {"page_title": "Branches", "form": form,
            "all_branches": Branch.objects.filter(tenant=request.tenant)})


@role_required(OWNER_ONLY)
def staff(request):
    User = get_user_model()
    class StaffForm(forms.Form):
        username = forms.CharField(max_length=150)
        first_name = forms.CharField(max_length=150, required=False)
        last_name = forms.CharField(max_length=150, required=False)
        password = forms.CharField(widget=forms.PasswordInput, min_length=12)
        role = forms.ChoiceField(choices=[c for c in User.Role.choices if c[0] != User.Role.OWNER])
        branches = forms.ModelMultipleChoiceField(queryset=Branch.objects.filter(tenant=request.tenant, is_active=True))

    form = StaffForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        if User.objects.filter(username=data["username"]).exists():
            form.add_error("username", "Username is already taken.")
        else:
            user = User.objects.create_user(username=data["username"], password=data["password"],
                first_name=data["first_name"], last_name=data["last_name"], role=data["role"], tenant=request.tenant)
            user.branches.set(data["branches"])
            messages.success(request, "Staff account created.")
            return redirect("staff")
    return render(request, "core/staff.html", {"page_title": "Staff", "form": form,
            "staff_members": User.objects.filter(tenant=request.tenant).prefetch_related("branches")})


@role_required()
@require_POST
def log_print_request(request):
    """Record a browser print request after checking access to the source page."""
    from django.http import JsonResponse
    from django.urls import resolve, Resolver404
    from django.core.exceptions import PermissionDenied
    from django.shortcuts import get_object_or_404
    from core.permissions import STOCK
    from stock.models import PurchaseOrder
    from .models import audit
    path = request.POST.get('path', '')
    if not path.startswith('/') or path.startswith('//') or len(path) > 500 or '?' in path:
        return JsonResponse({'ok': False}, status=400)
    try: match = resolve(path)
    except Resolver404: return JsonResponse({'ok': False}, status=400)
    name = match.url_name
    if name == 'sale_detail':
        if request.user.role not in SALES: raise PermissionDenied
        sale = get_object_or_404(Sale, pk=match.kwargs['pk'], branch=request.branch)
        if request.user.role == 'counter' and sale.created_by_id != request.user.pk: raise PermissionDenied
        target = sale.number
    elif name == 'job_detail':
        if request.user.role not in FRONT: raise PermissionDenied
        target = get_object_or_404(JobCard, pk=match.kwargs['pk'], branch=request.branch).number
    elif name == 'po_detail':
        if request.user.role not in STOCK: raise PermissionDenied
        target = get_object_or_404(PurchaseOrder, pk=match.kwargs['pk'], branch=request.branch).number
    elif name in ('report_sales','report_top','report_low_stock','report_slow','report_stock_value','report_receivables'):
        if request.user.role not in MGMT: raise PermissionDenied
        target = name
    else:
        return JsonResponse({'ok': False}, status=400)
    audit(request.user, 'print.request', target, 'Browser requested print preview; physical printing is not confirmed.', branch=request.branch, kind='activity')
    return JsonResponse({'ok': True})
