from datetime import date, timedelta
from decimal import Decimal
import hashlib
import hmac
import uuid

from django.contrib.auth import login
from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Count, DecimalField, F, Max, Min, Q, Sum
from django.shortcuts import render
from django.utils import timezone
from django.utils.text import slugify
from django.views.generic import UpdateView
from django.core.paginator import Paginator
from django import forms
from django.contrib.auth import get_user_model
from django.contrib import messages
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.decorators.http import require_http_methods

from catalog.models import BranchStock, Part
from core.models import AuditLog, Branch, ShopSettings, Tenant, User
from core.permissions import (
    FRONT, MGMT, MAX_TENANT_BRANCHES, OWNER_ONLY, SALES, RoleRequiredMixin, role_required,
)
from core.tenancy import allowed_branches
from core.utils import csv_response, money_str, optional_id, qty_str
from sales.models import Customer, Sale, SaleLine
from sales.reporting import daily_figures, line_figures, part_figures
from stock.models import PurchaseOrder, StockMovement
from workshop.models import JobCard

ZERO = Decimal("0")
DEC = DecimalField(max_digits=16, decimal_places=2)
SIGNUP_LIMIT = 10
SIGNUP_WINDOW_SECONDS = 60 * 60


class SignupForm(forms.Form):
    business_name = forms.CharField(max_length=120)
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150)
    username = forms.CharField(max_length=150)
    email = forms.EmailField(label="Email address (optional)", required=False)
    password1 = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput,
        help_text="Use at least 12 characters; avoid common passwords and personal details.",
    )
    password2 = forms.CharField(
        label="Confirm password", strip=False, widget=forms.PasswordInput,
    )
    website = forms.CharField(required=False, widget=forms.HiddenInput)

    def clean_username(self):
        UserModel = get_user_model()
        username = UserModel._meta.get_field("username").clean(
            self.cleaned_data["username"], None
        )
        if UserModel.objects.filter(username__iexact=username).exists():
            raise ValidationError("That username is already in use.")
        return username

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("website"):
            raise ValidationError("Unable to process this signup.")
        password = cleaned.get("password1")
        if password and password != cleaned.get("password2"):
            self.add_error("password2", "The passwords do not match.")
        elif password and cleaned.get("username"):
            UserModel = get_user_model()
            candidate = UserModel(
                username=cleaned["username"],
                first_name=cleaned.get("first_name", ""),
                last_name=cleaned.get("last_name", ""),
                email=cleaned.get("email", ""),
            )
            try:
                validate_password(password, user=candidate)
            except ValidationError as error:
                self.add_error("password1", error)
        return cleaned


class FirstBranchForm(forms.Form):
    name = forms.CharField(max_length=120, initial="Main branch")
    code = forms.RegexField(
        regex=r"^[A-Za-z0-9-]+$",
        max_length=16,
        initial="MAIN",
        help_text="Use letters, numbers, or hyphens.",
    )

    def clean_code(self):
        return self.cleaned_data["code"].upper()


def _signup_rate_limited(request):
    remote_address = request.META.get("REMOTE_ADDR", "")
    key = hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        remote_address.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    cache_key = f"partsdesk:signup:{key}"
    if cache.add(cache_key, 1, timeout=SIGNUP_WINDOW_SECONDS):
        attempts = 1
    else:
        try:
            attempts = cache.incr(cache_key)
        except ValueError:
            cache.add(cache_key, 1, timeout=SIGNUP_WINDOW_SECONDS)
            attempts = cache.get(cache_key, 1)
    return attempts > SIGNUP_LIMIT


