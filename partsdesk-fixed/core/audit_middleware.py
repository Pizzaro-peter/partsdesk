from time import monotonic
from uuid import uuid4
from django.contrib.messages import get_messages, ERROR
from .audit import audit_context, metadata, write_event
from .models import RequestLog


class AuditMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.audit_request_id = str(uuid4())
        request.audit_change_count = 0
        request.audit_failed = False
        actor = request.user if request.user.is_authenticated else None
        request.audit_actor = actor
        started = monotonic()
        # ActiveBranchMiddleware populates tenant and branch later; signals resolve them.
        with audit_context(request=request, user=actor):
            response = self.get_response(request)
            actor = request.audit_actor
            tenant = getattr(request, 'tenant', None) or (actor.tenant if actor else None)
            branch = getattr(request, 'branch', None)
            info = metadata(request)
            outcome = 'denied' if response.status_code in (401, 403, 404) else 'failure' if response.status_code >= 400 else 'success'
            if request.method not in ('GET', 'HEAD', 'OPTIONS'):
                failed = request.audit_failed or any(m.level >= ERROR for m in get_messages(request))
                if response.status_code == 200 and response.get('Content-Type', '').startswith('application/json'):
                    import json
                    try: failed = failed or json.loads(response.content).get('ok') is False
                    except (ValueError, AttributeError): pass
                elif response.status_code == 200 and not request.audit_change_count:
                    failed = True
                if failed and outcome == 'success': outcome = 'failure'
            # Static assets are not business actions. App/API/auth requests are covered.
            if not request.path.startswith(('/static/', '/favicon.ico')):
                RequestLog.objects.create(tenant=tenant, branch=branch, user=actor,
                    actor_name=actor.username if actor else 'Anonymous', status_code=response.status_code,
                    outcome=outcome, duration_ms=max(0, int((monotonic()-started)*1000)),
                    target=str(getattr(request.resolver_match, 'kwargs', {}))[:200], **info)
                if outcome != 'success':
                    write_event(actor, 'access.denied' if outcome == 'denied' else 'action.failed',
                        info['route'], f'HTTP {response.status_code}', branch=branch, tenant=tenant,
                        kind='security' if outcome == 'denied' else 'activity', outcome=outcome)
                elif request.GET.get('csv') and response.get('Content-Type', '').startswith('text/csv'):
                    write_event(actor, 'report.export', info['route'], branch=branch, tenant=tenant, kind='activity')
                elif request.GET.get('print') == '1':
                    write_event(actor, 'print.request', info['route'], 'Print view requested; physical printing is not confirmed.', branch=branch, tenant=tenant, kind='activity')
            response['X-Request-ID'] = request.audit_request_id
            return response
