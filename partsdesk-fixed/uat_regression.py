"""Independent acceptance regression suite for PartsDesk."""
import io
import csv
import json
import re
import tempfile
from html.parser import HTMLParser
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from django.core.management import call_command, CommandError
from django.core.exceptions import PermissionDenied
from django.db import transaction, IntegrityError
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from core.models import Tenant, Branch, User, ShopSettings, AuditLog, Sequence
from catalog.models import Part, BranchStock, Supplier, Vehicle, PartNumber
from catalog.forms import PartCreateForm
from sales.models import Customer, CustomerVehicle, Sale, Payment, SaleReturn
from sales.services import create_sale, add_payment, process_return, SaleError
from sales.reporting import daily_figures, part_figures
from stock.models import PurchaseOrder, StockMovement
from stock.services import record_movement, adjust_to_count, add_po_line, receive_po, draft_pos_from_low_stock, StockError
from workshop.models import JobCard, JobPart, JobLabour
from workshop.services import create_job, add_part, remove_part, add_labour, cancel_job, invoice_job


class AcceptanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ta=Tenant.objects.create(name='UAT A',slug='uat-a')
        cls.tb=Tenant.objects.create(name='UAT B',slug='uat-b')
        cls.main=Branch.objects.create(tenant=cls.ta,name='A Main',code='MAIN')
        cls.east=Branch.objects.create(tenant=cls.ta,name='B East',code='EAST')
        cls.other=Branch.objects.create(tenant=cls.tb,name='Other Main',code='MAIN')
        cls.cfg=ShopSettings.objects.create(tenant=cls.ta,currency_symbol='K',tax_rate=0,counter_max_discount=10)
        ShopSettings.objects.create(tenant=cls.tb)
        cls.users={}
        for role in ['owner','manager','counter','mechanic','storekeeper']:
            u=User.objects.create_user(username='uat-'+role,password='Acceptance-only-password-123',tenant=cls.ta,role=role)
            u.branches.add(cls.main);cls.users[role]=u
        cls.owner=cls.users['owner']
        cls.owner_b=User.objects.create_user(username='uat-owner-b',password='Acceptance-only-password-123',tenant=cls.tb,role='owner')
        cls.supplier=Supplier.objects.create(tenant=cls.ta,name='Supplier A')
        cls.supplier_b=Supplier.objects.create(tenant=cls.tb,name='Supplier B')
        cls.part=Part.objects.create(tenant=cls.ta,sku='UAT-FILTER',barcode='UAT-123',name='UAT Filter',cost_price=60,sell_price=100,trade_price=90,preferred_supplier=cls.supplier)
        cls.belt=Part.objects.create(tenant=cls.ta,sku='UAT-BELT',name='UAT Belt',cost_price=120,sell_price=200)
        cls.part_b=Part.objects.create(tenant=cls.tb,sku='UAT-FILTER',name='Secret B Filter',sell_price=75)
        for part,branch,qty,user in [(cls.part,cls.main,20,cls.owner),(cls.part,cls.east,5,cls.owner),(cls.belt,cls.main,10,cls.owner),(cls.part_b,cls.other,3,cls.owner_b)]:
            record_movement(part,qty,'opening',user,branch=branch)
        cls.retail=Customer.objects.create(tenant=cls.ta,name='UAT Retail',credit_limit=0)
        cls.trade=Customer.objects.create(tenant=cls.ta,name='UAT Trade',customer_type='trade',credit_limit=1000)
        cls.customer_b=Customer.objects.create(tenant=cls.tb,name='Secret B Customer')
        cls.vehicle=CustomerVehicle.objects.create(customer=cls.retail,reg_number='UAT-001',make='Toyota',model='Vitz')
        cls.vehicle_b=CustomerVehicle.objects.create(customer=cls.customer_b,reg_number='SECRET-B',make='Honda',model='Fit')
        cls.po=PurchaseOrder.objects.create(branch=cls.main,supplier=cls.supplier,number='PO-UAT')
        cls.po_b=PurchaseOrder.objects.create(branch=cls.other,supplier=cls.supplier_b,number='PO-SECRET')
        cls.job=create_job(user=cls.owner,branch=cls.main,customer=cls.retail,vehicle=cls.vehicle,complaint='UAT work',assigned_to=cls.users['mechanic'])
        cls.job_b=create_job(user=cls.owner_b,branch=cls.other,customer=cls.customer_b,vehicle=cls.vehicle_b,complaint='Secret B job')

    def setUp(self):
        self.client.force_login(self.owner)
        sess=self.client.session;sess['branch_id']=self.main.pk;sess.save()

    def qty(self,part=None,branch=None):
        return BranchStock.objects.get(part=part or self.part,branch=branch or self.main).quantity_on_hand

    def sale(self,qty=1,price=100,**kw):
        return create_sale(user=kw.pop('user',self.owner),branch=kw.pop('branch',self.main),lines=[{'part':self.part,'quantity':qty,'unit_price':price}],**kw)

    def checkout(self,**changes):
        payload={'request_id':str(uuid4()),'lines':[{'part_id':self.part.pk,'quantity':1}],'payment_method':'cash'}
        payload.update(changes)
        return self.client.post(reverse('pos_checkout'),json.dumps(payload),content_type='application/json')

    def csv_import(self,text,branch='MAIN'):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'parts.csv';p.write_text(text,encoding='utf-8-sig')
            call_command('import_parts',str(p),tenant=self.ta.slug,branch=branch,stdout=io.StringIO())

    def test_AUTH_01_login_logout(self):
        c=Client();self.assertFalse(c.login(username='uat-owner',password='wrong'))
        self.assertTrue(c.login(username='uat-owner',password='Acceptance-only-password-123'))
        self.assertEqual(c.get(reverse('dashboard')).status_code,200)
        c.logout();self.assertEqual(c.get(reverse('dashboard')).status_code,302)

    def test_AUTH_02_inactive_tenant_denied(self):
        self.ta.is_active=False;self.ta.save();self.assertEqual(self.client.get(reverse('dashboard')).status_code,403)

    def test_AUTH_03_no_branches_denied(self):
        u=self.users['counter'];u.branches.clear();self.client.force_login(u)
        self.assertEqual(self.client.get(reverse('pos')).status_code,403)

    def test_AUTH_04_revoked_selected_branch_falls_back_safely(self):
        self.main.is_active=False;self.main.save()
        self.assertEqual(self.client.get(reverse('dashboard')).status_code,200)
        self.assertEqual(self.client.session['branch_id'],self.east.pk)

    def test_AUTH_05_non_owner_is_not_platform_staff(self):
        for u in self.users.values():self.assertFalse(u.is_staff);self.assertFalse(u.is_superuser)

    def test_AUTH_06_password_change_workflow(self):
        self.assertEqual(self.client.get(reverse('password_change')).status_code,200)
        new='Changed-acceptance-password-777'
        r=self.client.post(reverse('password_change'),{'old_password':'Acceptance-only-password-123','new_password1':new,'new_password2':new})
        self.assertEqual(r.status_code,302);self.owner.refresh_from_db();self.assertTrue(self.owner.check_password(new))
        self.assertEqual(self.client.get(reverse('dashboard')).status_code,200)

    def test_ISO_01_cross_tenant_search(self):
        self.assertNotContains(self.client.get(reverse('part_list')),'Secret B Filter')
        self.assertEqual(self.client.get(reverse('part_search_api'),{'q':'Secret B'}).json()['results'],[])
        self.assertEqual(self.client.get(reverse('customer_api'),{'q':'Secret B'}).json()['results'],[])

    def test_ISO_02_branch_switch_restrictions(self):
        self.client.force_login(self.users['counter'])
        for branch in [self.east,self.other]:self.assertEqual(self.client.post(reverse('switch_branch'),{'branch_id':branch.pk}).status_code,403)

    def test_ISO_03_owner_switch_and_branch_quantity(self):
        self.assertEqual(self.client.post(reverse('switch_branch'),{'branch_id':self.east.pk}).status_code,302)
        self.assertEqual(self.client.get(reverse('part_search_api'),{'q':'UAT-FILTER'}).json()['results'][0]['qty'],5)

    def test_ISO_04_reject_foreign_checkout(self):
        for extra in [{'customer_id':self.customer_b.pk},{'lines':[{'part_id':self.part_b.pk,'quantity':1}]}]:
            self.assertEqual(self.checkout(**extra).status_code,400)
        self.assertEqual(Sale.objects.count(),0);self.assertEqual(self.qty(),20)

    def test_ISO_05_foreign_service_rejected(self):
        with self.assertRaises(PermissionDenied):record_movement(self.part_b,1,'opening',self.owner,branch=self.main)

    def test_ISO_06_foreign_forms_rejected(self):
        response=self.client.post(reverse('po_create'),{'supplier':self.supplier_b.pk})
        self.assertEqual(response.status_code,200);self.assertEqual(PurchaseOrder.objects.count(),2)
        r=self.client.post(reverse('job_create'),{'customer':self.customer_b.pk,'vehicle':self.vehicle_b.pk,'complaint':'wrong'})
        self.assertEqual(r.status_code,200);self.assertEqual(JobCard.objects.count(),2)

    def test_ISO_07_shared_customer_branch_transactions_hidden(self):
        sale=self.sale(customer=self.trade,payment_method='credit',amount_paid=0)
        self.client.post(reverse('switch_branch'),{'branch_id':self.east.pk})
        self.assertEqual(self.client.get(reverse('sale_detail',args=[sale.pk])).status_code,404)
        self.assertNotContains(self.client.get(reverse('customer_detail',args=[self.trade.pk])),sale.number)

    def test_ISO_08_cost_api_restricted(self):
        self.client.force_login(self.users['counter'])
        item=self.client.get(reverse('part_search_api'),{'q':'UAT-FILTER'}).json()['results'][0]
        self.assertNotIn('cost',item);self.assertNotIn('cost_price',item)
        self.assertNotContains(self.client.get(reverse('part_detail',args=[self.part.pk])),'<dt>Cost</dt>')

    def test_ISO_09_branch_sequences_independent(self):
        self.assertEqual(Sequence.next(self.main,'sale','INV'),Sequence.next(self.east,'sale','INV'))

    def test_ISO_10_stale_form_after_branch_switch_rejected(self):
        self.client.get(reverse('job_detail',args=[self.job.pk]))
        self.client.post(reverse('switch_branch'),{'branch_id':self.east.pk})
        self.assertEqual(self.client.post(reverse('job_add_part',args=[self.job.pk]),{'part':self.part.pk,'quantity':1}).status_code,404)
        self.assertEqual(self.qty(),20);self.assertEqual(self.qty(branch=self.east),5)

    def test_DATA_01_duplicate_sku_and_barcode_validation(self):
        for changes in [{'sku':self.part.sku},{'barcode':self.part.barcode}]:
            payload={'sku':'UAT-NEW','name':'New','part_type':'aftermarket','condition':'new','unit':'each','cost_price':'1','sell_price':'2','is_active':'on'};payload.update(changes)
            r=self.client.post(reverse('part_create'),payload)
            self.assertEqual(r.status_code,200);self.assertFalse(Part.objects.filter(tenant=self.ta,name='New').exists())

    def test_DATA_02_negative_prices_rejected(self):
        payload={'sku':'UAT-NEG','name':'Negative','part_type':'aftermarket','condition':'new','unit':'each','cost_price':'-60','sell_price':'-100','trade_price':'-90','is_active':'on'}
        self.client.post(reverse('part_create'),payload)
        self.assertFalse(Part.objects.filter(tenant=self.ta,sku='UAT-NEG').exists(),'Negative-price catalogue part was saved')

    def test_DATA_03_fitment_and_reference_search(self):
        v=Vehicle.objects.create(tenant=self.ta,make='Toyota',model='Vitz',year_from=2005,year_to=2010,engine='1.3')
        self.part.fits.add(v);PartNumber.objects.create(part=self.part,number='ALT-UAT',kind='alt')
        for q in ['UAT-123','ALT-UAT','Toyota Vitz 2007']:
            self.assertEqual(self.client.get(reverse('part_search_api'),{'q':q}).json()['results'][0]['id'],self.part.pk)

    def test_DATA_04_inactive_part_checkout_rejected(self):
        self.part.is_active=False;self.part.save()
        self.assertEqual(self.checkout().status_code,400)
        self.assertEqual(self.client.get(reverse('part_search_api'),{'q':self.part.sku}).json()['results'],[])

    def test_DATA_05_script_text_escaped(self):
        self.job.complaint="<script>alert('x')</script>";self.job.save()
        self.assertNotContains(self.client.get(reverse('job_detail',args=[self.job.pk])),"<script>alert('x')</script>")

    def test_STOCK_01_count_adjustments(self):
        for count in [25,17,0]:
            move=adjust_to_count(self.part,count,self.owner,'UAT count',branch=self.main)
            self.assertEqual(self.qty(),count);self.assertEqual(move.quantity_after,count)
        n=StockMovement.objects.count();self.assertIsNone(adjust_to_count(self.part,0,self.owner,branch=self.main));self.assertEqual(n,StockMovement.objects.count())

    def test_STOCK_02_insufficient_stock_atomic(self):
        with self.assertRaises(StockError):self.sale(qty=21)
        self.assertEqual(self.qty(),20);self.assertEqual(Sale.objects.count(),0);self.assertEqual(Payment.objects.count(),0)

    def test_STOCK_03_multiline_failure_rolls_back_all(self):
        with self.assertRaises(StockError):create_sale(user=self.owner,branch=self.main,lines=[{'part':self.part,'quantity':2,'unit_price':100},{'part':self.belt,'quantity':11,'unit_price':200}])
        self.assertEqual(self.qty(),20);self.assertEqual(self.qty(self.belt),10);self.assertEqual(Sale.objects.count(),0)

    def test_STOCK_04_partial_receipt_average_cost(self):
        l=add_po_line(self.po,self.part,D(10),D(90));self.po.status='ordered';self.po.save()
        receive_po(self.po,{l.pk:D(4)},self.owner);self.po.refresh_from_db()
        self.assertEqual(self.qty(),24);self.assertEqual(self.po.status,'partial')
        receive_po(self.po,{l.pk:D(6)},self.owner);self.po.refresh_from_db()
        self.assertEqual(self.qty(),30);self.assertEqual(self.po.status,'received')
        # Sequential receipts round moving cost per receipt; exact final may differ from a single receipt.
        self.assertEqual(BranchStock.objects.get(branch=self.main,part=self.part).cost_price,D('70.00'))
        self.assertEqual(self.qty(branch=self.east),5)

    def test_STOCK_05_order_status_guards(self):
        l=add_po_line(self.po,self.part,D(10),D(60))
        for status in ['draft','cancelled','received']:
            self.po.status=status;self.po.save()
            with self.assertRaises(StockError):receive_po(self.po,{l.pk:D(1)},self.owner)
        self.assertEqual(self.qty(),20)

    def test_STOCK_06_draft_only_line_edits(self):
        self.po.status='ordered';self.po.save()
        with self.assertRaises(StockError):add_po_line(self.po,self.part,D(1),D(60))

    def test_STOCK_07_replenishment_draft_and_open_order_skip(self):
        item=BranchStock.objects.get(branch=self.main,part=self.part);item.reorder_level=20;item.reorder_qty=10;item.save()
        orders=draft_pos_from_low_stock(self.owner,self.main);self.assertEqual(len(orders),1)
        self.assertEqual(orders[0].lines.get(part=self.part).qty_ordered,10)
        orders[0].status='ordered';orders[0].save()
        self.assertEqual(draft_pos_from_low_stock(self.owner,self.main),[])

    def test_STOCK_08_detail_shows_branch_cost(self):
        record_movement(self.part,10,'receipt',self.owner,unit_cost=D(90),branch=self.main)
        self.assertEqual(BranchStock.objects.get(branch=self.main,part=self.part).cost_price,D(70))
        html=self.client.get(reverse('part_detail',args=[self.part.pk])).content.decode()
        self.assertRegex(html,r'<dt>Cost</dt>\s*<dd>\s*K70\.00','Part detail must show branch cost, not original catalog cost')

    def test_STOCK_09_po_defaults_to_branch_cost(self):
        BranchStock.objects.filter(branch=self.main,part=self.part).update(cost_price=70)
        self.client.post(reverse('po_add_line',args=[self.po.pk]),{'part':self.part.pk,'quantity':'1','unit_cost':''})
        self.assertEqual(self.po.lines.get(part=self.part).unit_cost,D(70),'Blank PO cost uses original catalogue cost instead of current branch cost')

    def test_STOCK_10_draft_remove_button_has_form(self):
        line=add_po_line(self.po,self.part,D(1),D(60))
        html=self.client.get(reverse('po_detail',args=[self.po.pk])).content.decode()
        action=reverse('po_del_line',args=[self.po.pk,line.pk])
        class Forms(HTMLParser):
            def __init__(self):super().__init__();self.depth=0;self.bound=[]
            def handle_starttag(self,tag,attrs):
                attrs=dict(attrs)
                if tag=='form':self.depth+=1
                if tag=='button' and attrs.get('formaction')==action:self.bound.append(self.depth>0 or bool(attrs.get('form')))
            def handle_endtag(self,tag):
                if tag=='form':self.depth=max(0,self.depth-1)
        parser=Forms();parser.feed(html)
        self.assertEqual(parser.bound,[True],'Draft PO Remove button has no form owner, so cannot submit in a browser')

    def test_SALE_01_cash_and_trade_prices(self):
        r=self.checkout(lines=[{'part_id':self.part.pk,'quantity':2}]);self.assertEqual(r.status_code,200)
        s=Sale.objects.get();self.assertEqual((s.total,s.amount_paid,s.balance_due),(200,200,0));self.assertEqual(self.qty(),18)
        self.checkout(customer_id=self.trade.pk);self.assertEqual(Sale.objects.order_by('-pk').first().total,90)

    def test_SALE_02_counter_cannot_override_price(self):
        self.client.force_login(self.users['counter'])
        self.checkout(lines=[{'part_id':self.part.pk,'quantity':1,'unit_price':1}])
        self.assertEqual(Sale.objects.get().total,100)

    def test_SALE_03_counter_discount_boundary(self):
        self.client.force_login(self.users['counter'])
        self.assertEqual(self.checkout(lines=[{'part_id':self.part.pk,'quantity':1,'discount_pct':11}]).status_code,400)
        self.assertEqual(self.checkout(lines=[{'part_id':self.part.pk,'quantity':1,'discount_pct':10}]).status_code,200)
        self.assertEqual(Sale.objects.get().total,90)

    def test_SALE_04_credit_limits_shared(self):
        self.sale(qty=9,customer=self.trade,payment_method='credit',amount_paid=0)
        with self.assertRaises(SaleError):self.sale(qty=2,branch=self.east,customer=self.trade,payment_method='credit',amount_paid=0)
        self.assertEqual(self.trade.balance,900);self.assertEqual(self.qty(branch=self.east),5)

    def test_SALE_05_payment_and_overpayment_guards(self):
        s=self.sale(qty=2,customer=self.trade,amount_paid=50)
        self.assertEqual(s.balance_due,150)
        for amount in [0,-1,151]:
            with self.assertRaises(SaleError):add_payment(sale=s,amount=D(amount),method='cash',user=self.owner)
        add_payment(sale=s,amount=D(150),method='cash',user=self.owner);s.refresh_from_db();self.assertEqual(s.balance_due,0)

    def test_SALE_06_credit_failure_rolls_back(self):
        with self.assertRaises(SaleError):self.sale(customer=self.retail,payment_method='credit',amount_paid=0)
        self.assertEqual(Sale.objects.count(),0);self.assertEqual(self.qty(),20)

    def test_SALE_07_tax_inclusive_exclusive(self):
        self.cfg.tax_rate=10;self.cfg.prices_include_tax=True;self.cfg.save()
        s=self.sale(price=110);self.assertEqual((s.total,s.tax_total),(110,10))
        self.cfg.prices_include_tax=False;self.cfg.save();s=self.sale(price=100);self.assertEqual((s.total,s.tax_total),(110,10))

    def test_SALE_08_historical_price_snapshot(self):
        s=self.sale();self.part.sell_price=200;self.part.save();self.cfg.tax_rate=16;self.cfg.save();s.refresh_from_db()
        self.assertEqual(s.total,100);self.assertEqual(s.lines.get().unit_price,100)

    def test_SALE_09_returns_and_repeated_return_guard(self):
        s=self.sale(qty=2);l=s.lines.get()
        ret=process_return(sale=s,user=self.owner,items={l.pk:D(1)},restock=True)
        s.refresh_from_db();self.assertEqual((self.qty(),ret.total,s.status,s.amount_paid),(19,100,'part_returned',100))
        process_return(sale=s,user=self.owner,items={l.pk:D(1)},restock=True)
        s.refresh_from_db();self.assertEqual((self.qty(),s.status,s.amount_paid),(20,'returned',0))
        with self.assertRaises(SaleError):process_return(sale=s,user=self.owner,items={l.pk:D(1)})

    def test_SALE_10_no_restock_return(self):
        s=self.sale();process_return(sale=s,user=self.owner,items={s.lines.get().pk:D(1)},restock=False);self.assertEqual(self.qty(),19)

    def test_SALE_11_duplicate_checkout_replay(self):
        request_id=str(uuid4())
        for _ in range(2):self.assertEqual(self.checkout(request_id=request_id).status_code,200)
        self.assertEqual(Sale.objects.count(),1,'Replaying an identical checkout creates a second invoice and deduction; no request identity protection')

    def test_SALE_12_counter_cannot_pay_invisible_other_invoice(self):
        s=self.sale(customer=self.trade,payment_method='credit',amount_paid=0)
        self.client.force_login(self.users['counter'])
        self.assertEqual(self.client.get(reverse('sale_detail',args=[s.pk])).status_code,302)
        self.client.post(reverse('sale_payment',args=[s.pk]),{'amount':'50','method':'cash','reference':'Unauthorized'})
        s.refresh_from_db();self.assertEqual(s.amount_paid,0,'Counter can pay another user invoice hidden from their invoice view')

    def test_SALE_13_invoice_subtotal_preserves_cents(self):
        s=self.sale(price=D('100.55'))
        html=self.client.get(reverse('sale_detail',args=[s.pk])).content.decode()
        subtotal=re.search(r'Subtotal</span>\s*<span>([^<]*)',html).group(1)
        self.assertEqual(subtotal,'K100.55','Django add filter truncates Decimal amounts to integer for displayed invoice subtotal')

    def test_JOB_01_parts_labour_invoice_no_double_deduction(self):
        add_part(self.job,self.part,D(2),self.owner);add_labour(self.job,'Work',D(2),D(150))
        s=invoice_job(self.job,self.owner);self.assertEqual(s.total,500);self.assertEqual(self.qty(),18)
        self.job.refresh_from_db();self.assertEqual(self.job.status,'invoiced')
        for fn in [lambda:add_part(self.job,self.part,D(1),self.owner),lambda:cancel_job(self.job,self.owner),lambda:invoice_job(self.job,self.owner)]:
            with self.assertRaises(SaleError):fn()

    def test_JOB_02_csrf_removal_forms(self):
        jp=add_part(self.job,self.part,D(2),self.owner);jl=add_labour(self.job,'Work',D(2),D(150))
        c=Client(enforce_csrf_checks=True);c.force_login(self.owner)
        r=c.get(reverse('job_detail',args=[self.job.pk]));html=r.content.decode()
        for name,args in [('job_remove_part',[self.job.pk,jp.pk]),('job_remove_labour',[self.job.pk,jl.pk])]:
            url=reverse(name,args=args);forms=re.findall(r'<form\b[^>]*>.*?</form>',html,re.S)
            target=next(f for f in forms if f'action="{url}"' in f)
            token=re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"',target).group(1)
            self.assertEqual(c.post(url,{'csrfmiddlewaretoken':token}).status_code,302)
        self.assertEqual(self.qty(),20);self.assertFalse(JobPart.objects.filter(pk=jp.pk).exists());self.assertFalse(JobLabour.objects.filter(pk=jl.pk).exists())

    def test_JOB_03_csrf_missing_token_denied(self):
        jp=add_part(self.job,self.part,D(1),self.owner)
        c=Client(enforce_csrf_checks=True);c.force_login(self.owner);c.get(reverse('job_detail',args=[self.job.pk]))
        self.assertEqual(c.post(reverse('job_remove_part',args=[self.job.pk,jp.pk])).status_code,403);self.assertEqual(self.qty(),19)

    def test_JOB_04_cancel_once_only(self):
        add_part(self.job,self.part,D(2),self.owner);cancel_job(self.job,self.owner)
        self.assertEqual(self.qty(),20)
        with self.assertRaises(SaleError):cancel_job(self.job,self.owner)
        self.assertEqual(self.qty(),20)

    def test_JOB_05_bad_invoice_keeps_issued_stock(self):
        add_part(self.job,self.part,D(2),self.owner)
        with self.assertRaises(SaleError):invoice_job(self.job,self.owner,'credit',0)
        self.job.refresh_from_db();self.assertFalse(self.job.is_locked);self.assertEqual(self.qty(),18);self.assertEqual(Sale.objects.count(),0)

    def test_JOB_06_empty_invoice_rejected(self):
        with self.assertRaises(SaleError):invoice_job(self.job,self.owner)
        self.assertEqual(Sale.objects.count(),0)

    def test_JOB_07_remove_same_stale_line_not_twice(self):
        jp=add_part(self.job,self.part,D(2),self.owner)
        stale=JobPart.objects.get(pk=jp.pk)
        remove_part(jp,self.owner)
        try:remove_part(stale,self.owner)
        except (SaleError,JobPart.DoesNotExist):pass
        self.assertEqual(self.qty(),20,'A stale JobPart instance restores stock again after the row was already deleted')

    def test_JOB_08_mechanic_cancel_button_hidden(self):
        self.client.force_login(self.users['mechanic'])
        self.assertNotContains(self.client.get(reverse('job_detail',args=[self.job.pk])),reverse('job_cancel',args=[self.job.pk]))

    def test_JOB_09_validation_error_visible(self):
        r=self.client.post(reverse('job_detail',args=[self.job.pk]),{'status':'invalid','complaint':'Work'})
        self.assertContains(r,'Select a valid choice',msg_prefix='Invalid status is silently rejected because template omits form errors')

    def test_REP_01_branch_reports_and_return_reconciliation(self):
        s=self.sale(qty=2);process_return(sale=s,user=self.owner,items={s.lines.get().pk:D(1)})
        today=timezone.localdate();row=daily_figures(today,today,self.main)[today]
        self.assertEqual((row['revenue'],row['cost'],row['profit']),(100,60,40))
        self.assertEqual(daily_figures(today,today,self.east)[today]['revenue'],0)
        self.assertEqual(part_figures(today,today,self.main)[0]['qty'],1)

    def test_REP_02_receivables_branch_scoped(self):
        self.sale(customer=self.trade,payment_method='credit',amount_paid=0)
        self.sale(qty=2,branch=self.east,customer=self.trade,payment_method='credit',amount_paid=0)
        self.assertEqual(self.trade.balance,300)
        r=self.client.get(reverse('report_receivables'),{'csv':1})
        values=list(csv.DictReader(io.StringIO(r.content.decode())))
        self.assertEqual(D(values[0]['Owes']),100)

    def test_REP_03_csv_formula_text_safe(self):
        self.part.name='=1+1';self.part.save()
        BranchStock.objects.filter(branch=self.main,part=self.part).update(reorder_level=20)
        text=self.client.get(reverse('report_low_stock'),{'csv':1}).content.decode()
        self.assertNotIn(',=1+1,',text,'Untrusted part name exported as an executable spreadsheet formula')

    def test_REP_04_ledger_reconciles(self):
        self.sale(qty=2);jp=add_part(self.job,self.part,D(2),self.owner);remove_part(jp,self.owner);adjust_to_count(self.part,17,self.owner,branch=self.main)
        total=sum(StockMovement.objects.filter(branch=self.main,part=self.part).values_list('quantity',flat=True),D(0))
        self.assertEqual(total,self.qty());self.assertEqual(self.qty(branch=self.east),5)

    def test_REP_05_storekeeper_dashboard_hides_customer_job_data(self):
        self.client.force_login(self.users['storekeeper'])
        self.assertEqual(self.client.get(reverse('job_detail',args=[self.job.pk])).status_code,403)
        self.assertNotContains(self.client.get(reverse('dashboard')),'UAT Retail')

    def test_E2E_A_purchase_sale_return(self):
        l=add_po_line(self.po,self.part,D(10),D(90));self.po.status='ordered';self.po.save()
        receive_po(self.po,{l.pk:D(10)},self.owner)
        self.assertEqual(self.qty(),30)
        s=self.sale(qty=2);ret=process_return(sale=s,user=self.owner,items={s.lines.get().pk:D(1)})
        self.assertEqual(self.qty(),29);s.refresh_from_db();self.assertEqual(s.net_total,100)
        self.assertEqual(self.qty(branch=self.east),5);self.assertTrue(AuditLog.objects.filter(branch=self.main,action='po.receive').exists())

    def test_E2E_B_customer_job_invoice_via_http(self):
        r=self.client.post(reverse('customer_create'),{'name':'Journey Customer','customer_type':'retail','credit_limit':'0','is_active':'on'})
        self.assertEqual(r.status_code,302);c=Customer.objects.get(name='Journey Customer')
        self.client.post(reverse('vehicle_create',args=[c.pk]),{'reg_number':'JOURNEY','make':'Toyota','model':'Vitz'})
        v=c.vehicles.get()
        r=self.client.post(reverse('job_create'),{'customer':c.pk,'vehicle':v.pk,'complaint':'Service'})
        self.assertEqual(r.status_code,302);j=JobCard.objects.get(customer=c)
        self.client.post(reverse('job_add_part',args=[j.pk]),{'part':self.part.pk,'quantity':2});self.assertEqual(self.qty(),18)
        self.client.post(reverse('job_add_labour',args=[j.pk]),{'description':'Labour','hours':2,'rate':150})
        self.client.post(reverse('job_remove_part',args=[j.pk,j.parts.get().pk]));self.assertEqual(self.qty(),20)
        self.client.post(reverse('job_add_part',args=[j.pk]),{'part':self.part.pk,'quantity':2})
        self.client.post(reverse('job_invoice',args=[j.pk]),{'payment_method':'cash','amount_paid':500});j.refresh_from_db()
        self.assertEqual(j.invoice.total,500);self.assertEqual(self.qty(),18);self.assertEqual(j.status,'invoiced')

    def test_IMPORT_01_new_part_opening_stock(self):
        self.csv_import('sku,name,cost_price,sell_price,quantity,reorder_level\nIMPORT-1,Imported,60,100,4,2\n')
        p=Part.objects.get(tenant=self.ta,sku='IMPORT-1');self.assertEqual(self.qty(p),4)
        self.assertEqual(StockMovement.objects.get(part=p).reason,'opening')

    def test_IMPORT_02_existing_sku_no_stock_duplication(self):
        self.csv_import('sku,name,cost_price,sell_price,quantity\nUAT-FILTER,UAT Filter,60,100,20\n')
        self.assertEqual(self.qty(),20)

    def test_IMPORT_03_existing_sku_destination_branch_opening(self):
        self.csv_import('sku,name,cost_price,sell_price,quantity\nUAT-BELT,UAT Belt,120,200,4\n',branch='EAST')
        self.assertEqual(self.qty(self.belt,self.east),4,'Existing tenant SKU imported to new branch ignores requested opening stock')

    def test_IMPORT_04_bad_row_rolls_back_file(self):
        with self.assertRaises(CommandError):self.csv_import('sku,name,cost_price\nIMPORT-GOOD,Good,60\nIMPORT-BAD,Bad,garbage\n')
        self.assertFalse(Part.objects.filter(tenant=self.ta,sku='IMPORT-GOOD').exists(),'Earlier rows persist after a later row fails')

    def test_IMPORT_05_negative_values_rejected(self):
        try:self.csv_import('sku,name,cost_price,sell_price,quantity\nIMPORT-NEG,Negative,-1,-2,-3\n')
        except CommandError:pass
        self.assertFalse(Part.objects.filter(tenant=self.ta,sku='IMPORT-NEG').exists())

    def test_ADMIN_01_branch_staff_creation(self):
        r=self.client.post(reverse('branches'),{'name':'C South','code':'SOUTH','is_active':'on'});self.assertEqual(r.status_code,302)
        b=Branch.objects.get(tenant=self.ta,code='SOUTH');self.assertEqual(BranchStock.objects.get(branch=b,part=self.part).quantity_on_hand,0)
        r=self.client.post(reverse('staff'),{'username':'uat-new-staff','password':'UAT-password-long-123','role':'counter','branches':[b.pk]})
        self.assertEqual(r.status_code,302);u=User.objects.get(username='uat-new-staff');self.assertEqual(u.tenant_id,self.ta.pk);self.assertFalse(u.is_staff)

    def test_ADMIN_02_staff_foreign_branch_rejected(self):
        r=self.client.post(reverse('staff'),{'username':'uat-foreign','password':'UAT-password-long-123','role':'counter','branches':[self.other.pk]})
        self.assertEqual(r.status_code,200);self.assertFalse(User.objects.filter(username='uat-foreign').exists())

    def test_ADMIN_03_tax_and_discount_ranges(self):
        from core.views import ShopSettingsForm
        f=ShopSettingsForm({'shop_name':'Test','currency_symbol':'K','tax_name':'VAT','tax_rate':'-100','counter_max_discount':'200','default_labour_rate':'-1'},instance=self.cfg)
        self.assertFalse(f.is_valid(),'Negative tax/labour and discount above 100 are accepted')

    def test_ADMIN_04_create_tenant_command(self):
        with patch('getpass.getpass',return_value='Provisioning-password-123'):
            call_command('create_tenant',name='New UAT business',slug='provisioned-uat',owner='provisioned-owner',stdout=io.StringIO())
        u=User.objects.get(username='provisioned-owner');self.assertTrue(u.check_password('Provisioning-password-123'))
        self.assertFalse(u.is_superuser);self.assertFalse(u.is_staff)
        self.assertTrue(Branch.objects.filter(tenant=u.tenant,code='MAIN',is_active=True).exists())

    def test_ADMIN_05_seed_command_prefix_and_isolation(self):
        call_command('seed_demo',tenant=self.tb.slug,branch='MAIN',stdout=io.StringIO())
        for name in ['owner','manager','counter1','mechanic1','storekeeper1']:
            u=User.objects.get(username=f'{self.tb.slug}_{name}');self.assertEqual(u.tenant_id,self.tb.pk)
            self.assertTrue(u.check_password('partsdesk123'));self.assertTrue(u.branches.filter(pk=self.other.pk).exists())
        self.assertFalse(Part.objects.filter(tenant=self.ta,sku='BRK-1001').exists())