@require_http_methods(["GET", "POST"])
def signup(request):
    if request.user.is_authenticated:
        if request.user.tenant_id and not Branch.objects.filter(tenant_id=request.user.tenant_id).exists():
            return redirect("branch_onboarding")
        return redirect("dashboard")

    limited = request.method == "POST" and _signup_rate_limited(request)
    form = SignupForm(request.POST if request.method == "POST" else None)
    if limited:
        form.full_clean()
        form.add_error(None, "Too many signup attempts. Please try again in an hour.")
    elif request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            with transaction.atomic():
                slug = f"{slugify(data['business_name'])[:110] or 'business'}-{uuid.uuid4().hex[:10]}"
                tenant = Tenant.objects.create(name=data["business_name"], slug=slug)
                ShopSettings.objects.create(tenant=tenant, shop_name=data["business_name"])
                user = get_user_model().objects.create_user(
                    username=data["username"],
                    password=data["password1"],
                    first_name=data["first_name"],
                    last_name=data["last_name"],
                    email=data["email"],
                    role=User.Role.OWNER,
                    tenant=tenant,
                )
        except IntegrityError:
            if not get_user_model().objects.filter(username__iexact=data["username"]).exists():
                raise
            form.add_error("username", "That username is already in use. Please choose another.")
            return render(request, "registration/signup.html", {"form": form})
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        return redirect("branch_onboarding")
    return render(
        request, "registration/signup.html", {"form": form},
        status=429 if limited else 200,
    )


@require_http_methods(["GET", "POST"])
def branch_onboarding(request):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('two_factor:login')}?next={reverse('branch_onboarding')}")
    if request.user.role != User.Role.OWNER or not request.user.tenant_id:
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied("Only a business owner can complete initial branch setup.")
    existing_branch = Branch.objects.filter(tenant_id=request.user.tenant_id).first()
    if existing_branch:
        if existing_branch.is_active:
            request.session["branch_id"] = existing_branch.pk
            return redirect("dashboard")
        form = FirstBranchForm(request.POST if request.method == "POST" else None)
        form.add_error(None, "Your existing branches are inactive. Ask an administrator to reactivate one.")
        return render(request, "registration/branch_onboarding.html", {"form": form})

    form = FirstBranchForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            tenant = Tenant.objects.select_for_update().get(pk=request.user.tenant_id)
            if Branch.objects.filter(tenant=tenant).exists():
                return redirect("branch_onboarding")
            branch = Branch.objects.create(
                tenant=tenant,
                name=form.cleaned_data["name"],
                code=form.cleaned_data["code"],
            )
            request.user.branches.add(branch)
        request.session["branch_id"] = branch.pk
        return redirect("dashboard")
    return render(request, "registration/branch_onboarding.html", {"form": form})
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


def _report(request, title, columns, rows, *, csv_name, totals=None, dates=None, note="", post_action=None,
            extra_filter=None, metrics=None, chart=None, previous_period=None, ranked=None):
    """columns: [(label, kind)] with kind in text|money|qty. rows: raw values."""
    if request.GET.get("csv"):
        return csv_response(csv_name, [c[0] for c in columns], rows)
    sym = ShopSettings.get(request.tenant).currency_symbol

    def fmt(v, kind):
        if kind == "money":
            return money_str(v, sym)
        if kind == "qty":
            return qty_str(v)
        if kind == "percent":
            return f"{Decimal(v):.1f}%"
        return "" if v is None else str(v)

    def build(row):
        return [dict(v=fmt(v, k), num=k != "text") for v, (_, k) in zip(row, columns)]

    return render(request, "core/report.html", {
        "page_title": title, "columns": [dict(label=l, num=k != "text") for l, k in columns],
        "rows": [build(r) for r in rows], "totals": build(totals) if totals else None,
        "dates": dates, "note": note, "post_action": post_action, "extra_filter": extra_filter, "empty": not rows,
        "metrics": metrics, "chart": chart, "previous_period": previous_period, "ranked": ranked,
    })


def _period_totals(days):
    totals = {
        "invoices": sum(day["invoices"] for day in days),
        "revenue": sum((day["revenue"] for day in days), ZERO),
        "tax": sum((day["tax"] for day in days), ZERO),
        "cost": sum((day["cost"] for day in days), ZERO),
        "profit": sum((day["profit"] for day in days), ZERO),
    }
    totals["average"] = totals["revenue"] / totals["invoices"] if totals["invoices"] else ZERO
    totals["margin"] = totals["profit"] * 100 / totals["revenue"] if totals["revenue"] else ZERO
    return totals


