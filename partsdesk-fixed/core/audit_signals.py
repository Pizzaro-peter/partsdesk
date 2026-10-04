"""Business model changes, relationship changes and authentication events."""
import json
from django.apps import apps
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models.signals import pre_save, post_save, pre_delete, m2m_changed
from .audit import context, write_event, audit_context

TRACKED = {
    'core.Tenant': 'tenant', 'core.Branch': 'branch', 'core.User': 'user', 'core.ShopSettings': 'settings',
    'catalog.Part': 'part', 'catalog.Category': 'category', 'catalog.Supplier': 'supplier',
    'catalog.Vehicle': 'vehicle', 'catalog.PartNumber': 'part.xref', 'catalog.BranchStock': 'stock.balance',
    'catalog.CatalogueImport': 'catalog.upload', 'sales.Customer': 'customer', 'sales.CustomerVehicle': 'customer.vehicle',
    'sales.Sale': 'sale', 'sales.SaleLine': 'sale.line', 'sales.Payment': 'payment',
    'sales.SaleReturn': 'return', 'sales.SaleReturnLine': 'return.line',
    'stock.StockMovement': 'stock.movement', 'stock.PurchaseOrder': 'po', 'stock.PurchaseOrderLine': 'po.line',
    'workshop.JobCard': 'job', 'workshop.JobPart': 'job.part', 'workshop.JobLabour': 'job.labour',
}
# Avoid credential material and unbounded/free-form personal or payment text.
EXCLUDED = {'password','last_login','created_at','updated_at','rows','address','phone','email','vin',
            'notes','complaint','diagnosis','reference','description','contact_person','payment_terms'}


def snapshot(instance):
    data = {}
    for field in instance._meta.concrete_fields:
        if field.name not in EXCLUDED:
            data[field.name] = getattr(instance, field.attname)
    if instance._meta.label == 'catalog.CatalogueImport':
        data['row_count'] = len(instance.rows)
    return json.loads(json.dumps(data, cls=DjangoJSONEncoder))


def scope(instance):
    from .models import Branch, Tenant
    if isinstance(instance, Tenant): return instance, None
    if isinstance(instance, Branch): return instance.tenant, instance
    branch = getattr(instance, 'branch', None)
    tenant = getattr(instance, 'tenant', None)
    if branch: return branch.tenant, branch
    if tenant:
        request = context().get('request')
        chosen = getattr(request, 'branch', None) if request else context().get('branch')
        return tenant, chosen if chosen and chosen.tenant_id == tenant.pk else None
    for attr in ('sale','sale_return','sale_line','po','job','part','customer'):
        parent = getattr(instance, attr, None)
        if parent is not None: return scope(parent)
    return None, None


def target(instance):
    for attr in ('number','sku','name','reg_number','username'):
        value = getattr(instance, attr, None)
        if value: return str(value)
    for attr in ('sale','po','job','part','sale_return'):
        parent = getattr(instance, attr, None)
        if parent is not None: return f'{target(parent)} / {instance._meta.model_name} #{instance.pk}'
    return f'{instance._meta.model_name} #{instance.pk}'


def before_save(sender, instance, raw=False, **kwargs):
    if raw: return
    old = sender.objects.filter(pk=instance.pk).first() if instance.pk else None
    instance._audit_before = snapshot(old) if old else {}
    omitted = EXCLUDED - {'password','last_login','created_at','updated_at','rows'}
    instance._audit_redacted_fields = [f.name for f in instance._meta.concrete_fields if f.name in omitted and ((old is not None and getattr(old,f.attname) != getattr(instance,f.attname)) or (old is None and bool(getattr(instance,f.attname))))]
    instance._audit_password_changed = bool(old and sender._meta.label == 'core.User' and old.password != instance.password)


