import json
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.test import Client, TestCase
from django.urls import reverse

from catalog.models import BranchStock, Part, Supplier
from core.models import Branch, ShopSettings, Tenant, User
from sales.models import Customer
from sales.services import create_sale
from stock.models import PurchaseOrder
from workshop.models import JobCard
from stock.services import record_movement


class TenantIsolationTests(TestCase):
    def setUp(self):
        self.a = Tenant.objects.create(name="A", slug="a")
        self.b = Tenant.objects.create(name="B", slug="b")
        self.a1 = Branch.objects.create(tenant=self.a, name="A main", code="MAIN")
        self.a2 = Branch.objects.create(tenant=self.a, name="A west", code="WEST")
        self.b1 = Branch.objects.create(tenant=self.b, name="B main", code="MAIN")
        ShopSettings.objects.create(tenant=self.a)
        ShopSettings.objects.create(tenant=self.b)
        self.owner = User.objects.create_user(username="owner-a", password="a-strong-password", tenant=self.a, role="owner")
        self.staff = User.objects.create_user(username="staff-a", password="a-strong-password", tenant=self.a, role="counter")
        self.staff.branches.add(self.a1)
        self.b_user = User.objects.create_user(username="owner-b", password="b-strong-password", tenant=self.b, role="owner")
        self.part_a = Part.objects.create(tenant=self.a, sku="FILTER", name="A filter", sell_price=Decimal("10"))
        self.part_b = Part.objects.create(tenant=self.b, sku="FILTER", name="B filter", sell_price=Decimal("20"))
        record_movement(self.part_a, Decimal("5"), "opening", self.owner, branch=self.a1)
        record_movement(self.part_a, Decimal("2"), "opening", self.owner, branch=self.a2)
        record_movement(self.part_b, Decimal("3"), "opening", self.b_user, branch=self.b1)
        self.customer_b = Customer.objects.create(tenant=self.b, name="B customer")
        self.client.force_login(self.staff)

    def test_catalog_and_stock_are_separate(self):
        response = self.client.get(reverse("part_list"))
        self.assertContains(response, "A filter")
        self.assertNotContains(response, "B filter")
        self.assertEqual(self.client.get(reverse("part_detail", args=[self.part_b.pk])).status_code, 404)
        self.assertEqual(BranchStock.objects.get(branch=self.a1, part=self.part_a).quantity_on_hand, 5)
        self.assertEqual(BranchStock.objects.get(branch=self.a2, part=self.part_a).quantity_on_hand, 2)

    def test_cross_business_customer_and_part_rejected_at_checkout(self):
        url = reverse("pos_checkout")
        bad_customer = {"customer_id": self.customer_b.pk, "lines": [{"part_id": self.part_a.pk, "quantity": 1}]}
        self.assertEqual(self.client.post(url, data=json.dumps(bad_customer), content_type="application/json").status_code, 400)
        bad_part = {"lines": [{"part_id": self.part_b.pk, "quantity": 1}]}
        self.assertEqual(self.client.post(url, data=json.dumps(bad_part), content_type="application/json").status_code, 400)
        with self.assertRaises(PermissionDenied):
            create_sale(user=self.staff, branch=self.a1, lines=[{"part": self.part_b, "quantity": 1, "unit_price": 10}])

    def test_unassigned_branch_cannot_be_selected_or_used(self):
        response = self.client.post(reverse("switch_branch"), {"branch_id": self.a2.pk})
        self.assertEqual(response.status_code, 403)
        with self.assertRaises(PermissionDenied):
            record_movement(self.part_a, -1, "sale", self.staff, branch=self.a2)
        self.assertEqual(self.client.session.get("branch_id"), self.a1.pk)

    def test_owner_can_switch_branches(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("switch_branch"), {"branch_id": self.a2.pk})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session["branch_id"], self.a2.pk)
        self.assertEqual(self.client.get(reverse("part_search_api"), {"q": "FILTER"}).json()["results"][0]["qty"], 2)

    def test_branch_sale_does_not_change_other_branch_stock_or_reports(self):
        sale = create_sale(user=self.owner, branch=self.a1,
            lines=[{"part": self.part_a, "quantity": 1, "unit_price": 10}])
        self.assertEqual(BranchStock.objects.get(branch=self.a1, part=self.part_a).quantity_on_hand, 4)
        self.assertEqual(BranchStock.objects.get(branch=self.a2, part=self.part_a).quantity_on_hand, 2)
        self.assertEqual(BranchStock.objects.get(branch=self.b1, part=self.part_b).quantity_on_hand, 3)
        self.client.force_login(self.b_user)
        self.assertEqual(self.client.get(reverse("sale_detail", args=[sale.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("sale_list")), sale.number)
        self.client.force_login(self.owner)
        self.client.post(reverse("switch_branch"), {"branch_id": self.a2.pk})
        self.assertEqual(self.client.get(reverse("sale_detail", args=[sale.pk])).status_code, 404)

    def test_other_business_order_and_job_urls_are_not_accessible(self):
        customer = Customer.objects.create(tenant=self.b, name="Other customer")
        from sales.models import CustomerVehicle
        vehicle = CustomerVehicle.objects.create(customer=customer, reg_number="B-1", make="Car", model="One")
        supplier = Supplier.objects.create(tenant=self.b, name="B supplier")
        po = PurchaseOrder.objects.create(branch=self.b1, supplier=supplier, number="PO-000001", created_by=self.b_user)
        job = JobCard.objects.create(branch=self.b1, number="JOB-000001", customer=customer,
                                     vehicle=vehicle, complaint="Repair", created_by=self.b_user)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("po_detail", args=[po.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("job_detail", args=[job.pk])).status_code, 404)

    def test_new_branch_starts_with_independent_zero_stock(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("branches"), {"name": "East", "code": "EAST", "is_active": "on"})
        self.assertEqual(response.status_code, 302)
        east = Branch.objects.get(tenant=self.a, code="EAST")
        item = BranchStock.objects.get(branch=east, part=self.part_a)
        self.assertEqual(item.quantity_on_hand, 0)
        self.assertFalse(BranchStock.objects.filter(branch=east, part=self.part_b).exists())

    def test_new_catalog_part_is_shared_with_separate_branch_stock(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("part_create"), {
            "sku": "NEW-1", "name": "Shared item", "part_type": "aftermarket", "condition": "new",
            "unit": "each", "cost_price": "4", "sell_price": "8", "bin_location": "A-1",
            "reorder_level": "2", "reorder_qty": "6", "opening_qty": "3", "is_active": "on",
        })
        self.assertEqual(response.status_code, 302, response.content.decode()[:500])
        part = Part.objects.get(tenant=self.a, sku="NEW-1")
        self.assertEqual(BranchStock.objects.get(part=part, branch=self.a1).quantity_on_hand, 3)
        self.assertEqual(BranchStock.objects.get(part=part, branch=self.a2).quantity_on_hand, 0)
        self.assertFalse(Part.objects.filter(tenant=self.b, sku="NEW-1").exists())

    def test_receipt_changes_only_destination_branch_cost_and_quantity(self):
        self.part_a.cost_price = Decimal("4")
        self.part_a.save(update_fields=["cost_price"])
        BranchStock.objects.filter(part=self.part_a, branch__in=[self.a1, self.a2]).update(cost_price=Decimal("4"))
        record_movement(self.part_a, 5, "receipt", self.owner, unit_cost=Decimal("6"), branch=self.a1)
        main = BranchStock.objects.get(part=self.part_a, branch=self.a1)
        west = BranchStock.objects.get(part=self.part_a, branch=self.a2)
        self.assertEqual((main.quantity_on_hand, main.cost_price), (10, 5))
        self.assertEqual((west.quantity_on_hand, west.cost_price), (2, 4))