def _comparison(current, previous):
    if previous == ZERO:
        text = "No change vs previous period" if current == ZERO else "New vs previous period"
        return {"text": text, "direction": "flat" if current == ZERO else "up"}
    change = (current - previous) * 100 / abs(previous)
    direction = "up" if change > 0 else "down" if change < 0 else "flat"
    return {
        "text": f"{abs(change):.1f}% {'higher' if direction == 'up' else 'lower' if direction == 'down' else 'change'} vs previous period",
        "direction": direction,
    }


def _sales_metrics(current, previous):
    definitions = [
        ("Net sales", "revenue", "money"),
        ("Gross profit", "profit", "money"),
        ("Invoices", "invoices", "number"),
        ("Average sale", "average", "money"),
        ("Gross margin", "margin", "percent"),
    ]
    return [
        {
            "label": label,
            "value": current[key],
            "kind": kind,
            "comparison": _comparison(current[key], previous[key]),
        }
        for label, key, kind in definitions
    ]


def _sales_chart(days):
    """Bucket long date ranges into readable weeks/months and scale bars to revenue."""
    span = len(days)
    buckets = {}
    for day in days:
        current = day["date"]
        if span > 120:
            key = current.replace(day=1)
            label = current.strftime("%b %Y")
        elif span > 31:
            key = current - timedelta(days=current.weekday())
            label = f"Week of {key.strftime('%d %b')}"
        else:
            key = current
            label = current.strftime("%d %b")
        bucket = buckets.setdefault(key, {"label": label, "revenue": ZERO, "profit": ZERO, "invoices": 0})
        bucket["revenue"] += day["revenue"]
        bucket["profit"] += day["profit"]
        bucket["invoices"] += day["invoices"]
    peak = max((bucket["revenue"] for bucket in buckets.values()), default=ZERO)
    result = []
    for bucket in buckets.values():
        bucket["revenue_pct"] = int(bucket["revenue"] * 100 / peak) if peak else 0
        result.append(bucket)
    return result


