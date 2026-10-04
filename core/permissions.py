"""Role groups and helpers. One place to read to understand who can do what."""
from functools import wraps

from django.contrib.auth.mixins import UserPassesTestMixin
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from .tenancy import require_branch

OWNER, MANAGER, COUNTER, MECHANIC, STOREKEEPER = (
    "owner", "manager", "counter", "mechanic", "storekeeper",
)
MGMT = (OWNER, MANAGER)                       # reports, returns, price overrides
SALES = (OWNER, MANAGER, COUNTER)             # point of sale, invoices
STOCK = (OWNER, MANAGER, STOREKEEPER)         # catalog edits, purchasing, adjustments
FRONT = (OWNER, MANAGER, COUNTER, MECHANIC)   # customers and job cards
OWNER_ONLY = (OWNER,)


def role_required(roles=None):
    """Function-view decorator. roles=None means any signed-in user."""
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            require_branch(request)
            if roles is not None and request.user.role not in roles:
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return wrapper
    return decorator


class RoleRequiredMixin(UserPassesTestMixin):
    allowed_roles = None  # None = any signed-in user

    def test_func(self):
        user = self.request.user
        return user.is_authenticated and self.request.branch is not None and (
            self.allowed_roles is None or user.role in self.allowed_roles
        )
