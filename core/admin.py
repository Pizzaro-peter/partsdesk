from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    fieldsets = DjangoUserAdmin.fieldsets + (("Shop role", {"fields": ("role",)}),)
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        ("Shop role", {"fields": ("role", "first_name", "last_name")}),
    )
    list_display = ("username", "get_full_name", "role", "is_active")
    list_filter = ("role", "is_active")


from .models import AuditLog, RequestLog


class ReadOnlyLogAdmin(admin.ModelAdmin):
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False
    list_display = ('created_at', 'actor_name', 'tenant', 'branch', 'outcome')
    list_filter = ('outcome', 'tenant')
    search_fields = ('actor_name', 'target', 'request_id')


admin.site.register(AuditLog, ReadOnlyLogAdmin)
admin.site.register(RequestLog, ReadOnlyLogAdmin)
