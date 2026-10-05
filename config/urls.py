from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from two_factor.urls import urlpatterns as two_factor_urls
from two_factor.views import LoginView as TwoFactorLoginView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/login/", TwoFactorLoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path(
        "accounts/password_change/",
        auth_views.PasswordChangeView.as_view(template_name="registration/password_change_form.html"),
        name="password_change",
    ),
    path(
        "accounts/password_change/done/",
        auth_views.PasswordChangeDoneView.as_view(template_name="registration/password_change_done.html"),
        name="password_change_done",
    ),
    path("accounts/", include(two_factor_urls)),
    path("catalog/", include("catalog.urls")),
    path("stock/", include("stock.urls")),
    path("sales/", include("sales.urls")),
    path("workshop/", include("workshop.urls")),
    path("", include("core.urls")),
]
