from django.db import models
from django.conf import settings
from datetime import date

class VaccineInventory(models.Model):
    vaccine_name = models.CharField(max_length=200)
    brand = models.CharField(max_length=200)
    batch_number = models.CharField(max_length=100)
    expiration_date = models.DateField()
    quantity_received = models.IntegerField(default=0)
    quantity_available = models.IntegerField(default=0)
    quantity_used = models.IntegerField(default=0)
    source_supplier = models.CharField(max_length=200, blank=True)
    date_received = models.DateField(auto_now_add=True)
    remarks = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Vaccine Inventory"
        ordering = ['-date_received']

    def __str__(self):
        return f"{self.vaccine_name} ({self.brand}) - Batch: {self.batch_number}"

    def is_expired(self):
        return self.expiration_date < date.today()

    def is_low_stock(self):
        return self.quantity_available <= 5

class SupplyInventory(models.Model):
    SUPPLY_TYPES = [
        ('syringe', 'Syringe'), ('cotton', 'Cotton'), ('alcohol', 'Alcohol'),
        ('gloves', 'Gloves'), ('forms', 'Forms'), ('other', 'Other'),
    ]
    supply_name = models.CharField(max_length=200)
    supply_type = models.CharField(max_length=20, choices=SUPPLY_TYPES)
    quantity = models.IntegerField(default=0)
    unit = models.CharField(max_length=50, default='piece')
    reorder_level = models.IntegerField(default=10)
    source_supplier = models.CharField(max_length=200, blank=True)
    remarks = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Supply Inventory"
        ordering = ['supply_name']

    def __str__(self):
        return f"{self.supply_name} ({self.quantity} {self.unit})"

    def is_low_stock(self):
        return self.quantity <= self.reorder_level

class VaccineStockMovement(models.Model):
    MOVEMENT_TYPES = [('stock_in', 'Stock In'), ('stock_out', 'Stock Out')]

    vaccine = models.ForeignKey(VaccineInventory, on_delete=models.CASCADE, related_name='movements')
    movement_type = models.CharField(max_length=20, choices=MOVEMENT_TYPES)
    quantity = models.IntegerField()
    reference = models.CharField(max_length=200, blank=True, help_text="Reference case number or note")
    moved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.get_movement_type_display()} - {self.vaccine.vaccine_name} x{self.quantity}"
