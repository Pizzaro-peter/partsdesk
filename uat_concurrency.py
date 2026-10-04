from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from decimal import Decimal as D
from uuid import uuid4
from django.test import TransactionTestCase
from django.db import close_old_connections
from core.models import Tenant,Branch,User,ShopSettings
from catalog.models import Part,BranchStock
from sales.models import CheckoutRequest,Customer,CustomerVehicle,Sale
from sales.services import checkout_once,create_sale,add_payment,SaleError
from stock.services import record_movement,StockError
from workshop.services import create_job,add_part,remove_part
from workshop.models import JobPart

class ConcurrentTransactions(TransactionTestCase):
    def setUp(self):
        self.tenant=Tenant.objects.create(name='Concurrent',slug='concurrent')
        self.branch=Branch.objects.create(tenant=self.tenant,name='Main',code='MAIN')
        ShopSettings.objects.create(tenant=self.tenant,tax_rate=0)
        self.owner=User.objects.create_user(username='concurrent-owner',tenant=self.tenant,role='owner')
        self.part=Part.objects.create(tenant=self.tenant,sku='LAST',name='Last filter',sell_price=100)
        record_movement(self.part,1,'opening',self.owner,branch=self.branch)
        self.customer=Customer.objects.create(tenant=self.tenant,name='Credit customer',credit_limit=1000)

    def parallel(self,fn):
        gate=Barrier(2)
        def run(i):
            close_old_connections()
            try:gate.wait(timeout=10);return fn(i)
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:return list(pool.map(run,[0,1]))

    def test_last_unit_sold_once(self):
        def sell(i):
            try:
                create_sale(user=self.owner,branch=self.branch,lines=[dict(part=self.part,quantity=1,unit_price=100)])
                return 'sold'
            except StockError:return 'rejected'
        self.assertCountEqual(self.parallel(sell),['sold','rejected'])
        self.assertEqual(Sale.objects.filter(branch=self.branch).count(),1)
        self.assertEqual(BranchStock.objects.get(branch=self.branch,part=self.part).quantity_on_hand,0)

    def test_payment_cannot_exceed_balance_in_parallel(self):
        sale=create_sale(user=self.owner,branch=self.branch,lines=[dict(part=self.part,quantity=1,unit_price=100)],customer=self.customer,payment_method='credit',amount_paid=0)
        def pay(i):
            try:add_payment(sale=sale,amount=D(70),method='cash',user=self.owner);return 'paid'
            except SaleError:return 'rejected'
        self.assertCountEqual(self.parallel(pay),['paid','rejected'])
        sale.refresh_from_db();self.assertEqual(sale.amount_paid,70)

    def test_prefetched_part_removal_does_not_restore_twice(self):
        vehicle=CustomerVehicle.objects.create(customer=self.customer,reg_number='RACE',make='Toyota',model='Vitz')
        job=create_job(user=self.owner,branch=self.branch,customer=self.customer,vehicle=vehicle,complaint='Race')
        jp=add_part(job,self.part,D(1),self.owner)
        copies=[JobPart.objects.get(pk=jp.pk),JobPart.objects.get(pk=jp.pk)]
        def remove(i):
            try:remove_part(copies[i],self.owner);return 'removed'
            except (JobPart.DoesNotExist,SaleError):return 'rejected'
        self.assertCountEqual(self.parallel(remove), ['removed','rejected'])
        self.assertEqual(BranchStock.objects.get(branch=self.branch,part=self.part).quantity_on_hand,1,'Concurrent prefetched removals restored 2 units from 1 issued unit')
        from stock.models import StockMovement
        self.assertEqual(StockMovement.objects.filter(branch=self.branch, part=self.part, reason='job_return').count(),1)

    def test_same_checkout_key_in_parallel_creates_one_invoice(self):
        key=uuid4()
        def submit(i):
            sale=checkout_once(user=self.owner,branch=self.branch,key=key,payload_hash='a'*64,
                create=lambda:create_sale(user=self.owner,branch=self.branch,lines=[dict(part=self.part,quantity=1,unit_price=100)]))
            return sale.pk
        ids=self.parallel(submit)
        self.assertEqual(ids[0],ids[1])
        self.assertEqual(Sale.objects.filter(branch=self.branch).count(),1)
        self.assertEqual(CheckoutRequest.objects.filter(branch=self.branch).count(),1)
        self.assertEqual(BranchStock.objects.get(branch=self.branch,part=self.part).quantity_on_hand,0)

    def test_same_catalogue_confirmation_in_parallel_imports_once(self):
        from catalog.models import CatalogueImport
        from catalog.excel_import import confirm_import
        from stock.models import StockMovement
        from core.models import AuditLog
        batch=CatalogueImport.objects.create(tenant=self.tenant,branch=self.branch,created_by=self.owner,
            file_name='parts.xlsx',mode='create',rows=[{'_row':2,'sku':'EXCEL','name':'Excel filter','sell_price':'100','quantity':'4'}])
        results=self.parallel(lambda i:confirm_import(batch,self.owner))
        self.assertEqual(results[0],results[1])
        self.assertEqual(Part.objects.filter(tenant=self.tenant,sku='EXCEL').count(),1)
        part=Part.objects.get(tenant=self.tenant,sku='EXCEL')
        self.assertEqual(BranchStock.objects.get(branch=self.branch,part=part).quantity_on_hand,4)
        self.assertEqual(StockMovement.objects.filter(reference=f'IMPORT-{batch.pk}').count(),1)
        self.assertEqual(AuditLog.objects.filter(action='catalog.import').count(),1)