# Separately named tests for role/page, foreign-record, invalid payload and method checks.
PAGE_RULES={
 'dashboard':None,'part_list':None,'vehicle_list':None,
 'pos':{'owner','manager','counter'},'sale_list':{'owner','manager','counter'},
 'customer_list':{'owner','manager','counter','mechanic'},'job_list':{'owner','manager','counter','mechanic'},
 'supplier_list':{'owner','manager','storekeeper'},'po_list':{'owner','manager','storekeeper'},'movement_list':{'owner','manager','storekeeper'},
 'part_create':{'owner','manager','storekeeper'},'stock_adjust':{'owner','manager','storekeeper'},
 'reports_home':{'owner','manager'},'audit_list':{'owner','manager'},'report_sales':{'owner','manager'},
 'report_top':{'owner','manager'},'report_low_stock':{'owner','manager'},'report_slow':{'owner','manager'},
 'report_stock_value':{'owner','manager'},'report_receivables':{'owner','manager'},
 'branches':{'owner'},'staff':{'owner'},'shop_settings':{'owner'},
}
for role in ['owner','manager','counter','mechanic','storekeeper']:
 for page,allowed in PAGE_RULES.items():
    def role_test(self,role=role,page=page,allowed=allowed):
        self.client.force_login(self.users[role]);expected=200 if allowed is None or role in allowed else 403
        self.assertEqual(self.client.get(reverse(page)).status_code,expected)
    setattr(AcceptanceTests,f'test_ROLE_{role}_{page}',role_test)