@role_required(MGMT)
def reports_home(request):
    start, end = _range(request)
    today_rows = list(daily_figures(start, end, request.branch).values())
    duration = (end - start).days + 1
    previous_end = start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=duration - 1)
    previous_rows = list(daily_figures(previous_start, previous_end, request.branch).values())
    current, previous = _period_totals(today_rows), _period_totals(previous_rows)
    query = f"start={start.isoformat()}&end={end.isoformat()}"
    currency_symbol = ShopSettings.get(request.tenant).currency_symbol

    def money(value):
        return money_str(value, currency_symbol)

    stock = BranchStock.objects.filter(branch=request.branch, part__is_active=True)
    stock_value = stock.aggregate(
        cost=Sum(F("quantity_on_hand") * F("cost_price"), output_field=DEC),
        retail=Sum(F("quantity_on_hand") * F("part__sell_price"), output_field=DEC),
    )
    low_count = _low_stock_qs(request.branch).count()
    slow_cutoff = timezone.now() - timedelta(days=90)
    slow_parts = BranchStock.objects.filter(
        branch=request.branch, part__is_active=True, quantity_on_hand__gt=0,
        part__created_at__lt=slow_cutoff,
    ).annotate(last_sale=Max(
        "part__movements__created_at",
        filter=Q(part__movements__reason="sale", part__movements__branch=request.branch),
    )).filter(Q(last_sale__lt=slow_cutoff) | Q(last_sale__isnull=True))
    slow_value = sum(
        (row["quantity_on_hand"] * row["cost_price"]
         for row in slow_parts.order_by().values("pk", "quantity_on_hand", "cost_price").distinct()),
        ZERO,
    )
    owing = Customer.objects.filter(
        tenant=request.tenant, sales__branch=request.branch,
    ).annotate(due=Sum(
        F("sales__total") - F("sales__returned_total") - F("sales__amount_paid"),
    )).filter(due__gt=0)
    owing_total = owing.aggregate(value=Sum("due"))["value"] or ZERO
    top_parts = [part for part in part_figures(start, end, request.branch) if part["qty"] > ZERO]
    cards = [
        {"title": "Sales & profit", "url": "report_sales", "query": query,
         "blurb": "Revenue, tax, cost of parts and gross profit.",
         "metric": f"{money(current['revenue'])} net sales", "tone": "sales"},
        {"title": "Sales by payment type", "url": "report_payment_methods", "query": query,
         "blurb": "Compare sales grouped by the checkout payment type.",
         "metric": "Includes on-account sales", "tone": "sales"},
        {"title": "Branch comparison", "url": "report_branches", "query": query,
         "blurb": "Compare revenue, gross profit and margin across your branches.",
         "metric": f"{allowed_branches(request.user).count()} accessible branches", "tone": "products"},
        {"title": "Top sellers", "url": "report_top", "query": query,
         "blurb": "The parts earning the most revenue and gross profit.",
         "metric": f"{len(top_parts)} parts sold", "tone": "products"},
        {"title": "Low stock & reorder", "url": "report_low_stock", "query": "",
         "blurb": "Parts at or below their reorder point.",
         "metric": f"{low_count} part{'s' if low_count != 1 else ''} need attention", "tone": "stock"},
        {"title": "Slow movers", "url": "report_slow", "query": "",
         "blurb": "Stock sitting without a sale in 90 days.",
         "metric": f"{money(slow_value)} tied up at cost", "tone": "slow"},
        {"title": "Stock value", "url": "report_stock_value", "query": "",
         "blurb": "Current inventory value at cost and retail.",
         "metric": f"{money(stock_value['cost'] or ZERO)} at cost", "tone": "value"},
        {"title": "Stock receipt age", "url": "report_stock_age", "query": "",
         "blurb": "Current stock grouped by days since its last recorded receipt.",
         "metric": "Receipt recency, not FIFO stock age", "tone": "slow"},
        {"title": "Customers who owe", "url": "report_receivables", "query": "",
         "blurb": "Outstanding balances grouped by invoice age.",
         "metric": f"{money(owing_total)} outstanding", "tone": "owing"},
        {"title": "Supplier lead time", "url": "report_supplier_lead_time", "query": query,
         "blurb": "Average days from purchase order date to first receipt.",
         "metric": "Based on recorded stock receipts", "tone": "value"},
    ]
    return render(request, "core/reports_home.html", {
        "page_title": "Reports & analytics",
        "cards": cards,
        "dates": (start, end),
        "range_days": duration,
        "summary": _sales_metrics(current, previous),
        "sales_chart": _sales_chart(today_rows) if current["invoices"] else [],
        "has_sales": bool(current["invoices"]),
        "current_period": f"{start:%d %b %Y} – {end:%d %b %Y}",
        "previous_period": f"{previous_start:%d %b} – {previous_end:%d %b %Y}",
    })


@role_required(MGMT)
def report_sales(request):
    start, end = _range(request)
    days = list(daily_figures(start, end, request.branch).values())
    duration = (end - start).days + 1
    previous_end = start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=duration - 1)
    previous = _period_totals(list(daily_figures(previous_start, previous_end, request.branch).values()))
    current = _period_totals(days)
    metrics = _sales_metrics(current, previous)
    rows = [[r["date"], r["invoices"], r["revenue"], r["tax"], r["cost"], r["profit"]] for r in days]
    totals = ["Total", sum(r[1] for r in rows)] + [sum(r[i] for r in rows) for i in range(2, 6)]
    cols = [("Date", "text"), ("Invoices", "text"), ("Revenue (excl. tax)", "money"), ("Tax", "money"),
            ("Cost of parts", "money"), ("Gross profit", "money")]
    return _report(request, "Sales & profit by day", cols, [r for r in rows if r[1]], totals=totals,
                   dates=(start, end), csv_name="sales.csv", note="Returned items are excluded.",
                   metrics=metrics, chart=_sales_chart(days) if current["invoices"] else [],
                   previous_period=f"{previous_start:%d %b %Y} – {previous_end:%d %b %Y}")


