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
    reorder_level = models.PositiveIntegerField(default=5)
    source_supplier = models.CharField(max_length=200, blank=True)
    date_received = models.DateField(default=date.today)
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
        return self.quantity_available <= self.reorder_level

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

class MedicineInventory(models.Model):
    """Pharmacy stock kept separate from vaccine lots."""

    DOSAGE_FORMS = [
        ('tablet', 'Tablet'),
        ('capsule', 'Capsule'),
        ('syrup', 'Syrup'),
        ('injectable', 'Injectable'),
        ('drops', 'Drops'),
        ('ointment', 'Ointment / Cream'),
        ('other', 'Other'),
    ]

    medicine_name = models.CharField(max_length=200)
    generic_name = models.CharField(max_length=200, blank=True, help_text="Generic / brand name")
    strength = models.CharField(max_length=100, blank=True, help_text="e.g., 500 mg, 250 mg/5 mL")
    dosage_form = models.CharField(max_length=20, choices=DOSAGE_FORMS)
    unit = models.CharField(max_length=50, default='unit',
                            help_text="Unit of measurement — tablet, capsule, mL, vial")
    batch_number = models.CharField(max_length=100)
    expiration_date = models.DateField()
    quantity_received = models.IntegerField(default=0)
    quantity_available = models.IntegerField(default=0)
    quantity_dispensed = models.IntegerField(default=0)
    reorder_level = models.PositiveIntegerField(default=10)
    source_supplier = models.CharField(max_length=200, blank=True)
    date_received = models.DateField(default=date.today)
    remarks = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Medicine Inventory"
        ordering = ['medicine_name', 'expiration_date']

    def __str__(self):
        strength = f" {self.strength}" if self.strength else ''
        return f"{self.medicine_name}{strength} ({self.get_dosage_form_display()}, batch {self.batch_number})"

    def label(self):
        """Dropdown label: name, strength, form, and generic (when recorded)."""
        parts = [self.medicine_name]
        if self.strength:
            parts.append(self.strength)
        parts.append(f"· {self.get_dosage_form_display()}")
        if self.generic_name:
            parts.append(f"· {self.generic_name}")
        return ' '.join(parts)

    def is_expired(self):
        return self.expiration_date < date.today()

    def is_out_of_stock(self):
        return self.quantity_available <= 0

    def is_low_stock(self):
        return 0 < self.quantity_available <= self.reorder_level

    def stock_status(self):
        if self.is_expired():
            return 'Expired'
        if self.is_out_of_stock():
            return 'Out of Stock'
        if self.is_low_stock():
            return 'Low Stock'
        return 'Available'

    def stock_status_class(self):
        if self.is_expired() or self.is_out_of_stock():
            return 'bg-red-100 text-red-800'
        if self.is_low_stock():
            return 'bg-yellow-100 text-yellow-800'
        return 'bg-green-100 text-green-800'

    def dose_unit(self):
        """How the doctor should express one dose of this medicine."""
        if self.dosage_form in ('tablet', 'capsule'):
            return f'{self.get_dosage_form_display().lower()}(s) per dose'
        if self.dosage_form == 'syrup':
            return 'mL per dose'
        return f'{self.unit} per dose'


class MedicineStockMovement(models.Model):
    MOVEMENT_TYPES = [
        ('stock_in', 'Stock In'),
        ('dispense', 'Dispensed'),
        ('adjustment', 'Adjustment'),
    ]

    medicine = models.ForeignKey(MedicineInventory, on_delete=models.CASCADE, related_name='movements')
    movement_type = models.CharField(max_length=20, choices=MOVEMENT_TYPES)
    quantity = models.IntegerField()
    reference = models.CharField(max_length=200, blank=True, help_text="Reference case number or note")
    supplier = models.CharField(max_length=200, blank=True, help_text="Supplier for this delivery")
    date_received = models.DateField(null=True, blank=True, help_text="Date this delivery was received")
    time_received = models.TimeField(null=True, blank=True, help_text="Time this delivery was received")
    batch_number = models.CharField(max_length=100, blank=True, help_text="Batch this delivery belongs to")
    expiration_date = models.DateField(null=True, blank=True, help_text="Expiry of the delivered batch")
    moved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    notes = models.TextField(blank=True)
    submission_token = models.UUIDField(
        null=True, blank=True, unique=True, editable=False,
        help_text="Idempotency key for a recorded stock delivery",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.get_movement_type_display()} - {self.medicine.medicine_name} x{self.quantity}"


class VaccineStockMovement(models.Model):
    MOVEMENT_TYPES = [('stock_in', 'Stock In'), ('stock_out', 'Stock Out')]

    vaccine = models.ForeignKey(VaccineInventory, on_delete=models.CASCADE, related_name='movements')
    movement_type = models.CharField(max_length=20, choices=MOVEMENT_TYPES)
    quantity = models.IntegerField()
    reference = models.CharField(max_length=200, blank=True, help_text="Reference case number or note")
    supplier = models.CharField(max_length=200, blank=True, help_text="Supplier for this delivery")
    date_received = models.DateField(null=True, blank=True, help_text="Date this delivery was received")
    time_received = models.TimeField(null=True, blank=True, help_text="Time this delivery was received")
    batch_number = models.CharField(max_length=100, blank=True, help_text="Batch this delivery belongs to")
    expiration_date = models.DateField(null=True, blank=True, help_text="Expiry of the delivered batch")
    moved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    notes = models.TextField(blank=True)
    submission_token = models.UUIDField(
        null=True, blank=True, unique=True, editable=False,
        help_text="Idempotency key for a recorded stock delivery",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.get_movement_type_display()} - {self.vaccine.vaccine_name} x{self.quantity}"