for page,record in [('part_detail','part_b'),('part_edit','part_b'),('customer_detail','customer_b'),('customer_edit','customer_b'),('po_detail','po_b'),('job_detail','job_b')]:
 def foreign_test(self,page=page,record=record):self.assertEqual(self.client.get(reverse(page,args=[getattr(self,record).pk])).status_code,404)
 setattr(AcceptanceTests,f'test_ISO_foreign_url_{page}',foreign_test)

INVALID_BODIES={
 'malformed_json':'{','null':'null','array':'[]','string':'"hello"','lines_null':'{"lines":null}',
 'line_null':'{"lines":[null]}','invalid_part_id':'{"lines":[{"part_id":"bad","quantity":1}]}',
 'nonfinite_quantity':None,'nonfinite_price':None,'negative_quantity':None,'zero_quantity':None,'too_many_decimal_places':None,
}
for case,body in INVALID_BODIES.items():
 def invalid_test(self,case=case,body=body):
    c=Client(raise_request_exception=False);c.force_login(self.owner)
    if body is None:
        row={'part_id':self.part.pk,'quantity':1}
        if case=='nonfinite_quantity':row['quantity']='NaN'
        if case=='nonfinite_price':row['unit_price']='Infinity'
        if case=='negative_quantity':row['quantity']=-1
        if case=='zero_quantity':row['quantity']=0
        if case=='too_many_decimal_places':row['quantity']='0.001'
        body=json.dumps({'request_id':str(uuid4()),'lines':[row]})
    else:
        # Keep invalid line tests meaningful under the new checkout identity contract.
        try:
            decoded=json.loads(body)
            if isinstance(decoded,dict):
                decoded['request_id']=str(uuid4());body=json.dumps(decoded)
        except ValueError:
            pass
    r=c.post(reverse('pos_checkout'),body,content_type='application/json')
    self.assertEqual(r.status_code,400,f'Invalid payload {case} must fail cleanly: got {r.status_code}')
    self.assertEqual(Sale.objects.count(),0);self.assertEqual(self.qty(),20)
 setattr(AcceptanceTests,f'test_INPUT_{case}',invalid_test)

