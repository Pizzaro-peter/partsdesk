"""Excel catalogue acceptance tests using independent OOXML fixtures."""
from datetime import timedelta
from io import BytesIO
from unittest.mock import patch
from xml.sax.saxutils import escape
from zipfile import ZipFile, ZIP_DEFLATED
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook
import uat_regression as baseline
from catalog.excel_import import parse_workbook, validate_rows, ImportProblem, HEADERS
from catalog.models import Part, BranchStock, Category, Supplier, Vehicle, CatalogueImport
from core.models import AuditLog
from stock.models import StockMovement
from stock.services import record_movement, StockError


def xlsx(rows, headers=None, extras=None):
    headers = headers or ['sku','name','sell_price','quantity']
    def letters(n):
        s=''
        while n: n,r=divmod(n-1,26);s=chr(65+r)+s
        return s
    xmlrows=[]
    for number, values in enumerate([headers]+rows,1):
        cells=[]
        for col,value in enumerate(values,1):
            if value is None: continue
            addr=f'{letters(col)}{number}'
            if isinstance(value,tuple):
                kind,value=value
                body=f'<f>{escape(str(value))}</f><v>1</v>' if kind=='formula' else f'<v>{escape(str(value))}</v>'
                cells.append(f'<c r="{addr}" t="{"n" if kind=="formula" else kind}">{body}</c>')
            elif isinstance(value,(int,float)):
                cells.append(f'<c r="{addr}" t="n"><v>{value}</v></c>')
            else: cells.append(f'<c r="{addr}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>')
        xmlrows.append(f'<row r="{number}">{"".join(cells)}</row>')
    files={
      '[Content_Types].xml':'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
      '_rels/.rels':'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
      'xl/workbook.xml':'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Catalogue" sheetId="1" r:id="rId1"/></sheets></workbook>',
      'xl/_rels/workbook.xml.rels':'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
      'xl/worksheets/sheet1.xml':'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'+''.join(xmlrows)+'</sheetData></worksheet>'}
    files.update(extras or {});out=BytesIO()
    with ZipFile(out,'w',ZIP_DEFLATED) as z:
        for name,value in files.items():z.writestr(name,value)
    return out.getvalue()


