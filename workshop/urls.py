from django.urls import path

from . import views

urlpatterns = [
    path("", views.job_list, name="job_list"),
    path("new/", views.job_create, name="job_create"),
    path("<int:pk>/", views.job_detail, name="job_detail"),
    path("<int:pk>/parts/add/", views.job_add_part, name="job_add_part"),
    path("<int:pk>/parts/<int:item_id>/remove/", views.job_remove_part, name="job_remove_part"),
    path("<int:pk>/labour/add/", views.job_add_labour, name="job_add_labour"),
    path("<int:pk>/labour/<int:item_id>/remove/", views.job_remove_labour, name="job_remove_labour"),
    path("<int:pk>/invoice/", views.job_invoice, name="job_invoice"),
    path("<int:pk>/cancel/", views.job_cancel, name="job_cancel"),
]
