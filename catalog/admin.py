from django.contrib import admin

from .models import Category, Part, Supplier, Vehicle

admin.site.register([Category, Supplier, Vehicle])


@admin.register(Part)
class PartAdmin(admin.ModelAdmin):
    list_display = ("sku", "name", "brand", "condition", "sell_price")
    search_fields = ("sku", "name", "oem_number")
    readonly_fields = ()
