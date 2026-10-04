"""Run migration preservation check against a separate disposable SQLite database."""
import os,tempfile,json
from pathlib import Path
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
from django.conf import settings
scratch=tempfile.TemporaryDirectory(prefix='partsdesk-upgrade-')
settings.DATABASES['default']['NAME']=str(Path(scratch.name)/'upgrade.sqlite3')
import django
django.setup()
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
executor=MigrationExecutor(connection)
initial=[('core','0001_initial'),('catalog','0001_initial'),('sales','0001_initial'),('stock','0001_initial'),('workshop','0001_initial')]
executor.migrate(initial)
apps=executor.loader.project_state(initial).apps
U=apps.get_model('core','User');P=apps.get_model('catalog','Part')
u=U.objects.create(username='legacy-owner',role='owner',is_staff=True,is_superuser=True,password='!')
apps.get_model('core','ShopSettings').objects.create(shop_name='Legacy shop',currency_symbol='K')
p=P.objects.create(sku='LEGACY',name='Legacy filter',quantity_on_hand=7,cost_price=60,sell_price=100,reorder_level=2,reorder_qty=10,bin_location='A-1')
apps.get_model('stock','StockMovement').objects.create(part=p,quantity=7,quantity_after=7,reason='opening',user=u)
c=apps.get_model('sales','Customer').objects.create(name='Legacy customer',credit_limit=1000)
apps.get_model('sales','Sale').objects.create(number='INV-LEGACY',customer=c,total=200,amount_paid=50,created_by=u)
supplier=apps.get_model('catalog','Supplier').objects.create(name='Legacy supplier')
apps.get_model('stock','PurchaseOrder').objects.create(number='PO-LEGACY',supplier=supplier,created_by=u)
vehicle=apps.get_model('sales','CustomerVehicle').objects.create(customer=c,reg_number='LEGACY',make='Toyota',model='Vitz')
apps.get_model('workshop','JobCard').objects.create(number='JOB-LEGACY',customer=c,vehicle=vehicle,complaint='Repair',created_by=u)
executor=MigrationExecutor(connection);executor.migrate(executor.loader.graph.leaf_nodes())
from core.models import Tenant,Branch,User
from catalog.models import BranchStock,Part
from sales.models import Sale
from stock.models import StockMovement,PurchaseOrder
from workshop.models import JobCard
t=Tenant.objects.get(slug='original-business');b=Branch.objects.get(tenant=t,code='MAIN');item=BranchStock.objects.get(branch=b,part__sku='LEGACY')
assert (item.quantity_on_hand,item.cost_price,item.reorder_level,item.reorder_qty,item.bin_location)==(7,60,2,10,'A-1')
owner=User.objects.get(username='legacy-owner');assert owner.tenant_id==t.pk and not owner.is_staff and not owner.is_superuser
assert (Sale.objects.get().total,Sale.objects.get().amount_paid,Sale.objects.get().branch_id)==(200,50,b.pk)
assert StockMovement.objects.get().branch_id==b.pk and PurchaseOrder.objects.get().branch_id==b.pk and JobCard.objects.get().branch_id==b.pk
result={'test':'UPGRADE_01_legacy_data_preserved','result':'Pass','details':'Legacy stock, branch cost, reorder fields, ownership, invoice totals and transaction branch links preserved.'}
out=Path(os.environ.get('PARTSDESK_UAT_OUTPUT',str(Path(__file__).resolve().parent/'uat_results')));out.mkdir(exist_ok=True);(out/'upgrade-results.json').write_text(json.dumps([result],indent=2))
print(result)
connection.close();scratch.cleanup()
