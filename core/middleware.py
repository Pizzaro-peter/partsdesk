from .tenancy import active_branch
from .models import Branch, User


class ActiveBranchMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.tenant = None
        request.branch = None
        if request.user.is_authenticated and request.user.is_active:
            request.tenant = request.user.tenant
            request.branch = active_branch(request)
            if (
                request.user.role == User.Role.OWNER
                and request.tenant
                and not Branch.objects.filter(tenant=request.tenant).exists()
                and request.path_info not in ("/onboarding/branch/", "/accounts/logout/")
            ):
                from django.shortcuts import redirect

                return redirect("branch_onboarding")
        return self.get_response(request)
