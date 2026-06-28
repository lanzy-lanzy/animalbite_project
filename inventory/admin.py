from django.contrib import admin
from .models import VaccineInventory, SupplyInventory, VaccineStockMovement

@admin.register(VaccineInventory)
class VaccineInventoryAdmin(admin.ModelAdmin):
    list_display = ['vaccine_name', 'brand', 'batch_number', 'expiration_date', 'quantity_available']
    list_filter = ['expiration_date']
    search_fields = ['vaccine_name', 'brand', 'batch_number']

@admin.register(SupplyInventory)
class SupplyInventoryAdmin(admin.ModelAdmin):
    list_display = ['supply_name', 'supply_type', 'quantity', 'reorder_level']

@admin.register(VaccineStockMovement)
class VaccineStockMovementAdmin(admin.ModelAdmin):
    list_display = ['vaccine', 'movement_type', 'quantity', 'created_at']