@role_required(MGMT)
def report_top(request):
    start, end = _range(request)
    data = [item for item in part_figures(start, end, request.branch) if item["qty"] > ZERO][:50]
    rows = [[d["part"].sku, d["part"].name, d["qty"], d["revenue"], d["profit"]] for d in data]
    peak = max((item["revenue"] for item in data), default=ZERO)
    ranked = [
        {
            "label": f"{item['part'].sku} · {item['part'].name}",
            "value": item["revenue"],
            "profit": item["profit"],
            "pct": int(item["revenue"] * 100 / peak) if peak else 0,
        }
        for item in data[:10]
    ]
    cols = [("Code", "text"), ("Part", "text"), ("Qty sold", "qty"), ("Revenue (excl. tax)", "money"), ("Gross profit", "money")]
    return _report(request, "Top sellers", cols, rows, dates=(start, end), csv_name="top-sellers.csv",
                   note="Top 50 by revenue. Gross profit is revenue after returns, less recorded cost of goods.",
                   ranked=ranked)


@role_required(MGMT)
def report_payment_methods(request):
    start, end = _range(request)
    sales = Sale.objects.filter(branch=request.branch, created_at__date__range=(start, end))
    method_data = {
        method: {"invoices": 0, "revenue": ZERO, "profit": ZERO}
        for method, _ in Sale.Method.choices
    }
    method_labels = dict(Sale.Method.choices)
    for sale_id, method in sales.values_list("pk", "payment_method"):
        method_data[method]["invoices"] += 1
    for line in SaleLine.objects.filter(
        sale__branch=request.branch, sale__created_at__date__range=(start, end),
    ).select_related("sale"):
        revenue, _, cost = line_figures(line)
        values = method_data[line.sale.payment_method]
        values["revenue"] += revenue
        values["profit"] += revenue - cost
    rows = [
        [method_labels[method], values["invoices"], values["revenue"], values["profit"]]
        for method, values in method_data.items() if values["invoices"]
    ]
    totals = ["Total", sum(row[1] for row in rows), sum((row[2] for row in rows), ZERO),
              sum((row[3] for row in rows), ZERO)]
    cols = [("Checkout payment type", "text"), ("Invoices", "text"),
            ("Net sales (excl. tax)", "money"), ("Gross profit", "money")]
    return _report(
        request, "Sales by payment type", cols, rows, totals=totals if rows else None,
        dates=(start, end), csv_name="sales-by-payment-type.csv",
        note="Each invoice is grouped by its recorded checkout type. On-account invoices are not cash collections.",
    )


