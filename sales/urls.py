from django.urls import path

from . import views

urlpatterns = [
    path("pos/", views.pos, name="pos"),
    path("pos/checkout/", views.pos_checkout, name="pos_checkout"),
    path("invoices/", views.SaleList.as_view(), name="sale_list"),
    path("invoices/<int:pk>/", views.sale_detail, name="sale_detail"),
    path("invoices/<int:pk>/pay/", views.sale_payment, name="sale_payment"),
    path("invoices/<int:pk>/return/", views.sale_return, name="sale_return"),
    path("customers/", views.CustomerList.as_view(), name="customer_list"),
    path("customers/new/", views.CustomerCreate.as_view(), name="customer_create"),
    path("customers/<int:pk>/", views.customer_detail, name="customer_detail"),
    path("customers/<int:pk>/edit/", views.CustomerUpdate.as_view(), name="customer_edit"),
    path("customers/<int:pk>/vehicles/new/", views.vehicle_create, name="vehicle_create"),
    path("api/customers/", views.customer_api, name="customer_api"),
]