def after_save(sender, instance, created, raw=False, **kwargs):
    if raw: return
    before, after = getattr(instance, '_audit_before', {}), snapshot(instance)
    changes = {k: {'before': before.get(k), 'after': v} for k,v in after.items() if created or before.get(k) != v}
    redacted = getattr(instance, '_audit_redacted_fields', [])
    if redacted:
        changes['redacted_fields_changed'] = {'before': None, 'after': redacted}
    tenant, branch = scope(instance)
    actor = context().get('user')
    if actor is None and created:
        actor = getattr(instance, 'created_by', None) or getattr(instance, 'added_by', None)
        if sender._meta.label == 'stock.StockMovement': actor = instance.user
    action = TRACKED[sender._meta.label] + ('.create' if created else '.update')
    if changes:
        write_event(actor, action, target(instance), f'{sender._meta.verbose_name}: '+('created' if created else 'updated'),
                    tenant=tenant, branch=branch, changes=changes)
    if getattr(instance, '_audit_password_changed', False):
        write_event(actor, 'auth.password_change', instance.username, 'Password changed; value not recorded.', tenant=tenant, branch=branch, kind='authentication')


def before_delete(sender, instance, **kwargs):
    tenant, branch = scope(instance)
    write_event(context().get('user'), TRACKED[sender._meta.label]+'.delete', target(instance),
                'Record deleted', tenant=tenant, branch=branch, changes={'deleted': snapshot(instance)})


def relationship(sender, instance, action, reverse, model, pk_set, **kwargs):
    if action not in ('pre_clear','post_add','post_remove'): return
    if action == 'pre_clear':
        # Capture IDs before the through table is emptied.
        relations = {apps.get_model('catalog','Part').fits.through:'fits', apps.get_model('core','User').branches.through:'branches', apps.get_model('core','User').groups.through:'groups', apps.get_model('core','User').user_permissions.through:'user_permissions'}
        relation = relations[sender]
        if reverse:
            ids = list(model.objects.filter(**{relation: instance}).values_list('pk',flat=True))
        else: ids = list(getattr(instance, relation).values_list('pk',flat=True))
    else: ids = sorted(pk_set or [])
    if not ids: return
    tenant, branch = scope(instance)
    labels = {apps.get_model('catalog','Part').fits.through:'part.fitment', apps.get_model('core','User').branches.through:'user.branch_access', apps.get_model('core','User').groups.through:'user.group_access', apps.get_model('core','User').user_permissions.through:'user.permission_access'}
    label = labels[sender]
    write_event(context().get('user'), label+'.'+action.replace('post_','').replace('pre_',''), target(instance),
                'Relationship membership changed', tenant=tenant, branch=branch,
                changes={'related_ids':ids,'reverse':reverse,'operation':action})


def login(sender, request, user, **kwargs):
    from .tenancy import active_branch
    request.user = user
    request.audit_actor = user
    request.tenant = user.tenant
    request.branch = active_branch(request)
    write_event(user,'auth.login',user.username,'Signed in',tenant=user.tenant,branch=request.branch,kind='authentication')


def logout(sender, request, user, **kwargs):
    if user and request:
        request.audit_actor = user
        write_event(user,'auth.logout',user.username,'Signed out',tenant=user.tenant,
                    branch=getattr(request,'branch',None),kind='authentication')


def failed_login(sender, credentials, request, **kwargs):
    from .models import User
    account = User.objects.filter(username=str(credentials.get('username',''))[:150]).first()
    if request is not None:
        request.audit_failed = True
    with audit_context(user=None, branch=None, tenant=account.tenant if account else None):
        write_event(None,'auth.login_failed','login','Invalid credentials or inactive account',
            tenant=account.tenant if account else None, branch=None, actor_name=account.username if account else 'Unknown account',
            kind='authentication',outcome='failure')



def connect():
    for label in TRACKED:
        model = apps.get_model(label)
        for signal,receiver in [(pre_save,before_save),(post_save,after_save),(pre_delete,before_delete)]:
            signal.connect(receiver,sender=model,weak=False,dispatch_uid=f'audit:{label}:{receiver.__name__}')
    for sender in [apps.get_model('catalog','Part').fits.through,apps.get_model('core','User').branches.through,apps.get_model('core','User').groups.through,apps.get_model('core','User').user_permissions.through]:
        m2m_changed.connect(relationship,sender=sender,weak=False,dispatch_uid=f'audit:m2m:{sender._meta.label}')
    user_logged_in.connect(login,weak=False,dispatch_uid='audit:login')
    user_logged_out.connect(logout,weak=False,dispatch_uid='audit:logout')
    user_login_failed.connect(failed_login,weak=False,dispatch_uid='audit:login_failed')
