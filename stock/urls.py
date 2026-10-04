from django.urls import path

from . import views

urlpatterns = [
    path("ledger/", views.MovementList.as_view(), name="movement_list"),
    path("adjust/", views.adjust, name="stock_adjust"),
    path("purchase-orders/", views.POList.as_view(), name="po_list"),
    path("purchase-orders/new/", views.POCreate.as_view(), name="po_create"),
    path("purchase-orders/from-low-stock/", views.po_from_low_stock, name="po_from_low_stock"),
    path("purchase-orders/<int:pk>/", views.po_detail, name="po_detail"),
    path("purchase-orders/<int:pk>/add-line/", views.po_add_line, name="po_add_line"),
    path("purchase-orders/<int:pk>/lines/<int:line_id>/delete/", views.po_del_line, name="po_del_line"),
    path("purchase-orders/<int:pk>/status/", views.po_status, name="po_status"),
    path("purchase-orders/<int:pk>/receive/", views.po_receive, name="po_receive"),
]
