"""Audit context and safe structured events. Never serialize a request body."""
from contextvars import ContextVar
from contextlib import contextmanager
from functools import wraps
from inspect import signature
from ipaddress import ip_address
from django.db import transaction

_current = ContextVar('partsdesk_audit_context', default={})


@contextmanager
def audit_context(**values):
    token = _current.set(dict(_current.get(), **values))
    try:
        yield
    finally:
        _current.reset(token)


def context():
    return _current.get()


def metadata(request):
    try:
        address = str(ip_address(request.META.get('REMOTE_ADDR', '')))
    except ValueError:
        address = None
    match = request.resolver_match
    return dict(request_id=request.audit_request_id, ip_address=address,
                route=(match.view_name if match else 'unmatched')[:100], method=request.method[:10])


def write_event(user, action, target='', detail='', *, branch=None, tenant=None,
                kind='business', outcome='success', changes=None, actor_name=None):
    from .models import AuditLog
    ctx = context()
    user = user if user is not None else ctx.get('user')
    if user is not None and not user.is_authenticated:
        user = None
    branch = branch or ctx.get('branch')
    tenant = tenant or (branch.tenant if branch else None) or (user.tenant if user else None) or ctx.get('tenant')
    if branch and tenant and branch.tenant_id != tenant.pk:
        raise ValueError('Audit branch and tenant do not match.')
    if user and tenant and user.tenant_id != tenant.pk and not user.is_superuser:
        raise ValueError('Audit actor and tenant do not match.')
    request = ctx.get('request')
    values = metadata(request) if request else {}
    entry = AuditLog.objects.create(tenant=tenant, branch=branch, user=user,
        actor_name=(actor_name or (user.username if user else 'System'))[:150], action=action[:40],
        target=str(target)[:200], detail=str(detail)[:2000], kind=kind, outcome=outcome,
        changes=changes or {}, **values)
    if request:
        request.audit_change_count += 1
    return entry


def audit_actor(fn):
    """Preserve the actor for service calls, including non-HTTP callers."""
    sig = signature(fn)
    @wraps(fn)
    def wrapped(*args, **kwargs):
        values = sig.bind_partial(*args, **kwargs).arguments
        user = values.get('user') or context().get('user')
        branch = values.get('branch') or context().get('branch')
        if branch is None:
            for key in ('sale', 'job', 'po', 'job_part', 'labour', 'batch'):
                obj = values.get(key)
                if obj is not None:
                    parent = getattr(obj, 'job', obj)
                    branch = getattr(parent, 'branch', None)
                    if branch: break
        with audit_context(user=user, branch=branch):
            try:
                with transaction.atomic():
                    return fn(*args, **kwargs)
            except Exception:
                request = context().get('request')
                if request:
                    request.audit_failed = True
                # HTTP middleware records failures outside the business transaction.
                # CLI failures propagate; no successful business event survives rollback.
                raise
    return wrapped
