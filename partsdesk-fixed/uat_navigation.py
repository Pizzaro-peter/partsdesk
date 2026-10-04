"""Focused checks for parent navigation and shared header rendering."""
from types import SimpleNamespace
from html.parser import HTMLParser
from django.test import TestCase, RequestFactory
from django.urls import reverse, resolve
import uat_regression as baseline
from core.navigation import page_back_link, PARENTS, RECORD_PARENTS

class BackLinks(HTMLParser):
    def __init__(self):super().__init__();self.links=[]
    def handle_starttag(self,tag,attrs):
        d=dict(attrs)
        if tag=='a' and d.get('aria-label','').startswith('Back to '):self.links.append(d)

class NavigationTests(TestCase):
    @classmethod
    def setUpTestData(cls):baseline.AcceptanceTests.setUpTestData.__func__(cls)
    setUp=baseline.AcceptanceTests.setUp
    def test_named_parent_map(self):
        for name,(target,label) in PARENTS.items():
            request=SimpleNamespace(user=self.owner,resolver_match=SimpleNamespace(url_name=name,kwargs={}))
            with self.subTest(name=name):self.assertEqual(page_back_link(request),dict(url=reverse(target),label=label))
        for name,(target,label) in RECORD_PARENTS.items():
            request=SimpleNamespace(user=self.owner,resolver_match=SimpleNamespace(url_name=name,kwargs={'pk':42}))
            with self.subTest(name=name):self.assertEqual(page_back_link(request),dict(url=reverse(target,kwargs={'pk':42}),label=label))
    def test_customer_and_catalogue_vehicle_parents(self):
        for kwargs,target in [({},'vehicle_list'),({'pk':42},'customer_detail')]:
            r=SimpleNamespace(user=self.owner,resolver_match=SimpleNamespace(url_name='vehicle_create',kwargs=kwargs))
            self.assertEqual(page_back_link(r)['url'],reverse(target,kwargs=kwargs))
    def test_dashboard_and_anonymous_no_back_link(self):
        self.assertIsNone(page_back_link(None))
        self.assertIsNone(page_back_link(SimpleNamespace(user=SimpleNamespace(is_authenticated=False))))
        self.assertIsNone(page_back_link(SimpleNamespace(user=self.owner,resolver_match=SimpleNamespace(url_name='dashboard'))))
    def test_direct_pages_show_one_get_link_outside_forms(self):
        pages=[('part_detail',{'pk':self.part.pk},'part_list'),('part_edit',{'pk':self.part.pk},'part_detail'),('customer_detail',{'pk':self.retail.pk},'customer_list'),('customer_edit',{'pk':self.retail.pk},'customer_detail'),('job_detail',{'pk':self.job.pk},'job_list'),('job_create',{},'job_list'),('po_detail',{'pk':self.po.pk},'po_list'),('stock_adjust',{},'movement_list'),('report_sales',{},'reports_home'),('catalogue_upload',{},'part_list'),('supplier_create',{},'supplier_list')]
        for name,kwargs,target in pages:
            with self.subTest(name=name):
                r=self.client.get(reverse(name,kwargs=kwargs),HTTP_REFERER='https://external.example/')
                self.assertEqual(r.status_code,200);parser=BackLinks();parser.feed(r.content.decode())
                self.assertEqual(len(parser.links),1);d=parser.links[0]
                self.assertEqual(d['href'],reverse(target,kwargs=kwargs if target in ('part_detail','customer_detail') else {}))
                self.assertIn('no-print',d['class']);self.assertNotIn('onclick',d)
    def test_primary_sections_return_to_dashboard(self):
        for name in ['part_list','customer_list','sale_list','job_list','po_list','movement_list','reports_home','branches','staff','shop_settings','pos','password_change','password_change_done']:
            with self.subTest(name=name):
                r=self.client.get(reverse(name));self.assertEqual(r.status_code,200)
                parser=BackLinks();parser.feed(r.content.decode());self.assertEqual(parser.links[0]['href'],reverse('dashboard'))
    def test_fallback_ignores_referrer_and_query(self):
        r=RequestFactory().get('/unknown/?next=https://external.example/',HTTP_REFERER='https://external.example/')
        r.user=self.owner;r.resolver_match=None
        self.assertEqual(page_back_link(r),dict(url=reverse('dashboard'),label='Back to dashboard'))

    def test_invoice_return_and_catalogue_preview_destinations(self):
        from catalog.models import CatalogueImport
        sale=baseline.AcceptanceTests.sale(self)
        batch=CatalogueImport.objects.create(tenant=self.ta,branch=self.main,created_by=self.owner,
            file_name='parts.xlsx',mode='create',rows=[{'_row':2,'sku':'BACK-TEST','name':'Back test','sell_price':'10'}])
        for name,pk,target,kwargs in [('sale_detail',sale.pk,'sale_list',{}),('sale_return',sale.pk,'sale_detail',{'pk':sale.pk}),('catalogue_preview',batch.pk,'catalogue_upload',{})]:
            with self.subTest(name=name):
                r=self.client.get(reverse(name,kwargs={'pk':pk}));self.assertEqual(r.status_code,200)
                parser=BackLinks();parser.feed(r.content.decode());self.assertEqual(len(parser.links),1)
                self.assertEqual(parser.links[0]['href'],reverse(target,kwargs=kwargs))
