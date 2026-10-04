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
