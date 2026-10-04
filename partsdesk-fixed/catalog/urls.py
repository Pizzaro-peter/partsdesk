from django.urls import path

from . import views

urlpatterns = [
    path('upload/', views.catalogue_upload, name='catalogue_upload'),
    path('upload/template/', views.catalogue_template, name='catalogue_template'),
    path('upload/<int:pk>/', views.catalogue_preview, name='catalogue_preview'),
    path('upload/<int:pk>/confirm/', views.catalogue_confirm, name='catalogue_confirm'),
    path('upload/<int:pk>/cancel/', views.catalogue_cancel, name='catalogue_cancel'),
    path("", views.PartList.as_view(), name="part_list"),
    path("parts/new/", views.PartCreate.as_view(), name="part_create"),
    path("parts/<int:pk>/", views.part_detail, name="part_detail"),
    path("parts/<int:pk>/edit/", views.PartUpdate.as_view(), name="part_edit"),
    path("parts/<int:pk>/fitment/add/", views.part_fitment_add, name="part_fitment_add"),
    path("parts/<int:pk>/fitment/<int:vehicle_id>/remove/", views.part_fitment_remove, name="part_fitment_remove"),
    path("parts/<int:pk>/xref/add/", views.part_xref_add, name="part_xref_add"),
    path("parts/<int:pk>/xref/<int:xref_id>/remove/", views.part_xref_remove, name="part_xref_remove"),
    path("api/search/", views.part_search_api, name="part_search_api"),
    path("categories/", views.category_list, name="category_list"),
    path("vehicles/", views.VehicleList.as_view(), name="vehicle_list"),
    path("vehicles/new/", views.VehicleCreate.as_view(), name="vehicle_create"),
    path("vehicles/<int:pk>/edit/", views.VehicleUpdate.as_view(), name="vehicle_edit"),
    path("suppliers/", views.SupplierList.as_view(), name="supplier_list"),
    path("suppliers/new/", views.SupplierCreate.as_view(), name="supplier_create"),
    path("suppliers/<int:pk>/edit/", views.SupplierUpdate.as_view(), name="supplier_edit"),
]