@role_required(MGMT)
def report_branches(request):
    start, end = _range(request)
    rows = []
    for branch in allowed_branches(request.user).order_by("name"):
        totals = _period_totals(list(daily_figures(start, end, branch).values()))
        rows.append([
            branch.name, totals["invoices"], totals["revenue"], totals["profit"], totals["margin"],
        ])
    totals = [
        "Total", sum(row[1] for row in rows), sum((row[2] for row in rows), ZERO),
        sum((row[3] for row in rows), ZERO),
        (sum((row[3] for row in rows), ZERO) * 100 / sum((row[2] for row in rows), ZERO)
         if sum((row[2] for row in rows), ZERO) else ZERO),
    ]
    cols = [("Branch", "text"), ("Invoices", "text"), ("Net sales (excl. tax)", "money"),
            ("Gross profit", "money"), ("Gross margin", "percent")]
    return _report(
        request, "Branch comparison", cols, rows, totals=totals, dates=(start, end),
        csv_name="branch-comparison.csv",
        note="Only branches you are allowed to access are included. Gross profit excludes operating expenses.",
    )


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
def report_stock_age(request):
    stocks = (BranchStock.objects.filter(
        branch=request.branch, part__is_active=True, quantity_on_hand__gt=0,
    ).select_related("part", "part__preferred_supplier").annotate(
        last_receipt=Max(
            "part__movements__created_at",
            filter=Q(
                part__movements__reason=StockMovement.Reason.RECEIPT,
                part__movements__branch=request.branch,
            ),
        ),
    ))
    today = timezone.localdate()
    rows = []
    for stock in stocks:
        value = stock.quantity_on_hand * stock.cost_price
        if stock.last_receipt:
            receipt_date = timezone.localtime(stock.last_receipt).date()
            age_days = max((today - receipt_date).days, 0)
            age_bucket = (
                "0–30 days" if age_days <= 30 else
                "31–60 days" if age_days <= 60 else
                "61–90 days" if age_days <= 90 else "Over 90 days"
            )
            receipt_display = receipt_date.isoformat()
        else:
            age_days, age_bucket, receipt_display = None, "No receipt recorded", "—"
        rows.append([
            stock.part.sku, stock.part.name, stock.part.preferred_supplier.name if stock.part.preferred_supplier else "—",
            stock.quantity_on_hand, value, receipt_display, age_days, age_bucket,
        ])
    rows.sort(key=lambda row: (row[6] is None, -(row[6] or 0), row[1]))
    totals = ["Total", "", "", sum((row[3] for row in rows), ZERO),
              sum((row[4] for row in rows), ZERO), "", "", ""]
    cols = [
        ("Code", "text"), ("Part", "text"), ("Preferred supplier", "text"), ("On hand", "qty"),
        ("Value at cost", "money"), ("Last receipt", "text"), ("Days since receipt", "text"), ("Receipt age", "text"),
    ]
    return _report(
        request, "Stock receipt age", cols, rows, totals=totals if rows else None,
        csv_name="stock-receipt-age.csv",
        note="Current stock is grouped by the latest recorded receipt for each part. This is a recency indicator, not FIFO/lot-level age; opening stock and receipts not recorded in the ledger have no receipt date.",
    )


@role_required(MGMT)
def report_receivables(request):
    today = timezone.localdate()
    buckets = ("0–30 days", "31–60 days", "61–90 days", "Over 90 days")
    balances = {}
    owing = Sale.objects.filter(branch=request.branch).filter(
        total__gt=F("returned_total") + F("amount_paid"),
    ).select_related("customer").order_by()
    for sale in owing:
        if not sale.customer_id:
            continue
        age_days = max((today - timezone.localtime(sale.created_at).date()).days, 0)
        bucket = 0 if age_days <= 30 else 1 if age_days <= 60 else 2 if age_days <= 90 else 3
        balance = sale.total - sale.returned_total - sale.amount_paid
        account = balances.setdefault(sale.customer_id, {
            "name": sale.customer.name, "phone": sale.customer.phone,
            "credit_limit": sale.customer.credit_limit, "buckets": [ZERO, ZERO, ZERO, ZERO],
        })
        account["buckets"][bucket] += balance
    rows = []
    for account in balances.values():
        amount = sum(account["buckets"], ZERO)
        rows.append([account["name"], account["phone"], account["credit_limit"],
                     *account["buckets"], amount])
    rows.sort(key=lambda row: (-row[-1], row[0]))
    totals = ["Total", "", None] + [
        sum((row[index] for row in rows), ZERO) for index in range(3, 8)
    ]
    cols = [("Customer", "text"), ("Phone", "text"), ("Credit limit", "money"),
            *((bucket, "money") for bucket in buckets), ("Owes", "money")]
    return _report(
        request, "Receivables by invoice age", cols, rows, totals=totals if rows else None,
        csv_name="receivables.csv",
        note="Aging is measured from invoice date because customer-specific due dates are not stored. These age bands are not a contractual overdue assessment.",
    )


