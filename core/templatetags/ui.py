from django import template

from core.utils import money_str, qty_str

register = template.Library()

STATUS_CLASSES = {
    "open": "info", "diagnosing": "info", "awaiting_parts": "warn", "in_progress": "accent",
    "ready": "ok", "invoiced": "muted", "cancelled": "bad",
    "draft": "muted", "ordered": "info", "partial": "warn", "received": "ok",
    "completed": "ok", "part_returned": "warn", "returned": "bad",
}


@register.simple_tag(takes_context=True)
def money(context, value):
    shop = context.get("shop")
    return money_str(value, shop.currency_symbol if shop else "")


@register.filter
def qty(value):
    return qty_str(value) if value is not None else ""


@register.filter
def status_class(value):
    return "b-" + STATUS_CLASSES.get(str(value), "muted")


@register.simple_tag(takes_context=True)
def url_replace(context, **kwargs):
    query = context["request"].GET.copy()
    for key, value in kwargs.items():
        query[key] = value
    return query.urlencode()


@register.simple_tag(takes_context=True)
def page_back_link(context):
    from core.navigation import page_back_link as destination
    return destination(context.get("request"))


# Onboarding-style steps for data-collection forms: form class -> [(title, hint, [field names])].
FORM_STEPS = {
    "CustomerForm": [
        ("Who is the customer?", "Basic identity and how to reach them.", ["name", "phone", "email", "address"]),
        ("Account type & credit", "Decides which prices apply and whether they can buy on account.", ["customer_type", "credit_limit"]),
        ("Review & notes", "Anything staff should know before serving them.", ["notes", "is_active"]),
    ],
    "SupplierForm": [
        ("Supplier details", "Who you buy from.", ["name", "contact_person"]),
        ("Contact information", "How purchase orders will reach them.", ["phone", "email", "address"]),
        ("Terms & notes", "Payment terms agreed with this supplier.", ["payment_terms", "notes", "is_active"]),
    ],
    "PartForm": [
        ("Identification", "What the part is called and how it is found.", ["name", "sku", "barcode", "oem_number", "category", "brand"]),
        ("Classification", "Type, condition and how it is stocked.", ["part_type", "condition", "unit", "is_universal", "description"]),
        ("Pricing", "Cost and selling prices.", ["cost_price", "sell_price", "trade_price"]),
        ("Stock & sourcing", "Branch location, reorder rules and preferred supplier.", ["bin_location", "reorder_level", "reorder_qty", "preferred_supplier", "is_active"]),
    ],
    "ShopSettingsForm": [
        ("Business profile", "Printed on invoices and receipts.", ["shop_name", "address", "phone", "email", "tax_id"]),
        ("Tax & currency", "How prices and tax are calculated.", ["currency_symbol", "tax_name", "tax_rate", "prices_include_tax"]),
        ("Sales policy", "Limits and defaults for staff.", ["counter_max_discount", "default_labour_rate", "invoice_footer"]),
    ],
    "JobCreateForm": [
        ("Customer & vehicle", "Who is bringing the vehicle in.", ["customer", "vehicle"]),
        ("Work requested", "What the customer wants done.", ["complaint", "odometer"]),
        ("Scheduling", "Who will do it and when it is promised.", ["assigned_to", "promised_date"]),
    ],
    "POForm": [
        ("Supplier", "Who the order is for.", ["supplier"]),
        ("Delivery & notes", "When you expect it and any instructions.", ["expected_date", "notes"]),
    ],
}


@register.simple_tag
def form_steps(form):
    """Group a form's visible fields into onboarding steps. Unknown forms become a single untitled step."""
    visible = {f.name: f for f in form.visible_fields()}
    spec = FORM_STEPS.get(type(form).__name__)
    if not spec:
        for base in type(form).__mro__[1:]:
            spec = FORM_STEPS.get(base.__name__)
            if spec:
                break
    if not spec:
        return [{"title": "", "hint": "", "fields": list(visible.values()), "has_errors": bool(form.errors)}]
    steps, used = [], set()
    for title, hint, names in spec:
        fields = [visible[n] for n in names if n in visible]
        used.update(n for n in names if n in visible)
        if fields:
            steps.append({"title": title, "hint": hint, "fields": fields})
    rest = [f for n, f in visible.items() if n not in used]
    if rest:
        steps.append({"title": "Additional details", "hint": "", "fields": rest})
    for st in steps:
        st["has_errors"] = any(f.errors for f in st["fields"])
    return steps
