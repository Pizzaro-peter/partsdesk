from django.urls import path

from . import views

urlpatterns = [
    path("audit/print-request/", views.log_print_request, name="log_print_request"),
    path("switch-branch/", views.switch_branch, name="switch_branch"),
    path("branches/", views.branches, name="branches"),
    path("staff/", views.staff, name="staff"),
    path("", views.dashboard, name="dashboard"),
    path("reports/", views.reports_home, name="reports_home"),
    path("reports/sales/", views.report_sales, name="report_sales"),
    path("reports/top-sellers/", views.report_top, name="report_top"),
    path("reports/low-stock/", views.report_low_stock, name="report_low_stock"),
    path("reports/slow-movers/", views.report_slow, name="report_slow"),
    path("reports/stock-value/", views.report_stock_value, name="report_stock_value"),
    path("reports/receivables/", views.report_receivables, name="report_receivables"),
    path("audit/", views.audit_list, name="audit_list"),
    path("settings/", views.ShopSettingsView.as_view(), name="shop_settings"),
]