@role_required(MGMT)
def report_supplier_lead_time(request):
    start, end = _range(request)
    orders = list(PurchaseOrder.objects.filter(
        branch=request.branch, order_date__range=(start, end),
        status__in=(PurchaseOrder.Status.PARTIAL, PurchaseOrder.Status.RECEIVED),
    ).select_related("supplier").order_by("order_date", "number"))
    first_receipts = dict(
        StockMovement.objects.filter(
            branch=request.branch, reason=StockMovement.Reason.RECEIPT,
            reference__in=[order.number for order in orders],
        ).values("reference").annotate(first_received=Min("created_at")).values_list("reference", "first_received")
    )
    supplier_days = {}
    for order in orders:
        first_received = first_receipts.get(order.number)
        if first_received is None:
            continue
        received_date = timezone.localtime(first_received).date()
        lead_days = max((received_date - order.order_date).days, 0)
        supplier_days.setdefault(order.supplier_id, {
            "supplier": order.supplier.name, "orders": 0, "days": 0,
        })
        supplier_days[order.supplier_id]["orders"] += 1
        supplier_days[order.supplier_id]["days"] += lead_days
    rows = [
        [values["supplier"], values["orders"],
         (Decimal(values["days"]) / values["orders"]).quantize(Decimal("0.1"))]
        for values in sorted(supplier_days.values(), key=lambda value: value["supplier"].casefold())
    ]
    count = sum(row[1] for row in rows)
    total_days = sum((row[2] * row[1] for row in rows), ZERO)
    totals = ["Overall average", count, total_days / count if count else ZERO]
    cols = [("Supplier", "text"), ("Orders with receipts", "text"), ("Average days to first receipt", "text")]
    return _report(
        request, "Supplier lead time", cols, rows, totals=totals if rows else None,
        dates=(start, end), csv_name="supplier-lead-time.csv",
        note="Uses purchase order dates and the first stock receipt recorded against each order in this branch. Partial orders are included; this measures time to first delivery, not time to complete the order.",
    )


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
    success_url = "/"
    extra_context = {"page_title": "Shop settings", "cancel_url": "/", "submit_label": "Save settings"}

    def get_object(self, queryset=None):
        return ShopSettings.get(self.request.tenant)

    def form_valid(self, form):
        messages.success(self.request, "Business settings saved.")
        return super().form_valid(form)


@role_required()
def profile(request):
    user = request.user
    return render(request, "core/profile.html", {
        "page_title": "My profile",
        "tenant": request.tenant,
        "branches": allowed_branches(user),
        "recent": AuditLog.objects.filter(user=user, tenant=request.tenant).select_related("branch")[:15],
    })


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
    all_b = list(Branch.objects.filter(tenant=request.tenant))
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            Tenant.objects.select_for_update().get(pk=request.tenant.pk)
            branch = form.save(commit=False)
            branch.tenant = request.tenant
            if Branch.objects.filter(tenant=request.tenant, code=branch.code).exists():
                form.add_error("code", "Code already exists for this business.")
            elif Branch.objects.filter(tenant=request.tenant).count() >= MAX_TENANT_BRANCHES:
                form.add_error(None, f"A business can have at most {MAX_TENANT_BRANCHES} branches.")
            else:
                branch.save()
                BranchStock.objects.bulk_create(
                    [BranchStock(branch=branch, part=p, cost_price=p.cost_price)
                     for p in Part.objects.filter(tenant=request.tenant)],
                    ignore_conflicts=True)
                messages.success(request, "Branch created.")
                return redirect("branches")
        all_b = list(Branch.objects.filter(tenant=request.tenant))
    return render(request, "core/branches.html", {"page_title": "Branches", "form": form,
            "all_branches": all_b, "active_count": sum(b.is_active for b in all_b),
            "inactive_count": sum(not b.is_active for b in all_b),
            "branch_limit_reached": len(all_b) >= MAX_TENANT_BRANCHES,
            "max_tenant_branches": MAX_TENANT_BRANCHES})


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
    members = list(User.objects.filter(tenant=request.tenant).prefetch_related("branches"))
    return render(request, "core/staff.html", {"page_title": "Staff", "form": form,
            "staff_members": members, "active_count": sum(m.is_active for m in members),
            "unassigned_count": sum(not m.branches.all() for m in members)})


