"""Security and transaction checks for audit and request logging."""
import json
from datetime import datetime, timezone as tz
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase, Client
from django.urls import reverse
from django.db import transaction
import uat_regression as baseline
from core.models import AuditLog, RequestLog, User, audit
from core.audit import audit_context
from catalog.models import Part, CatalogueImport
from sales.services import create_sale, add_payment
from stock.services import adjust_to_count, receive_po, add_po_line
from workshop.services import add_labour, remove_labour
from catalog.excel_import import confirm_import

class AuditTests(TestCase):
    @classmethod
    def setUpTestData(cls):baseline.AcceptanceTests.setUpTestData.__func__(cls)
    setUp=baseline.AcceptanceTests.setUp
    def sale(self,**kw):return baseline.AcceptanceTests.sale(self,**kw)
    def serialized(self):
        return json.dumps(list(AuditLog.objects.values())+list(RequestLog.objects.values()),default=str)
    def test_login_logout_request_metadata(self):
        c=Client();r=c.post(reverse('login'),{'username':self.owner.username,'password':'Acceptance-only-password-123'},REMOTE_ADDR='192.0.2.10',HTTP_X_FORWARDED_FOR='203.0.113.99')
        self.assertEqual(r.status_code,302)
        e=AuditLog.objects.filter(action='auth.login',request_id=r['X-Request-ID']).get()
        self.assertEqual(e.user,self.owner);self.assertEqual(e.tenant,self.ta);self.assertEqual(e.ip_address,'192.0.2.10')
        req=RequestLog.objects.get(request_id=e.request_id);self.assertEqual(req.outcome,'success')
        r=c.post(reverse('logout'));self.assertEqual(r.status_code,302)
        self.assertTrue(AuditLog.objects.filter(action='auth.logout',user=self.owner,request_id=r['X-Request-ID']).exists())
        self.assertNotIn('Acceptance-only-password-123',self.serialized())
    def test_failed_login_known_account_visible_only_to_its_tenant(self):
        r=Client().post(reverse('login'),{'username':self.owner.username,'password':'DO-NOT-LOG-THIS'})
        e=AuditLog.objects.get(action='auth.login_failed',request_id=r['X-Request-ID'])
        self.assertEqual(e.tenant,self.ta);self.assertIsNone(e.branch);self.assertEqual(e.outcome,'failure')
        self.assertEqual(RequestLog.objects.get(request_id=r['X-Request-ID']).outcome,'failure')
        self.assertNotIn('DO-NOT-LOG-THIS',self.serialized())
        self.client.force_login(self.owner_b)
        self.assertEqual(len(self.client.get(reverse('audit_list'),{'action':'auth.login_failed','branch':'all'}).context['entries']),0)
    def test_unknown_login_has_no_tenant_and_no_raw_credentials(self):
        r=Client().post(reverse('login'),{'username':'UNKNOWN-SECRET-ACCOUNT','password':'SECRET-PASSWORD'})
        e=AuditLog.objects.get(action='auth.login_failed',request_id=r['X-Request-ID'])
        self.assertIsNone(e.tenant);self.assertIsNone(e.user);self.assertEqual(e.actor_name,'Unknown account')
        self.assertNotIn('SECRET-PASSWORD',self.serialized());self.assertNotIn('UNKNOWN-SECRET-ACCOUNT',self.serialized())
    def test_password_change_no_password_hash_or_values(self):
        r=self.client.post(reverse('password_change'),{'old_password':'Acceptance-only-password-123','new_password1':'New-password-for-UAT-765','new_password2':'New-password-for-UAT-765'})
        self.assertEqual(r.status_code,302);self.assertTrue(AuditLog.objects.filter(action='auth.password_change',target=self.owner.username).exists())
        data=self.serialized();self.assertNotIn('New-password-for-UAT-765',data);self.assertNotIn('pbkdf2_',data);self.assertNotIn('"password"',data)
    def test_sale_payment_discount_and_correct_service_actor(self):
        sale=create_sale(user=self.owner,branch=self.main,customer=self.trade,payment_method='credit',amount_paid=0,
            lines=[dict(part=self.part,quantity=1,unit_price=100,discount_pct=10)])
        self.assertTrue(AuditLog.objects.filter(action='sale.create',user=self.owner,target=sale.number).exists())
        e=AuditLog.objects.filter(action='sale.line.create',target__startswith=sale.number).last();self.assertEqual(Decimal(e.changes['discount_pct']['after']),10)
        add_payment(sale=sale,amount=20,method='cash',user=self.users['manager'],reference='PRIVATE-PAYMENT-REFERENCE')
        e=AuditLog.objects.filter(action='payment.create',target__startswith=sale.number).first()
        self.assertEqual(e.user,self.users['manager']);self.assertEqual(Decimal(e.changes['amount']['after']),20)
        self.assertNotIn('PRIVATE-PAYMENT-REFERENCE',self.serialized())
    def test_adjustment_before_after_and_reason(self):
        adjust_to_count(self.part,15,self.owner,'Physical count',branch=self.main)
        e=AuditLog.objects.filter(action='stock.balance.update',branch=self.main).first()
        self.assertEqual(Decimal(e.changes['quantity_on_hand']['before']),20)
        self.assertEqual(Decimal(e.changes['quantity_on_hand']['after']),15)
        self.assertEqual(e.user,self.owner)
        self.assertTrue(AuditLog.objects.filter(action='stock.adjust',detail__contains='Physical count').exists())
    def test_failed_checkout_no_success_events_and_failure_request(self):
        before=AuditLog.objects.filter(kind='business').count()
        r=baseline.AcceptanceTests.checkout(self,lines=[{'part_id':self.part.pk,'quantity':1000}])
        self.assertEqual(r.status_code,400);self.assertEqual(AuditLog.objects.filter(kind='business').count(),before)
        self.assertTrue(AuditLog.objects.filter(action='action.failed',request_id=r['X-Request-ID']).exists())
        self.assertEqual(RequestLog.objects.get(request_id=r['X-Request-ID']).outcome,'failure')
    def test_business_rollback_removes_events(self):
        before=AuditLog.objects.count()
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                Part.objects.create(tenant=self.ta,sku='ROLLBACK-AUDIT',name='Rollback')
                raise RuntimeError('rollback')
        self.assertEqual(AuditLog.objects.count(),before);self.assertFalse(Part.objects.filter(sku='ROLLBACK-AUDIT').exists())
    def test_audit_write_failure_rolls_back_model_save(self):
        with patch('core.models.AuditLog.objects.create',side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):Part.objects.create(tenant=self.ta,sku='FAIL-AUDIT',name='Fail')
        self.assertFalse(Part.objects.filter(sku='FAIL-AUDIT').exists())
    def test_request_gets_are_separate_and_do_not_log_query_or_cookies(self):
        count=AuditLog.objects.count()
        self.client.cookies['private-cookie']='PRIVATE-COOKIE'
        r=self.client.get(reverse('part_list'),{'token':'PRIVATE-TOKEN'})
        self.assertEqual(r.status_code,200);self.assertEqual(AuditLog.objects.count(),count)
        e=RequestLog.objects.get(request_id=r['X-Request-ID']);self.assertEqual(e.route,'part_list');self.assertEqual(e.user,self.owner)
        self.assertNotIn('PRIVATE-TOKEN',self.serialized());self.assertNotIn('PRIVATE-COOKIE',self.serialized())
    def test_denied_role_and_cross_tenant_access_logged_to_actor_tenant(self):
        self.client.force_login(self.users['counter'])
        r=self.client.get(reverse('audit_list'));self.assertEqual(r.status_code,403)
        e=AuditLog.objects.get(action='access.denied',request_id=r['X-Request-ID']);self.assertEqual(e.tenant,self.ta)
        r=self.client.get(reverse('part_detail',args=[self.part_b.pk]));self.assertEqual(r.status_code,404)
        self.assertEqual(AuditLog.objects.get(action='access.denied',request_id=r['X-Request-ID']).tenant,self.ta)
    def test_append_only_queryset_instance_and_bulk_update(self):
        e=audit(self.owner,'test.readonly',branch=self.main)
        for fn in [lambda:AuditLog.objects.filter(pk=e.pk).update(detail='changed'),lambda:AuditLog.objects.filter(pk=e.pk).delete(),lambda:e.delete(),lambda:e.save(),lambda:AuditLog.objects.bulk_update([e],['detail'])]:
            with self.assertRaises(ValueError):fn()
        self.assertTrue(AuditLog.objects.filter(pk=e.pk).exists())
    def test_filters_and_exports_respect_branch_assignments(self):
        audit(self.owner,'TEST-EAST','EAST-ONLY-RECORD',branch=self.east)
        audit(self.owner_b,'TEST-OTHER','OTHER-TENANT-RECORD',branch=self.other)
        self.client.force_login(self.users['manager'])
        for data in [{'branch':'all','csv':'1'},{'branch':str(self.east.pk),'csv':'1'},{'branch':str(self.other.pk),'csv':'1'}]:
            r=self.client.get(reverse('audit_list'),data);self.assertEqual(r.status_code,200)
            self.assertNotContains(r,'EAST-ONLY-RECORD');self.assertNotContains(r,'OTHER-TENANT-RECORD')
        self.client.force_login(self.owner)
        r=self.client.get(reverse('audit_list'),{'branch':'all','action':'TEST-EAST','csv':'1'});self.assertContains(r,'EAST-ONLY-RECORD');self.assertNotContains(r,'OTHER-TENANT-RECORD')
    def test_request_filters_exports_and_formula_escape(self):
        e=audit(self.owner,'TEST-CSV','=SUM(1,2)',branch=self.main)
        r=self.client.get(reverse('audit_list'),{'action':'TEST-CSV','csv':'1'});self.assertContains(r,"'=SUM")
        self.client.get(reverse('part_list'))
        r=self.client.get(reverse('audit_list'),{'view':'requests','action':'part_list','csv':'1'});self.assertContains(r,'part_list');self.assertContains(r,'HTTP status')
        r=self.client.get(reverse('audit_list'),{'user':self.owner_b.pk,'view':'requests','csv':'1'});self.assertNotContains(r,'part_list')
    def test_zambia_date_boundary_and_invalid_dates(self):
        with patch('django.utils.timezone.now',return_value=datetime(2030,1,1,22,15,tzinfo=tz.utc)):
            audit(self.owner,'TEST-DATE','LOCAL-JAN-2',branch=self.main)
        r=self.client.get(reverse('audit_list'),{'action':'TEST-DATE','start':'2030-01-02','end':'2030-01-02','csv':'1'});self.assertContains(r,'LOCAL-JAN-2');self.assertContains(r,'+02:00')
        r=self.client.get(reverse('audit_list'),{'start':'bad'});self.assertEqual(r.status_code,200);self.assertContains(r,'Invalid start date')
    def test_print_requests_verified_and_csrf_protected(self):
        sale=self.sale();r=self.client.post(reverse('log_print_request'),{'path':reverse('sale_detail',args=[sale.pk])})
        self.assertEqual(r.status_code,200);self.assertTrue(AuditLog.objects.filter(action='print.request',target=sale.number).exists())
        r=self.client.post(reverse('log_print_request'),{'path':'https://external.example/'});self.assertEqual(r.status_code,400)
        c=Client(enforce_csrf_checks=True);c.force_login(self.owner)
        self.assertEqual(c.post(reverse('log_print_request'),{'path':reverse('sale_detail',args=[sale.pk])}).status_code,403)
    def test_auto_print_and_report_export_logged(self):
        sale=self.sale();r=self.client.get(reverse('sale_detail',args=[sale.pk]),{'print':'1'})
        self.assertTrue(AuditLog.objects.filter(action='print.request',request_id=r['X-Request-ID']).exists())
        r=self.client.get(reverse('report_sales'),{'csv':'1'})
        self.assertTrue(AuditLog.objects.filter(action='report.export',request_id=r['X-Request-ID']).exists())
    def test_relationship_add_remove_clear(self):
        from catalog.models import Vehicle
        v=Vehicle.objects.create(tenant=self.ta,make='Toyota',model='Vitz',year_from=2005,year_to=2010)
        with audit_context(user=self.owner,branch=self.main):
            self.part.fits.add(v);self.part.fits.remove(v);self.part.fits.add(v);self.part.fits.clear()
        for name in ('add','remove','clear'):self.assertTrue(AuditLog.objects.filter(action='part.fitment.'+name,user=self.owner).exists())
    def test_user_role_branch_and_settings_changes_logged(self):
        manager=self.users['manager']
        with audit_context(user=self.owner,branch=self.main):
            manager.role='storekeeper';manager.save();manager.branches.add(self.east)
            self.cfg.shop_name='Audit test shop';self.cfg.save()
        self.assertTrue(AuditLog.objects.filter(action='user.update',target=manager.username,changes__has_key='role').exists())
        self.assertTrue(AuditLog.objects.filter(action='user.branch_access.add',target=manager.username).exists())
        self.assertTrue(AuditLog.objects.filter(action='settings.update',changes__has_key='shop_name').exists())
    def test_staff_creation_never_records_password(self):
        r=self.client.post(reverse('staff'),{'username':'audit-staff','first_name':'Test','last_name':'Staff','password':'Staff-secret-12345','role':'counter','branches':[self.main.pk]})
        self.assertEqual(r.status_code,302);e=AuditLog.objects.get(action='user.create',target='audit-staff')
        self.assertEqual(e.user,self.owner);self.assertNotIn('Staff-secret-12345',self.serialized())
    def test_purchase_and_workshop_labour_changes(self):
        with audit_context(user=self.owner,branch=self.main):
            add_po_line(self.po,self.part,2,60)
            self.po.status='ordered';self.po.save()
            receive_po(self.po,{self.po.lines.get().pk:2},self.owner)
            labour=add_labour(self.job,'Test work',1,50);remove_labour(labour)
        for name in ('po.line.create','po.update','po.receive','job.labour.create','job.labour.delete'):
            with self.subTest(action=name):self.assertTrue(AuditLog.objects.filter(action=name,user=self.owner).exists())
    def test_excel_upload_rows_not_copied_to_audit(self):
        b=CatalogueImport.objects.create(tenant=self.ta,branch=self.main,created_by=self.owner,file_name='parts.xlsx',mode='create',rows=[{'_row':2,'sku':'AUDIT-XLSX','name':'Audit import','sell_price':'10'}])
        confirm_import(b,self.owner)
        self.assertTrue(AuditLog.objects.filter(action='catalog.import',user=self.owner).exists())
        self.assertTrue(AuditLog.objects.filter(action='catalog.upload.update').exists())
        self.assertNotIn('"rows"',self.serialized())
    def test_request_actor_does_not_leak_between_sessions(self):
        r=self.client.get(reverse('part_list'));first=RequestLog.objects.get(request_id=r['X-Request-ID'])
        self.client.force_login(self.owner_b);r=self.client.get(reverse('part_list'));second=RequestLog.objects.get(request_id=r['X-Request-ID'])
        self.assertEqual(first.tenant,self.ta);self.assertEqual(second.tenant,self.tb);self.assertEqual(second.user,self.owner_b)

    def test_branch_switch_and_permission_memberships(self):
        from django.contrib.auth.models import Group, Permission
        r=self.client.post(reverse('switch_branch'),{'branch_id':self.east.pk})
        e=AuditLog.objects.get(action='branch.switch',request_id=r['X-Request-ID'])
        self.assertEqual(e.changes['branch_id']['before'],self.main.pk);self.assertEqual(e.changes['branch_id']['after'],self.east.pk)
        with audit_context(user=self.owner,branch=self.main):
            group=Group.objects.create(name='UAT group');self.users['manager'].groups.add(group);self.users['manager'].groups.clear()
            self.users['manager'].user_permissions.add(Permission.objects.first())
        for action in ('user.group_access.add','user.group_access.clear','user.permission_access.add'):
            self.assertTrue(AuditLog.objects.filter(action=action,user=self.owner).exists())

    def test_csrf_and_foreign_print_access_denied(self):
        c=Client(enforce_csrf_checks=True);c.force_login(self.owner)
        r=c.post(reverse('stock_adjust'),{})
        self.assertEqual(r.status_code,403);self.assertTrue(AuditLog.objects.filter(action='access.denied',request_id=r['X-Request-ID']).exists())
        other=create_sale(user=self.owner_b,branch=self.other,lines=[dict(part=self.part_b,quantity=1,unit_price=75)])
        r=self.client.post(reverse('log_print_request'),{'path':reverse('sale_detail',args=[other.pk])})
        self.assertEqual(r.status_code,404);self.assertFalse(AuditLog.objects.filter(action='print.request',target=other.number,tenant=self.ta).exists())

    def test_invalid_form_and_login_while_signed_in(self):
        r=self.client.post(reverse('customer_create'),{'name':''})
        self.assertEqual(r.status_code,200);self.assertEqual(RequestLog.objects.get(request_id=r['X-Request-ID']).outcome,'failure')
        r=self.client.post(reverse('login'),{'username':self.owner_b.username,'password':'wrong-password'})
        self.assertEqual(r.status_code,200)
        e=AuditLog.objects.get(action='auth.login_failed',request_id=r['X-Request-ID']);self.assertEqual(e.tenant,self.tb);self.assertIsNone(e.user)

    def test_reverse_relationship_clear(self):
        from catalog.models import Vehicle
        v=Vehicle.objects.create(tenant=self.ta,make='Test',model='Reverse',year_from=2000,year_to=2001)
        with audit_context(user=self.owner,branch=self.main):
            v.parts.add(self.part);v.parts.clear();self.main.staff.add(self.users['manager']);self.main.staff.clear()
        self.assertTrue(AuditLog.objects.filter(action='part.fitment.clear',changes__reverse=True).exists())
        self.assertTrue(AuditLog.objects.filter(action='user.branch_access.clear',changes__reverse=True).exists())

    def test_description_only_change_recorded_without_contents(self):
        with audit_context(user=self.owner,branch=self.main):
            self.part.description='PRIVATE-DESCRIPTION-TEXT';self.part.save(update_fields=['description'])
        e=AuditLog.objects.filter(action='part.update',target=self.part.sku).first()
        self.assertIn('description',e.changes['redacted_fields_changed']['after'])
        self.assertNotIn('PRIVATE-DESCRIPTION-TEXT',self.serialized())

    def test_known_login_failure_can_be_filtered_by_user(self):
        Client().post(reverse('login'),{'username':self.owner.username,'password':'wrong'})
        r=self.client.get(reverse('audit_list'),{'action':'auth.login_failed','user':self.owner.pk,'csv':'1'})
        self.assertContains(r,'auth.login_failed')
