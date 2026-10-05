import json
from datetime import datetime, time as dt_time, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.core.cache import cache
from django.test import Client, RequestFactory, TestCase
from django.utils import timezone
from django.urls import reverse

from catalog.models import BranchStock, Part, Supplier
from core.models import AuditLog, Branch, RequestLog, ShopSettings, Tenant, User
from sales.models import Customer, Sale
from sales.services import create_sale
from stock.models import PurchaseOrder, StockMovement
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

    def test_tenant_cannot_create_more_than_five_branches(self):
        self.client.force_login(self.owner)
        for index in range(3):
            Branch.objects.create(tenant=self.a, name=f"Extra {index}", code=f"EX{index}")
        Branch.objects.filter(tenant=self.a, code="WEST").update(is_active=False)

        response = self.client.post(reverse("branches"), {
            "name": "Sixth branch",
            "code": "SIXTH",
            "is_active": "on",
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Branch limit reached (5 maximum).")
        self.assertEqual(Branch.objects.filter(tenant=self.a).count(), 5)

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

    def test_shop_settings_save_returns_to_dashboard_and_persists(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("shop_settings"), {
            "shop_name": "Updated A Shop",
            "address": "1 Main Street",
            "phone": "123456",
            "email": "shop@example.com",
            "tax_id": "TAX-123",
            "currency_symbol": "K",
            "tax_name": "VAT",
            "tax_rate": "16.00",
            "prices_include_tax": "on",
            "counter_max_discount": "10.00",
            "default_labour_rate": "50.00",
            "invoice_footer": "Thank you.",
        }, follow=True)

        self.assertEqual(response.redirect_chain, [(reverse("dashboard"), 302)])
        self.assertContains(response, "Business settings saved.")
        self.a.settings.refresh_from_db()
        self.assertEqual(self.a.settings.shop_name, "Updated A Shop")
        self.assertEqual(self.a.settings.default_labour_rate, Decimal("50.00"))

    def test_login_form_has_password_visibility_control(self):
        response = Client().get(reverse("login"))

        self.assertContains(response, "data-password-toggle")
        self.assertContains(response, 'aria-controls="id_auth-password"')
        self.assertContains(response, 'aria-label="Show password"')
        self.assertContains(response, 'class="eye-show"')

    def test_user_can_update_own_name_and_username(self):
        self.client.force_login(self.owner)

        response = self.client.post(reverse("profile"), {
            "first_name": "Peter",
            "last_name": "Simukanzye",
            "username": "peter-auto-owner",
        }, follow=True)

        self.assertEqual(response.redirect_chain, [(reverse("profile"), 302)])
        self.assertContains(response, "Your profile was updated.")
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.first_name, "Peter")
        self.assertEqual(self.owner.last_name, "Simukanzye")
        self.assertEqual(self.owner.username, "peter-auto-owner")
        self.assertEqual(self.owner.tenant, self.a)
        self.assertEqual(self.owner.role, User.Role.OWNER)
        self.assertIn("_auth_user_id", self.client.session)
        self.assertTrue(AuditLog.objects.filter(
            tenant=self.a, user=self.owner, action="user.update", target="peter-auto-owner",
        ).exists())

    def test_user_cannot_claim_another_users_username_with_different_case(self):
        self.client.force_login(self.owner)
        original_username = self.owner.username

        response = self.client.post(reverse("profile"), {
            "first_name": "Peter",
            "last_name": "Owner",
            "username": self.staff.username.upper(),
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "That username is already in use.")
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.username, original_username)

    def test_totp_is_optional_and_user_can_enroll(self):
        password = "Optional-TOTP-test-password-482!"
        self.staff.set_password(password)
        self.staff.save(update_fields=["password"])
        client = Client()
        response = client.post(reverse("login"), {
            "login_view-current_step": "auth",
            "auth-username": self.staff.username,
            "auth-password": password,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard"))
        profile_response = client.get(reverse("two_factor:profile"))
        self.assertEqual(profile_response.status_code, 200)
        self.assertContains(profile_response, "Account security")
        self.assertNotContains(profile_response, "Provide a template named")
        self.assertNotContains(profile_response, "cdnjs.cloudflare.com")
        setup_response = client.get(reverse("two_factor:setup"))
        self.assertEqual(setup_response.status_code, 200)
        self.assertContains(setup_response, "/static/css/app.css")

    def test_totp_enabled_account_must_supply_a_valid_code(self):
        from django_otp.oath import TOTP
        from django_otp.plugins.otp_totp.models import TOTPDevice

        password = "Enabled-TOTP-test-password-284!"
        self.staff.set_password(password)
        self.staff.save(update_fields=["password"])
        device = TOTPDevice.objects.create(
            user=self.staff,
            name="default",
            confirmed=True,
            key="3132333435363738393031323334353637383930",
        )
        client = Client()
        auth_response = client.post(reverse("login"), {
            "login_view-current_step": "auth",
            "auth-username": self.staff.username,
            "auth-password": password,
        })
        self.assertEqual(auth_response.status_code, 200)
        self.assertContains(auth_response, "Authenticator code")
        self.assertNotIn("_auth_user_id", client.session)

        totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
        code = str(totp.token()).zfill(device.digits)
        token_response = client.post(reverse("login"), {
            "login_view-current_step": "token",
            "token-otp_token": code,
        })
        self.assertEqual(token_response.status_code, 302)
        self.assertEqual(token_response.url, reverse("dashboard"))
        self.assertIn("_auth_user_id", client.session)

    def test_login_attempts_lock_out_username_and_ip(self):
        remote_addr = "198.51.100.22"
        password = "Rate-limit-test-password-517!"
        self.staff.set_password(password)
        self.staff.save(update_fields=["password"])
        for _ in range(5):
            Client().post(reverse("login"), {
                "auth-username": self.staff.username,
                "auth-password": "incorrect",
            }, REMOTE_ADDR=remote_addr)

        locked_client = Client()
        response = locked_client.post(reverse("login"), {
            "auth-username": self.staff.username,
            "auth-password": password,
        }, REMOTE_ADDR=remote_addr)
        self.assertNotEqual(response.status_code, 302)
        self.assertNotIn("_auth_user_id", locked_client.session)

    def test_owner_can_update_staff_details_and_branch_access(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("staff_user", args=[self.staff.pk]), {
            "action": "update",
            "first_name": "Updated",
            "last_name": "Counter",
            "role": User.Role.MANAGER,
            "branches": [self.a1.pk, self.a2.pk],
        })

        self.assertRedirects(response, reverse("staff_user", args=[self.staff.pk]))
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.get_full_name(), "Updated Counter")
        self.assertEqual(self.staff.role, User.Role.MANAGER)
        self.assertSetEqual(set(self.staff.branches.values_list("pk", flat=True)), {self.a1.pk, self.a2.pk})
        self.assertTrue(AuditLog.objects.filter(
            tenant=self.a, user=self.owner, action="user.update", target=self.staff.username,
        ).exists())
        self.assertTrue(AuditLog.objects.filter(
            tenant=self.a, user=self.owner, action="user.branch_access.add", target=self.staff.username,
        ).exists())


    def test_owner_cannot_assign_staff_to_another_tenant_branch(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("staff_user", args=[self.staff.pk]), {
            "action": "update",
            "first_name": "Staff",
            "last_name": "Member",
            "role": User.Role.COUNTER,
            "branches": [self.b1.pk],
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select a valid choice")
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.role, User.Role.COUNTER)
        self.assertSetEqual(set(self.staff.branches.values_list("pk", flat=True)), {self.a1.pk})

    def test_owner_can_reset_staff_password_without_logging_secret(self):
        self.client.force_login(self.owner)
        temporary_password = "Zebra-Moon-Train-934!"
        response = self.client.post(reverse("staff_user", args=[self.staff.pk]), {
            "action": "reset_password",
            "password": temporary_password,
            "confirm_password": temporary_password,
        }, follow=True)

        self.assertEqual(response.redirect_chain, [(reverse("staff_user", args=[self.staff.pk]), 302)])
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.check_password(temporary_password))
        self.assertNotContains(response, temporary_password)
        password_event = AuditLog.objects.get(
            tenant=self.a, user=self.owner, action="auth.password_change", target=self.staff.username,
        )
        self.assertNotIn(temporary_password, str(password_event.changes))
        self.assertNotIn(temporary_password, password_event.detail)

    def test_invalid_temporary_password_does_not_change_staff_credentials(self):
        self.client.force_login(self.owner)
        original_password = self.staff.password
        response = self.client.post(reverse("staff_user", args=[self.staff.pk]), {
            "action": "reset_password",
            "password": "short",
            "confirm_password": "short",
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ensure this value has at least 12 characters")
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.password, original_password)

    def test_owner_can_disable_and_reenable_staff_account(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("staff_user", args=[self.staff.pk]), {"action": "disable"})
        self.assertRedirects(response, reverse("staff_user", args=[self.staff.pk]))
        self.staff.refresh_from_db()
        self.assertFalse(self.staff.is_active)

        response = self.client.post(reverse("staff_user", args=[self.staff.pk]), {"action": "enable"})
        self.assertRedirects(response, reverse("staff_user", args=[self.staff.pk]))
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.is_active)

    def test_staff_management_is_limited_to_current_tenant_and_owner(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("staff_user", args=[self.b_user.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("staff_user", args=[self.owner.pk])).status_code, 404)

        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("staff_user", args=[self.staff.pk])).status_code, 403)

    def test_non_owners_cannot_use_staff_management(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("staff_user", args=[self.staff.pk])).status_code, 403)

    def test_receipt_changes_only_destination_branch_cost_and_quantity(self):
        self.part_a.cost_price = Decimal("4")
        self.part_a.save(update_fields=["cost_price"])
        BranchStock.objects.filter(part=self.part_a, branch__in=[self.a1, self.a2]).update(cost_price=Decimal("4"))
        record_movement(self.part_a, 5, "receipt", self.owner, unit_cost=Decimal("6"), branch=self.a1)
        main = BranchStock.objects.get(part=self.part_a, branch=self.a1)
        west = BranchStock.objects.get(part=self.part_a, branch=self.a2)
        self.assertEqual((main.quantity_on_hand, main.cost_price), (10, 5))
        self.assertEqual((west.quantity_on_hand, west.cost_price), (2, 4))

    def test_analytics_compares_equal_periods_and_renders_sales_chart(self):
        self.client.force_login(self.owner)
        today = timezone.localdate()
        previous_day = today - timedelta(days=1)
        BranchStock.objects.filter(branch=self.a1, part=self.part_a).update(cost_price=Decimal("4"))

        current_sale = create_sale(
            user=self.owner, branch=self.a1,
            lines=[{"part": self.part_a, "quantity": 1, "unit_price": 10}],
        )
        previous_sale = create_sale(
            user=self.owner, branch=self.a1,
            lines=[{"part": self.part_a, "quantity": 1, "unit_price": 5}],
        )
        previous_sale_time = timezone.make_aware(datetime.combine(previous_day, dt_time.min))
        Sale.objects.filter(pk=previous_sale.pk).update(created_at=previous_sale_time)

        response = self.client.get(reverse("reports_home"), {
            "start": today.isoformat(),
            "end": today.isoformat(),
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Clear numbers. Better decisions.")
        self.assertContains(response, "100.0% higher vs previous period")
        self.assertContains(response, "aria-label=\"Net sales chart")
        self.assertEqual(response.context["summary"][0]["value"], Decimal("10"))
        self.assertEqual(response.context["summary"][1]["value"], Decimal("6"))
        self.assertEqual(response.context["summary"][2]["value"], 1)
        self.assertEqual(response.context["sales_chart"][0]["revenue"], Decimal("10"))
        self.assertEqual(current_sale.branch_id, self.a1.pk)

        report = self.client.get(reverse("report_sales"), {
            "start": today.isoformat(),
            "end": today.isoformat(),
        })
        self.assertEqual(report.status_code, 200)
        self.assertContains(report, "Average sale")
        self.assertEqual(report.context["metrics"][0]["value"], Decimal("10"))
        self.assertEqual(report.context["chart"][0]["invoices"], 1)

        top_report = self.client.get(reverse("report_top"), {
            "start": today.isoformat(),
            "end": today.isoformat(),
        })
        self.assertEqual(top_report.status_code, 200)
        self.assertContains(top_report, "Top 10 by net sales")
        self.assertEqual(top_report.context["ranked"][0]["value"], Decimal("10"))

    def test_analytics_chart_buckets_long_ranges_into_weeks(self):
        from core.views import _sales_chart

        start = timezone.localdate()
        days = [
            {
                "date": start + timedelta(days=offset),
                "revenue": Decimal("10"),
                "profit": Decimal("4"),
                "invoices": 1,
            }
            for offset in range(60)
        ]
        chart = _sales_chart(days)
        self.assertLessEqual(len(chart), 10)
        self.assertEqual(sum(point["invoices"] for point in chart), 60)
        self.assertEqual(max(point["revenue_pct"] for point in chart), 100)

    def test_sales_by_payment_type_summarizes_net_sales_and_gross_profit(self):
        self.client.force_login(self.owner)
        BranchStock.objects.filter(branch=self.a1, part=self.part_a).update(cost_price=Decimal("4"))
        create_sale(
            user=self.owner, branch=self.a1,
            lines=[{"part": self.part_a, "quantity": 1, "unit_price": 10}],
            payment_method=Sale.Method.CARD,
        )
        create_sale(
            user=self.owner, branch=self.a1,
            lines=[{"part": self.part_a, "quantity": 1, "unit_price": 5}],
            payment_method=Sale.Method.CREDIT,
            customer=Customer.objects.create(tenant=self.a, name="Credit account", credit_limit=Decimal("100")),
            amount_paid=0,
        )
        response = self.client.get(reverse("report_payment_methods"))
        self.assertEqual(response.status_code, 200)
        rows = {row[0]["v"]: row for row in response.context["rows"]}
        self.assertEqual([cell["v"] for cell in rows["Card"][1:]], ["1", "$10.00", "$6.00"])
        self.assertEqual([cell["v"] for cell in rows["On account"][1:]], ["1", "$5.00", "$1.00"])
        self.assertContains(response, "not cash collections")

    def test_branch_comparison_is_limited_to_branches_user_can_access(self):
        manager = User.objects.create_user(
            username="manager-a", password="ManagerTestPassword!42", tenant=self.a, role="manager",
        )
        manager.branches.add(self.a1)
        self.client.force_login(manager)
        response = self.client.get(reverse("report_branches"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row[0]["v"] for row in response.context["rows"]], ["A main"])
        self.assertNotContains(response, "A west")
        self.assertNotContains(response, "B main")

        self.client.force_login(self.owner)
        response = self.client.get(reverse("report_branches"))
        self.assertEqual([row[0]["v"] for row in response.context["rows"]], ["A main", "A west"])
        self.assertContains(response, "Gross margin")

    def test_receivables_report_groups_balances_by_invoice_age(self):
        self.client.force_login(self.owner)
        customer = Customer.objects.create(tenant=self.a, name="A credit", credit_limit=Decimal("100"))
        recent = create_sale(
            user=self.owner, branch=self.a1,
            lines=[{"part": self.part_a, "quantity": 1, "unit_price": 10}],
            payment_method=Sale.Method.CREDIT, customer=customer, amount_paid=0,
        )
        older = create_sale(
            user=self.owner, branch=self.a1,
            lines=[{"part": self.part_a, "quantity": 1, "unit_price": 5}],
            payment_method=Sale.Method.CREDIT, customer=customer, amount_paid=0,
        )
        today = timezone.localdate()
        Sale.objects.filter(pk=older.pk).update(
            created_at=timezone.make_aware(datetime.combine(today - timedelta(days=70), dt_time.min))
        )
        response = self.client.get(reverse("report_receivables"))
        self.assertEqual(response.status_code, 200)
        row = response.context["rows"][0]
        self.assertEqual(row[0]["v"], "A credit")
        self.assertEqual(row[3]["v"], "$10.00")
        self.assertEqual(row[5]["v"], "$5.00")
        self.assertEqual(row[-1]["v"], "$15.00")
        self.assertContains(response, "measured from invoice date")

    def test_stock_age_report_uses_latest_recorded_receipt(self):
        self.client.force_login(self.owner)
        today = timezone.localdate()
        movement = record_movement(
            self.part_a, 1, StockMovement.Reason.RECEIPT, self.owner,
            unit_cost=Decimal("4"), reference="AGE-PO", branch=self.a1,
        )
        StockMovement.objects.filter(pk=movement.pk).update(
            created_at=timezone.make_aware(datetime.combine(today - timedelta(days=45), dt_time.min))
        )
        response = self.client.get(reverse("report_stock_age"))
        self.assertEqual(response.status_code, 200)
        row = next(row for row in response.context["rows"] if row[0]["v"] == self.part_a.sku)
        self.assertEqual(row[6]["v"], "45")
        self.assertEqual(row[7]["v"], "31–60 days")
        self.assertContains(response, "not FIFO/lot-level age")

    def test_supplier_lead_time_uses_order_date_to_first_receipt(self):
        self.client.force_login(self.owner)
        supplier = Supplier.objects.create(tenant=self.a, name="A supplier")
        today = timezone.localdate()
        order = PurchaseOrder.objects.create(
            branch=self.a1, supplier=supplier, number="PO-AGE-1", created_by=self.owner,
            order_date=today - timedelta(days=7), status=PurchaseOrder.Status.PARTIAL,
        )
        record_movement(
            self.part_a, 1, StockMovement.Reason.RECEIPT, self.owner,
            reference=order.number, branch=self.a1,
        )
        response = self.client.get(reverse("report_supplier_lead_time"), {
            "start": (today - timedelta(days=7)).isoformat(), "end": today.isoformat(),
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual([[cell["v"] for cell in row] for row in response.context["rows"]],
                         [["A supplier", "1", "7.0"]])
        self.assertContains(response, "time to first delivery")


class PublicSignupTests(TestCase):
    def setUp(self):
        cache.clear()
        self.signup_data = {
            "business_name": "North Star Auto Parts",
            "first_name": "Alex",
            "last_name": "Owner",
            "username": "northstar_owner",
            "email": "alex@example.com",
            "password1": "Quasar-River-Tulip-753!",
            "password2": "Quasar-River-Tulip-753!",
            "website": "",
        }

    def test_login_page_offers_public_business_signup(self):
        response = self.client.get(reverse("two_factor:login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("signup"))

    def test_signup_creates_owner_and_tenant_then_requires_first_branch(self):
        response = self.client.post(reverse("signup"), self.signup_data)

        self.assertRedirects(response, reverse("branch_onboarding"))
        user = User.objects.get(username="northstar_owner")
        self.assertEqual(user.role, User.Role.OWNER)
        self.assertEqual(user.first_name, "Alex")
        self.assertEqual(user.email, "alex@example.com")
        self.assertTrue(user.check_password(self.signup_data["password1"]))
        self.assertTrue(user.tenant_id)
        self.assertEqual(user.tenant.name, "North Star Auto Parts")
        self.assertTrue(ShopSettings.objects.filter(tenant=user.tenant).exists())
        self.assertFalse(Branch.objects.filter(tenant=user.tenant).exists())
        self.assertIn("_auth_user_id", self.client.session)

    def test_signup_rejects_password_mismatch_without_creating_records(self):
        data = {**self.signup_data, "password2": "Different-Password-483!"}

        response = self.client.post(reverse("signup"), data)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The passwords do not match.")
        self.assertFalse(User.objects.filter(username="northstar_owner").exists())
        self.assertFalse(Tenant.objects.filter(name="North Star Auto Parts").exists())

    def test_signup_honeypot_rejects_bot_submission_without_creating_records(self):
        data = {**self.signup_data, "website": "spam"}

        response = self.client.post(reverse("signup"), data)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Unable to process this signup.")
        self.assertFalse(User.objects.filter(username="northstar_owner").exists())
        self.assertFalse(Tenant.objects.exists())

    def test_signup_throttles_repeated_attempts(self):
        request = RequestFactory().post(reverse("signup"))
        request.META["REMOTE_ADDR"] = "198.51.100.55"
        from core.views import _signup_rate_limited

        self.assertEqual([_signup_rate_limited(request) for _ in range(10)], [False] * 10)
        self.assertTrue(_signup_rate_limited(request))
        with patch("core.views._signup_rate_limited", return_value=True):
            response = self.client.post(reverse("signup"), self.signup_data)

        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "Too many signup attempts", status_code=429)
        self.assertFalse(Tenant.objects.exists())

    def test_first_branch_onboarding_is_resumable_and_assigns_owner_access(self):
        signup_response = self.client.post(reverse("signup"), self.signup_data)
        self.assertRedirects(signup_response, reverse("branch_onboarding"))
        user = User.objects.get(username="northstar_owner")

        dashboard_response = self.client.get(reverse("dashboard"))

        self.assertRedirects(dashboard_response, reverse("branch_onboarding"))
        response = self.client.post(reverse("branch_onboarding"), {
            "name": "Central",
            "code": "CENTRAL",
        })

        self.assertRedirects(response, reverse("dashboard"))
        branch = Branch.objects.get(tenant=user.tenant, code="CENTRAL")
        user.refresh_from_db()
        self.assertEqual(user.role, User.Role.OWNER)
        self.assertIn(branch, user.branches.all())
        self.assertEqual(self.client.session["branch_id"], branch.pk)
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)


class RequestLogRetentionTests(TestCase):
    def test_retention_command_previews_then_only_purges_request_logs(self):
        old_time = timezone.now() - timedelta(days=120)
        with patch("django.utils.timezone.now", return_value=old_time):
            request_log = RequestLog.objects.create(
                request_id="old-request",
                method="GET",
                route="dashboard",
                status_code=200,
                outcome="success",
            )
        audit_log = AuditLog.objects.create(action="retention.test", request_id="audit-retained")

        output = StringIO()
        call_command("purge_request_logs", days=90, stdout=output)
        self.assertIn("Dry run: 1 request log(s)", output.getvalue())
        self.assertTrue(RequestLog.objects.filter(pk=request_log.pk).exists())

        call_command("purge_request_logs", days=90, delete=True, stdout=StringIO())
        self.assertFalse(RequestLog.objects.filter(pk=request_log.pk).exists())
        self.assertTrue(AuditLog.objects.filter(pk=audit_log.pk).exists())

    def test_retention_command_requires_positive_days(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("purge_request_logs", days=0, stdout=StringIO())
