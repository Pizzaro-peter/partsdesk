from .tenancy import active_branch


class ActiveBranchMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.tenant = None
        request.branch = None
        if request.user.is_authenticated and request.user.is_active:
            request.tenant = request.user.tenant
            request.branch = active_branch(request)
        return self.get_response(request)