for page,args in [('pos_checkout',None),('switch_branch',None),('job_cancel','job'),('job_invoice','job'),('po_receive','po'),('po_status','po'),('po_from_low_stock',None)]:
 def get_test(self,page=page,args=args):self.assertEqual(self.client.get(reverse(page,args=[getattr(self,args).pk] if args else None)).status_code,405)
 setattr(AcceptanceTests,f'test_HTTP_GET_{page}',get_test)

for report in ['report_sales','report_top','report_low_stock','report_slow','report_stock_value','report_receivables']:
 def csv_test(self,report=report):
    r=self.client.get(reverse(report),{'csv':1});self.assertEqual(r.status_code,200);self.assertIn('text/csv',r['Content-Type'])
 setattr(AcceptanceTests,f'test_REPORT_CSV_{report}',csv_test)

for page,query in [('job_create',{'customer':'bad'}),('stock_adjust',{'part':'bad'}),('movement_list',{'part':'bad'}),('report_slow',{'days':'9999999999999999999999999999'})]:
 def query_test(self,page=page,query=query):
    c=Client(raise_request_exception=False);c.force_login(self.owner)
    r=c.get(reverse(page),query)
    self.assertLess(r.status_code,500,'Malformed query parameter should not cause an unhandled server error')
 setattr(AcceptanceTests,f'test_INPUT_query_{page}',query_test)
