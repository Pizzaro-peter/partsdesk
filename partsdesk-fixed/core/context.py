from django.urls import reverse

from .models import ShopSettings
from .tenancy import allowed_branches
from .permissions import FRONT, MGMT, OWNER_ONLY, SALES, STOCK

NAV = [
    ("Dashboard", "dashboard", None),
    ("Point of sale", "pos", SALES),
    ("Invoices", "sale_list", SALES),
    ("Job cards", "job_list", FRONT),
    ("Parts", "part_list", None),
    ("Customers", "customer_list", FRONT),
    ("Purchasing", "po_list", STOCK),
    ("Stock ledger", "movement_list", STOCK),
    ("Reports", "reports_home", MGMT),
    ("Audit log", "audit_list", MGMT),
    ("Settings", "shop_settings", OWNER_ONLY),
    ("Branches", "branches", OWNER_ONLY),
    ("Staff", "staff", OWNER_ONLY),
]


def shop(request):
    if not request.user.is_authenticated:
        return {}
    if request.branch is None:
        return {"shop": None, "nav": [], "branches": [], "active_branch": None}
    items = []
    for label, name, roles in NAV:
        if roles is None or request.user.role in roles:
            url = reverse(name)
            active = request.path == url if url == "/" else request.path.startswith(url)
            items.append({"label": label, "url": url, "active": active})
    return {"shop": ShopSettings.get(request.tenant), "nav": items,
            "branches": allowed_branches(request.user), "active_branch": request.branch}