class StaffUpdateForm(forms.Form):
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    role = forms.ChoiceField(choices=[])
    branches = forms.ModelMultipleChoiceField(queryset=Branch.objects.none(), required=False)

    def __init__(self, *args, tenant, member, **kwargs):
        super().__init__(*args, **kwargs)
        User = get_user_model()
        self.member = member
        self.fields["role"].choices = [
            choice for choice in User.Role.choices if choice[0] != User.Role.OWNER
        ]
        assigned = member.branches.filter(tenant=tenant)
        self.fields["branches"].queryset = Branch.objects.filter(tenant=tenant).filter(
            Q(is_active=True) | Q(pk__in=assigned.values("pk"))
        )
        self.initial.update({
            "first_name": member.first_name,
            "last_name": member.last_name,
            "role": member.role,
            "branches": assigned,
        })

    def clean(self):
        cleaned = super().clean()
        selected = cleaned.get("branches")
        if selected is not None:
            current_ids = set(self.member.branches.values_list("pk", flat=True))
            newly_assigned_inactive = [
                branch for branch in selected
                if not branch.is_active and branch.pk not in current_ids
            ]
            if newly_assigned_inactive:
                self.add_error("branches", "You cannot assign an inactive branch.")
        return cleaned


class TemporaryPasswordForm(forms.Form):
    password = forms.CharField(min_length=12, widget=forms.PasswordInput)
    confirm_password = forms.CharField(min_length=12, widget=forms.PasswordInput, label="Confirm temporary password")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("password") and cleaned.get("confirm_password") and cleaned["password"] != cleaned["confirm_password"]:
            self.add_error("confirm_password", "The passwords do not match.")
        return cleaned


@role_required(OWNER_ONLY)
def staff_user(request, user_id):
    User = get_user_model()
    member = get_object_or_404(
        User.objects.filter(tenant=request.tenant).exclude(role=User.Role.OWNER),
        pk=user_id,
    )
    update_form = StaffUpdateForm(tenant=request.tenant, member=member)
    password_form = TemporaryPasswordForm()

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "update":
            update_form = StaffUpdateForm(
                request.POST, tenant=request.tenant, member=member,
            )
            if update_form.is_valid():
                data = update_form.cleaned_data
                member.first_name = data["first_name"]
                member.last_name = data["last_name"]
                member.role = data["role"]
                member.save(update_fields=["first_name", "last_name", "role"])
                member.branches.set(data["branches"])
                messages.success(request, f"Updated access for {member.display_name}.")
                return redirect("staff_user", user_id=member.pk)
        elif action == "reset_password":
            password_form = TemporaryPasswordForm(request.POST)
            if password_form.is_valid():
                try:
                    validate_password(password_form.cleaned_data["password"], user=member)
                except ValidationError as error:
                    password_form.add_error("password", error)
                else:
                    member.set_password(password_form.cleaned_data["password"])
                    member.save(update_fields=["password"])
                    messages.success(
                        request,
                        f"Temporary password set for {member.display_name}. Share it with them securely; "
                        "they can change it from their profile after signing in.",
                    )
                    return redirect("staff_user", user_id=member.pk)
        elif action in ("disable", "enable"):
            member.is_active = action == "enable"
            member.save(update_fields=["is_active"])
            status = "disabled" if action == "disable" else "enabled"
            messages.success(request, f"{member.display_name}'s account is {status}.")
            return redirect("staff_user", user_id=member.pk)
        else:
            messages.error(request, "Choose a valid user management action.")

    return render(request, "core/staff_user.html", {
        "page_title": member.display_name,
        "member": member,
        "update_form": update_form,
        "password_form": password_form,
    })


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
