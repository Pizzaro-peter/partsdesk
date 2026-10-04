from django.core.exceptions import PermissionDenied

from .models import Branch, User


def allowed_branches(user):
    if not user.is_authenticated or not user.is_active or not user.tenant_id:
        return Branch.objects.none()
    qs = Branch.objects.filter(tenant_id=user.tenant_id, tenant__is_active=True, is_active=True)
    if user.role != User.Role.OWNER:
        qs = qs.filter(staff=user)
    return qs


def active_branch(request):
    """Fail closed if a saved branch is no longer allowed."""
    qs = allowed_branches(request.user)
    branch = qs.filter(pk=request.session.get("branch_id")).first()
    if branch is None:
        branch = qs.first()
        if branch:
            request.session["branch_id"] = branch.pk
        else:
            request.session.pop("branch_id", None)
    return branch


def require_branch(request):
    if request.branch is None:
        raise PermissionDenied("No active branch is assigned to your account.")
    return request.branch


def check_staff_branch(user, branch):
    if not allowed_branches(user).filter(pk=branch.pk).exists():
        raise PermissionDenied("You are not assigned to this branch.")


def same_tenant(tenant, *objects):
    if any(obj is not None and getattr(obj, "tenant_id", None) != tenant.pk for obj in objects):
        raise PermissionDenied("A selected record is outside this business.")


def same_branch(branch, *objects):
    if any(obj is not None and getattr(obj, "branch_id", None) != branch.pk for obj in objects):
        raise PermissionDenied("A selected record is outside this branch.")
