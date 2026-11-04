from django.contrib import admin
from .models import (
    VaccineInventory, SupplyInventory, VaccineStockMovement,
    MedicineInventory, MedicineStockMovement,
)

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


@admin.register(MedicineInventory)
class MedicineInventoryAdmin(admin.ModelAdmin):
    list_display = ['medicine_name', 'generic_name', 'strength', 'dosage_form',
                    'batch_number', 'expiration_date', 'quantity_available']
    list_filter = ['dosage_form', 'expiration_date']
    search_fields = ['medicine_name', 'generic_name', 'strength', 'batch_number']


@admin.register(MedicineStockMovement)
class MedicineStockMovementAdmin(admin.ModelAdmin):
    list_display = ['medicine', 'movement_type', 'quantity', 'moved_by', 'created_at']
