from django.contrib import admin

from .models import PurchaseOrder, StockMovement

admin.site.register(StockMovement)
admin.site.register(PurchaseOrder)
