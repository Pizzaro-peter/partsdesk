from django.urls import path

from . import views

urlpatterns = [
    path("audit/print-request/", views.log_print_request, name="log_print_request"),
    path("switch-branch/", views.switch_branch, name="switch_branch"),
    path("profile/", views.profile, name="profile"),
    path("branches/", views.branches, name="branches"),
    path("staff/", views.staff, name="staff"),
    path("staff/<int:user_id>/", views.staff_user, name="staff_user"),
    path("", views.dashboard, name="dashboard"),
    path("reports/", views.reports_home, name="reports_home"),
    path("reports/sales/", views.report_sales, name="report_sales"),
    path("reports/payment-types/", views.report_payment_methods, name="report_payment_methods"),
    path("reports/branches/", views.report_branches, name="report_branches"),
    path("reports/top-sellers/", views.report_top, name="report_top"),
    path("reports/low-stock/", views.report_low_stock, name="report_low_stock"),
    path("reports/slow-movers/", views.report_slow, name="report_slow"),
    path("reports/stock-value/", views.report_stock_value, name="report_stock_value"),
    path("reports/stock-age/", views.report_stock_age, name="report_stock_age"),
    path("reports/receivables/", views.report_receivables, name="report_receivables"),
    path("reports/supplier-lead-time/", views.report_supplier_lead_time, name="report_supplier_lead_time"),
    path("audit/", views.audit_list, name="audit_list"),
    path("settings/", views.ShopSettingsView.as_view(), name="shop_settings"),
]
