"""Focused regression checks for the corrected defects and checkout request contract."""
import csv
import io
import json
import re
from decimal import Decimal as D
from uuid import uuid4

from django.core.management.base import CommandError
from django.test import Client, TestCase
from django.urls import reverse

import uat_regression as baseline
from core.utils import csv_response
from sales.models import CheckoutRequest, Sale
from sales.services import SaleError, add_payment, create_sale
from stock.models import StockMovement
from stock.services import StockError, record_movement
from workshop.services import add_part


class FixRegressionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        baseline.AcceptanceTests.setUpTestData.__func__(cls)

    setUp = baseline.AcceptanceTests.setUp
    checkout = baseline.AcceptanceTests.checkout
    qty = baseline.AcceptanceTests.qty
    csv_import = baseline.AcceptanceTests.csv_import
    sale = baseline.AcceptanceTests.sale

    def test_FIX_01_checkout_requires_request_identity(self):
        response = self.checkout(request_id=None)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Sale.objects.count(), 0)

    def test_FIX_02_invalid_request_identity_rejected(self):
        self.assertEqual(self.checkout(request_id='not-a-uuid').status_code, 400)
        self.assertEqual(CheckoutRequest.objects.count(), 0)

    def test_FIX_03_replay_returns_same_invoice_and_deducts_once(self):
        key = str(uuid4())
        first = self.checkout(request_id=key)
        second = self.checkout(request_id=key)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.json(), first.json())
        self.assertEqual((Sale.objects.count(), CheckoutRequest.objects.count(), self.qty()), (1, 1, 19))

    def test_FIX_04_replay_is_persistent_across_sessions(self):
        key = str(uuid4())
        first = self.checkout(request_id=key)
        self.client.logout()
        self.client.force_login(self.owner)
        self.assertEqual(self.checkout(request_id=key).json(), first.json())
        self.assertEqual(Sale.objects.count(), 1)

    def test_FIX_05_replay_uses_historical_invoice_after_catalogue_changes(self):
        key = str(uuid4())
        first = self.checkout(request_id=key)
        self.part.sell_price = 200
        self.part.is_active = False
        self.part.save()
        self.assertEqual(self.checkout(request_id=key).json(), first.json())
        self.assertEqual(Sale.objects.get().total, 100)

    def test_FIX_06_changed_ticket_cannot_reuse_committed_request(self):
        key = str(uuid4())
        self.checkout(request_id=key)
        response = self.checkout(request_id=key, lines=[{'part_id': self.part.pk, 'quantity': 2}])
        self.assertEqual(response.status_code, 409)
        self.assertEqual((Sale.objects.count(), self.qty()), (1, 19))

    def test_FIX_07_new_keys_allow_two_identical_legitimate_sales(self):
        self.assertEqual(self.checkout().status_code, 200)
        self.assertEqual(self.checkout().status_code, 200)
        self.assertEqual((Sale.objects.count(), self.qty()), (2, 18))

    def test_FIX_08_failed_checkout_rolls_back_request_and_stock(self):
        key = str(uuid4())
        response = self.checkout(request_id=key, lines=[{'part_id': self.part.pk, 'quantity': 21}])
        self.assertEqual(response.status_code, 400)
        self.assertEqual((Sale.objects.count(), CheckoutRequest.objects.count(), self.qty()), (0, 0, 20))
        self.assertEqual(self.checkout(request_id=key).status_code, 200)
        self.assertEqual((Sale.objects.count(), self.qty()), (1, 19))

    def test_FIX_09_request_scope_is_branch_specific(self):
        key = str(uuid4())
        self.checkout(request_id=key)
        self.client.post(reverse('switch_branch'), {'branch_id': self.east.pk})
        self.assertEqual(self.checkout(request_id=key).status_code, 200)
        self.assertEqual((Sale.objects.count(), self.qty(), self.qty(branch=self.east)), (2, 19, 4))

    def test_FIX_10_request_scope_is_user_specific(self):
        key = str(uuid4())
        self.checkout(request_id=key)
        self.client.force_login(self.users['manager'])
        self.assertEqual(self.checkout(request_id=key).status_code, 200)
        self.assertEqual(Sale.objects.count(), 2)

    def test_FIX_11_null_notes_fail_cleanly(self):
        self.assertEqual(self.checkout(notes=None).status_code, 400)
        self.assertEqual(Sale.objects.count(), 0)

    def test_FIX_12_nonfinite_payment_fails_cleanly(self):
        self.assertEqual(self.checkout(amount_paid='NaN').status_code, 400)
        self.assertEqual(CheckoutRequest.objects.count(), 0)

    def test_FIX_13_excess_payment_precision_rejected(self):
        self.assertEqual(self.checkout(amount_paid='100.001').status_code, 400)
        self.assertEqual(self.qty(), 20)

    def test_FIX_14_excess_ticket_total_rejected_before_writes(self):
        response = self.checkout(lines=[{'part_id': self.part.pk, 'quantity': 2, 'unit_price': '9999999999.99'}])
        self.assertEqual(response.status_code, 400)
        self.assertEqual((Sale.objects.count(), self.qty()), (0, 20))

    def test_FIX_15_large_decimal_exponent_fails_cleanly(self):
        self.assertEqual(self.checkout(lines=[{'part_id': self.part.pk, 'quantity': '1e1000'}]).status_code, 400)
        self.assertEqual(self.qty(), 20)

    def test_FIX_16_service_rejects_nonfinite_price(self):
        with self.assertRaises(SaleError):
            self.sale(price=D('Infinity'))
        self.assertEqual(Sale.objects.count(), 0)

    def test_FIX_17_stock_service_rejects_fractional_precision(self):
        with self.assertRaises(StockError):
            record_movement(self.part, D('0.001'), 'receipt', self.owner, branch=self.main)
        self.assertEqual(self.qty(), 20)

    def test_FIX_18_negative_job_quantity_cannot_increase_stock(self):
        with self.assertRaises(SaleError):
            add_part(self.job, self.part, D('-1'), self.owner)
        self.assertEqual((self.qty(), self.job.parts.count()), (20, 0))

    def test_FIX_19_payment_service_enforces_counter_ownership(self):
        sale = self.sale(customer=self.trade, payment_method='credit', amount_paid=0)
        with self.assertRaises(SaleError):
            add_payment(sale=sale, amount=50, method='cash', user=self.users['counter'])
        sale.refresh_from_db()
        self.assertEqual(sale.amount_paid, 0)

    def test_FIX_20_counter_can_pay_own_invoice(self):
        counter = self.users['counter']
        sale = self.sale(user=counter, customer=self.trade, payment_method='credit', amount_paid=0)
        self.client.force_login(counter)
        self.assertEqual(self.client.post(reverse('sale_payment', args=[sale.pk]), {'amount': '50', 'method': 'cash'}).status_code, 302)
        sale.refresh_from_db()
        self.assertEqual(sale.amount_paid, 50)

    def test_FIX_21_csv_escapes_text_formulas_and_preserves_numbers(self):
        row = ['=1+1', '+SUM(A1)', '-1+1', '@SUM(A1)', ' \t=1+1', 'normal', D('-1.00')]
        response = csv_response('test.csv', ['value'], [row])
        actual = list(csv.reader(io.StringIO(response.content.decode())))[1]
        self.assertEqual(actual, ["'" + v for v in row[:5]] + ['normal', '-1.00'])

    def test_FIX_22_existing_sku_branch_opening_is_applied_only_once(self):
        data = 'sku,name,cost_price,sell_price,quantity\nUAT-BELT,UAT Belt,120,200,4\n'
        self.csv_import(data, branch='EAST')
        self.csv_import(data, branch='EAST')
        self.assertEqual(self.qty(self.belt, self.east), 4)
        self.assertEqual(StockMovement.objects.filter(branch=self.east, part=self.belt, reason='opening').count(), 1)

    def test_FIX_23_import_cannot_reopen_depleted_stock(self):
        self.csv_import('sku,name,cost_price,sell_price,quantity\nUAT-BELT,UAT Belt,120,200,4\n', branch='EAST')
        record_movement(self.belt, -4, 'sale', self.owner, branch=self.east)
        self.csv_import('sku,name,cost_price,sell_price,quantity\nUAT-BELT,UAT Belt,120,200,4\n', branch='EAST')
        self.assertEqual(self.qty(self.belt, self.east), 0)

    def test_FIX_24_nonfinite_import_rolls_back_entire_file(self):
        with self.assertRaises(CommandError):
            self.csv_import('sku,name,cost_price,sell_price,quantity\nNEW-GOOD,Good,60,100,2\nNEW-BAD,Bad,NaN,100,2\n')
        from catalog.models import Part
        self.assertFalse(Part.objects.filter(tenant=self.ta, sku__startswith='NEW-').exists())

    def test_FIX_25_branch_margin_uses_branch_cost(self):
        record_movement(self.part, 10, 'receipt', self.owner, unit_cost=90, branch=self.main)
        self.assertContains(self.client.get(reverse('part_detail', args=[self.part.pk])), 'margin 30%')

    def test_FIX_26_po_remove_form_passes_real_csrf_validation(self):
        from stock.services import add_po_line
        line = add_po_line(self.po, self.part, D(1), D(60))
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        html = client.get(reverse('po_detail', args=[self.po.pk])).content.decode()
        action = reverse('po_del_line', args=[self.po.pk, line.pk])
        target = next(f for f in re.findall(r'<form\b[^>]*>.*?</form>', html, re.S) if f'action="{action}"' in f)
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', target).group(1)
        self.assertEqual(client.post(action, {'csrfmiddlewaretoken': token}).status_code, 302)
        self.assertFalse(self.po.lines.exists())
