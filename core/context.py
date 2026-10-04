from django.urls import reverse

from .models import ShopSettings
from .tenancy import allowed_branches
from .permissions import FRONT, MGMT, OWNER_ONLY, SALES, STOCK

NAV = [
    (None, [("Dashboard", "dashboard", None)]),
    ("Sales", [
        ("Point of Sale", "pos", SALES),
        ("Invoices", "sale_list", SALES),
        ("Customers", "customer_list", FRONT),
    ]),
    ("Workshop", [("Job Cards", "job_list", FRONT)]),
    ("Inventory", [
        ("Parts Catalogue", "part_list", None),
        ("Categories", "category_list", STOCK),
        ("Vehicle Fitment", "vehicle_list", None),
        ("Stock Ledger", "movement_list", STOCK),
        ("Stock Adjustments", "stock_adjust", STOCK),
    ]),
    ("Procurement", [
        ("Purchase Orders", "po_list", STOCK),
        ("Suppliers", "supplier_list", STOCK),
    ]),
    ("Insights", [
        ("Reports", "reports_home", MGMT),
        ("Audit Trail", "audit_list", MGMT),
    ]),
    ("Administration", [
        ("Business Settings", "shop_settings", OWNER_ONLY),
        ("Branches", "branches", OWNER_ONLY),
        ("Staff & Access", "staff", OWNER_ONLY),
    ]),
]


def shop(request):
    if not request.user.is_authenticated:
        return {}
    if request.branch is None:
        return {"shop": None, "nav": [], "branches": [], "active_branch": None}
    groups, best = [], None
    for title, entries in NAV:
        items = []
        for label, name, roles in entries:
            if roles is None or request.user.role in roles:
                url = reverse(name)
                item = {"label": label, "url": url, "active": False}
                if (request.path == url if url == "/" else request.path.startswith(url)) and (
                        best is None or len(url) > len(best["url"])):
                    best = item
                items.append(item)
        if items:
            groups.append({"title": title, "items": items})
    if best:
        best["active"] = True
    for g in groups:
        g["open"] = any(i["active"] for i in g["items"])
    return {"shop": ShopSettings.get(request.tenant), "nav": groups,
            "branches": allowed_branches(request.user), "active_branch": request.branch}