def upload(content,name='parts.xlsx'):
    return SimpleUploadedFile(name,content,content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


class ExcelCatalogueTests(TestCase):
    @classmethod
    def setUpTestData(cls):baseline.AcceptanceTests.setUpTestData.__func__(cls)
    setUp=baseline.AcceptanceTests.setUp
    qty=baseline.AcceptanceTests.qty
    def preview(self,rows=None,headers=None,mode='create'):
        r=self.client.post(reverse('catalogue_upload'),{'mode':mode,'file':upload(xlsx(rows or [['NEW-01','New filter',100,5]],headers))})
        self.assertEqual(r.status_code,302,r.content.decode()[:1500]);return CatalogueImport.objects.latest('pk')
    def confirm(self,b):return self.client.post(reverse('catalogue_confirm',args=[b.pk]),follow=True)
    def rejected(self,rows,headers=None,mode='create'):
        r=self.client.post(reverse('catalogue_upload'),{'mode':mode,'file':upload(xlsx(rows,headers))})
        self.assertEqual(r.status_code,200);self.assertFalse(CatalogueImport.objects.exists());return r
    def test_preview_no_catalogue_side_effects(self):
        models=(Part,Category,Supplier,Vehicle,StockMovement);counts=[m.objects.count() for m in models]
        b=self.preview([['NEW-01','New',100,5,'New category','New supplier']],['sku','name','sell_price','quantity','category','preferred_supplier'])
        self.assertEqual(counts,[m.objects.count() for m in models])
        r=self.client.get(reverse('catalogue_preview',args=[b.pk]));self.assertContains(r,'Confirm import');self.assertContains(r,self.main.name)
    def test_confirm_creates_catalogue_fitment_branch_opening_and_audit(self):
        heads=['sku','name','sell_price','quantity','cost_price','barcode','oem_number','category','preferred_supplier','vehicle_make','vehicle_model','year_from','year_to','engine','is_universal','is_active']
        b=self.preview([['0001','Oil filter',100,5,60,'000123','90915','Filters','Supplier A','Toyota','Vitz',2005,2010,'1.3L','no','yes']],heads)
        self.assertContains(self.confirm(b),'Import completed.');p=Part.objects.get(tenant=self.ta,sku='0001')
        self.assertEqual(p.barcode,'000123');self.assertEqual(p.preferred_supplier,self.supplier);self.assertEqual(p.category.tenant,self.ta)
        self.assertFalse(p.is_universal);self.assertTrue(p.is_active);self.assertEqual(p.fits.get().model,'Vitz')
        self.assertEqual(self.qty(p),5);self.assertEqual(self.qty(p,self.east),0)
        self.assertFalse(BranchStock.objects.filter(part=p,branch=self.other).exists())
        self.assertEqual(AuditLog.objects.filter(action='catalog.import',branch=self.main).count(),1)
    def test_replay_idempotent(self):
        b=self.preview();self.confirm(b);self.confirm(b)
        self.assertEqual(Part.objects.filter(sku='NEW-01').count(),1);self.assertEqual(self.qty(Part.objects.get(sku='NEW-01')),5)
        self.assertEqual(AuditLog.objects.filter(action='catalog.import').count(),1)
        self.assertEqual(StockMovement.objects.filter(reference=f'IMPORT-{b.pk}').count(),1)
    def test_update_preserves_blank_values_stock_and_average_cost(self):
        b=self.preview([['UAT-FILTER','Renamed',110,99,70,None,None]],['sku','name','sell_price','quantity','cost_price','trade_price','preferred_supplier'],mode='update')
        self.assertContains(self.client.get(reverse('catalogue_preview',args=[b.pk])),'will be skipped');self.confirm(b);self.part.refresh_from_db()
        self.assertEqual(self.part.trade_price,90);self.assertEqual(self.part.preferred_supplier,self.supplier);self.assertEqual(self.part.cost_price,70)
        self.assertEqual(self.qty(),20);self.assertEqual(BranchStock.objects.get(part=self.part,branch=self.main).cost_price,60)
    def test_add_only_existing_sku_rejected(self):self.assertContains(self.rejected([['UAT-FILTER','Updated',100,0]]),'already exists')
    def test_invalid_row_blocks_entire_file(self):
        self.assertContains(self.rejected([['NEW-01','Good',100,0],['BAD','Bad',-1,0]]),'Row 3');self.assertFalse(Part.objects.filter(sku='NEW-01').exists())
    def test_duplicate_sku_and_barcode_rejected(self):
        self.assertContains(self.rejected([['abc','First',10,0],['ABC','Second',10,0]]),'duplicate SKU')
        self.assertContains(self.rejected([['A','First',10,'001'],['B','Second',10,'001']],['sku','name','sell_price','barcode']),'barcode')
        self.assertContains(self.rejected([['A','First',10,'UAT-123']],['sku','name','sell_price','barcode']),'already assigned')
    def test_other_tenant_barcode_no_conflict(self):
        self.part_b.barcode='OTHER-CODE';self.part_b.save();b=self.preview([['NEW-01','New',100,'OTHER-CODE']],['sku','name','sell_price','barcode']);self.confirm(b)
        self.assertEqual(Part.objects.filter(barcode='OTHER-CODE').count(),2)
    def test_numeric_identifiers_rejected(self):
        for key in ('sku','barcode','oem_number'):
            heads=['sku','name','sell_price']+([] if key=='sku' else [key]);values=[123 if key=='sku' else 'NEW-01','New',100]+([] if key=='sku' else [123])
            with self.subTest(key=key),self.assertRaisesRegex(ImportProblem,'Text'):parse_workbook(upload(xlsx([values],heads)))
    def test_formulas_and_errors_rejected(self):
        for val in [('formula','1+1'),('e','#DIV/0!')]:
            with self.subTest(val=val),self.assertRaisesRegex(ImportProblem,'formula or Excel error'):parse_workbook(upload(xlsx([['A','Name',val,0]])))
    def test_required_fields_and_zero_price(self):
        for values in [['A',None,10,0],['A','New',None,0]]:self.rejected([values])
        b=self.preview([['NEW-01','Free',0,0]]);self.confirm(b);self.assertEqual(Part.objects.get(sku='NEW-01').sell_price,0)
    def test_decimal_validation(self):
        for key in ('sell_price','quantity','cost_price','reorder_qty'):
            for bad in ('-1','NaN','Infinity','1.234','10000000000'):
                with self.subTest(key=key,bad=bad),self.assertRaises(ImportProblem):validate_rows([dict(_row=2,sku='A',name='New',**{'sell_price':'100',key:bad})],self.ta,mode='create')
    def test_choices_and_boolean_validation(self):
        for key,val in [('condition','recon'),('part_type','generic'),('is_active','maybe')]:
            with self.subTest(key=key),self.assertRaises(ImportProblem):validate_rows([dict(_row=2,sku='A',name='New',sell_price='10',**{key:val})],self.ta,mode='create')
        b=self.preview([['NEW-01','New',100,'refurbished','no','no']],['sku','name','sell_price','condition','is_active','is_universal']);self.confirm(b)
        p=Part.objects.get(sku='NEW-01');self.assertEqual(p.condition,'refurbished');self.assertFalse(p.is_active);self.assertFalse(p.is_universal)
    def test_fitment_validation_and_preservation(self):
        for fit in [{'vehicle_make':'Toyota'},{'vehicle_make':'Toyota','vehicle_model':'Vitz','year_from':'2010','year_to':'2005'},{'vehicle_make':'Toyota','vehicle_model':'Vitz','year_from':'2000.5','year_to':'2010'}]:
            with self.subTest(fit=fit),self.assertRaises(ImportProblem):validate_rows([dict(_row=2,sku='A',name='New',sell_price='10',**fit)],self.ta,mode='create')
        v=Vehicle.objects.create(tenant=self.ta,make='Toyota',model='Mark X',year_from=2004,year_to=2009);self.part.fits.add(v)
        b=self.preview([['UAT-FILTER','Filter','Toyota','Vitz',2005,2010]],['sku','name','vehicle_make','vehicle_model','year_from','year_to'],mode='update');self.confirm(b);self.assertEqual(self.part.fits.count(),2)
    def test_opening_once_and_depleted_stock_not_reopened(self):
        p=Part.objects.create(tenant=self.ta,sku='UNUSED',name='Unused',sell_price=10)
        for i in range(2):b=self.preview([['UNUSED','Unused',10,4]],mode='update');self.confirm(b)
        self.assertEqual(self.qty(p),4);self.assertEqual(StockMovement.objects.filter(part=p).count(),1)
        record_movement(self.part,-20,'adjustment',self.owner,branch=self.main)
        b=self.preview([['UAT-FILTER','Filter',100,20]],mode='update');self.confirm(b);self.assertEqual(self.qty(),0)
    def test_revalidation_after_preview(self):
        b=self.preview([['NEW-01','First',10,'UNIQUE'],['NEW-02','Second',10,'SECOND']],['sku','name','sell_price','barcode'])
        self.part.barcode='SECOND';self.part.save();self.assertContains(self.confirm(b),'No catalogue changes were saved')
        self.assertFalse(Part.objects.filter(sku__startswith='NEW-').exists());b.refresh_from_db();self.assertEqual(b.status,'ready')
    def test_mid_import_failure_rolls_back_all_rows(self):
        b=self.preview([['NEW-01','First',10,5],['NEW-02','Second',10,6]]);calls=[]
        def movement(*args,**kwargs):
            calls.append(1)
            if len(calls)==2:raise StockError('Simulated stock failure')
            return record_movement(*args,**kwargs)
        with patch('catalog.excel_import.record_movement',side_effect=movement):self.assertContains(self.confirm(b),'No catalogue changes were saved')
        self.assertFalse(Part.objects.filter(sku__startswith='NEW-').exists());self.assertFalse(StockMovement.objects.filter(reference=f'IMPORT-{b.pk}').exists())
        b.refresh_from_db();self.assertEqual(b.status,'ready')
    def test_expiry_and_cancel(self):
        b=self.preview();CatalogueImport.objects.filter(pk=b.pk).update(created_at=timezone.now()-timedelta(hours=2));self.assertContains(self.confirm(b),'expired')
        b=self.preview();self.client.post(reverse('catalogue_cancel',args=[b.pk]));self.confirm(b);b.refresh_from_db();self.assertEqual(b.status,'cancelled');self.assertFalse(Part.objects.filter(sku='NEW-01').exists())
    def test_tenant_branch_uploader_isolation(self):
        b=self.preview()
        for user,branch in [(self.owner_b,self.other),(self.users['manager'],self.main),(self.owner,self.east)]:
            self.client.force_login(user);s=self.client.session;s['branch_id']=branch.pk;s.save()
            for name in ('catalogue_preview','catalogue_confirm','catalogue_cancel'):
                call=self.client.get if name=='catalogue_preview' else self.client.post
                self.assertEqual(call(reverse(name,args=[b.pk])).status_code,404)
    def test_role_permissions(self):
        for role in ('counter','mechanic'):
            self.client.force_login(self.users[role]);self.assertEqual(self.client.get(reverse('catalogue_upload')).status_code,403);self.assertEqual(self.client.get(reverse('catalogue_template')).status_code,403)
            self.assertNotContains(self.client.get(reverse('part_list')),'Upload catalogue')
        for role in ('owner','manager','storekeeper'):
            self.client.force_login(self.users[role]);self.assertEqual(self.client.get(reverse('catalogue_upload')).status_code,200)
    def test_post_only_and_csrf(self):
        b=self.preview()
        for name in ('catalogue_confirm','catalogue_cancel'):self.assertEqual(self.client.get(reverse(name,args=[b.pk])).status_code,405)
        c=Client(enforce_csrf_checks=True);c.force_login(self.owner);self.assertEqual(c.post(reverse('catalogue_confirm',args=[b.pk])).status_code,403)
        c.get(reverse('catalogue_preview',args=[b.pk]));self.assertEqual(c.post(reverse('catalogue_confirm',args=[b.pk]),{'csrfmiddlewaretoken':c.cookies['csrftoken'].value}).status_code,302)
        self.assertEqual(self.qty(Part.objects.get(sku='NEW-01')),5)
    def test_headers_empty_and_unheaded_cells(self):
        for heads,rows in [(['sku','sku','name'],[['A','B','Name']]),(['sku','name','unknown'],[['A','Name',1]]),(['name'],[['Name']]),(['sku','name'],[]),(['sku','name'],[['A','Name','rogue']])]:
            with self.subTest(heads=heads),self.assertRaises(ImportProblem):parse_workbook(upload(xlsx(rows,heads)))
        r=parse_workbook(upload(xlsx([['A','Name',100,5]],['part code','part name','retail price','opening stock'])));self.assertEqual(r[0]['quantity'],'5')
    def test_extension_corruption_and_size_limits(self):
        for f in [upload(b'invalid'),upload(b'invalid','old.xls'),upload(b'x'*(5*1024*1024+1))]:
            with self.subTest(name=f.name),self.assertRaises(ImportProblem):parse_workbook(f)
    def test_macros_external_links_xml_coordinates_merges_entities(self):
        for name in ('xl/vbaProject.bin','xl/externalLinks/externalLink1.xml'):
            with self.subTest(name=name),self.assertRaises(ImportProblem):parse_workbook(upload(xlsx([['A','Name',100,0]],extras={name:'payload'})))
        for inner in ['<sheetData><row r="5002"/></sheetData>','<sheetData><row r="2"><c r="BM2"/></row></sheetData>','<sheetData/><mergeCells><mergeCell ref="A2:B2"/></mergeCells>']:
            xml='<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'+inner+'</worksheet>'
            with self.subTest(xml=xml),self.assertRaises(ImportProblem):parse_workbook(upload(xlsx([],extras={'xl/worksheets/sheet1.xml':xml})))
        xml='<!DOCTYPE worksheet [<!ENTITY unsafe "secret">]><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>&unsafe;</sheetData></worksheet>'
        with self.assertRaises(ImportProblem):parse_workbook(upload(xlsx([],extras={'xl/worksheets/sheet1.xml':xml})))
    def test_template_download(self):
        r=self.client.get(reverse('catalogue_template'));self.assertEqual(r.status_code,200);book=load_workbook(BytesIO(b''.join(r.streaming_content)));s=book['Catalogue']
        self.assertEqual([c.value for c in s[1]],HEADERS);self.assertTrue(all(c.value is None for c in s[2]));self.assertEqual(s['A2'].number_format,'@');self.assertEqual(s['N2'].number_format,'@')
        self.assertTrue(any('refurbished' in (dv.formula1 or '') for dv in s.data_validations.dataValidation));book.close()
    def test_upload_form_required_file_valid_mode(self):
        for data in [{'mode':'create'},{'mode':'bogus','file':upload(xlsx([['A','Name',10,0]]))}]:self.assertEqual(self.client.post(reverse('catalogue_upload'),data).status_code,200)
        self.assertFalse(CatalogueImport.objects.exists())

    def test_maximum_rows_and_unpacked_size_limit(self):
        rows=parse_workbook(upload(xlsx([[f'SKU-{i}','Part',10,0] for i in range(5000)])))
        self.assertEqual(len(rows),5000);self.assertEqual(rows[-1]['_row'],5001)
        with self.assertRaisesRegex(ImportProblem,'too large when unpacked'):
            parse_workbook(upload(xlsx([],extras={'large.xml':'x'*(20*1024*1024)})))

    def test_company_name_and_tenant_owned_related_records(self):
        self.cfg.shop_name='Peter Motor Parts';self.cfg.save()
        self.assertContains(self.client.get(reverse('catalogue_upload')),'Peter Motor Parts')
        Category.objects.create(tenant=self.tb,name='Private category')
        b=self.preview([['NEW-01','New',100,'Private category','Supplier B']],['sku','name','sell_price','category','preferred_supplier'])
        self.assertContains(self.client.get(reverse('catalogue_preview',args=[b.pk])),'Peter Motor Parts')
        self.confirm(b);p=Part.objects.get(sku='NEW-01')
        self.assertEqual(p.category.tenant,self.ta);self.assertEqual(p.preferred_supplier.tenant,self.ta)
