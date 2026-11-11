import uuid

from django import forms
from django.db import IntegrityError, transaction
from django.utils import timezone
from .models import (
    VaccineInventory, SupplyInventory, VaccineStockMovement,
    MedicineInventory, MedicineStockMovement,
)

class VaccineInventoryForm(forms.ModelForm):
    quantity_received = forms.IntegerField(
        min_value=1,
        widget=forms.NumberInput(attrs={
            'class': 'form-input', 'min': 1, 'placeholder': 'e.g., 25',
        }),
    )

    class Meta:
        model = VaccineInventory
        fields = [
            'vaccine_name', 'brand', 'strength', 'dosage_form', 'unit',
            'batch_number', 'expiration_date', 'quantity_received',
            'reorder_level', 'source_supplier', 'remarks',
        ]
        widgets = {
            'vaccine_name': forms.TextInput(attrs={
                'class': 'form-input', 'placeholder': 'e.g., Anti-Rabies Vaccine',
            }),
            'brand': forms.TextInput(attrs={
                'class': 'form-input', 'placeholder': 'e.g., Verorab',
            }),
            'strength': forms.TextInput(attrs={
                'class': 'form-input', 'placeholder': 'e.g., 0.5 mL',
            }),
            'dosage_form': forms.Select(attrs={'class': 'form-input'}),
            'unit': forms.TextInput(attrs={
                'class': 'form-input', 'placeholder': 'vial / dose / unit / ampoule',
            }),
            'batch_number': forms.TextInput(attrs={
                'class': 'form-input', 'placeholder': 'e.g., LOT-2026-01',
            }),
            'expiration_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-input'}),
            'reorder_level': forms.NumberInput(attrs={'class': 'form-input', 'min': 0}),
            'source_supplier': forms.TextInput(attrs={'class': 'form-input'}),
            'remarks': forms.Textarea(attrs={'rows': 3, 'class': 'form-input'}),
        }


class VaccineAddStockForm(forms.Form):
    """
    Records one delivery for an already selected vaccine. A delivery either
    creates a new batch row or tops up an existing batch whose expiry matches.
    """

    batch_number = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={
            'class': 'form-input', 'placeholder': 'e.g., IMX-2026-002',
        }),
    )
    quantity_received = forms.IntegerField(
        min_value=1,
        widget=forms.NumberInput(attrs={
            'class': 'form-input', 'min': 1, 'placeholder': 'e.g., 25',
        }),
    )
    source_supplier = forms.CharField(
        max_length=200,
        widget=forms.TextInput(attrs={
            'class': 'form-input', 'placeholder': 'e.g., ABC Pharma',
        }),
    )
    date_received = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-input'}),
    )
    time_received = forms.TimeField(
        widget=forms.TimeInput(
            attrs={'type': 'time', 'class': 'form-input'}, format='%H:%M'),
    )
    expiration_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-input'}),
    )
    remarks = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            'rows': 3, 'class': 'form-textarea',
            'placeholder': 'e.g., New vaccine stock delivery.',
        }),
    )
    submission_token = forms.UUIDField(
        required=False, widget=forms.HiddenInput(),
        help_text="Used to ignore duplicate submissions of the same delivery",
    )

    def __init__(self, *args, vaccine=None, **kwargs):
        self.vaccine = vaccine
        self.existing_batch = None
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.initial.setdefault('date_received', timezone.localdate())
            self.initial.setdefault(
                'time_received',
                timezone.localtime().time().replace(second=0, microsecond=0))
            self.initial.setdefault('submission_token', uuid.uuid4())

    def clean(self):
        cleaned = super().clean()
        batch_number = (cleaned.get('batch_number') or '').strip()
        date_received = cleaned.get('date_received')
        expiration_date = cleaned.get('expiration_date')
        if batch_number:
            cleaned['batch_number'] = batch_number

        if date_received and expiration_date:
            if expiration_date < date_received:
                self.add_error(
                    'expiration_date',
                    'Expiration date cannot be before the date received.')
            elif expiration_date < timezone.localdate():
                self.add_error(
                    'expiration_date',
                    'Expired stock cannot be added. Check the expiration date '
                    'before saving.')
        if self.errors or not (self.vaccine and batch_number and expiration_date):
            return cleaned

        candidates = VaccineInventory.objects.filter(
            vaccine_name__iexact=self.vaccine.vaccine_name,
            brand__iexact=self.vaccine.brand,
            strength__iexact=self.vaccine.strength,
            dosage_form=self.vaccine.dosage_form,
            unit__iexact=self.vaccine.unit,
            batch_number__iexact=batch_number,
        )
        if candidates.exclude(expiration_date=expiration_date).exists():
            raise forms.ValidationError(
                f'Batch {batch_number} already exists for '
                f'{self.vaccine.vaccine_name} ({self.vaccine.brand}) with a '
                'different expiration date. Verify the batch details before '
                'recording this delivery.'
            )
        self.existing_batch = (
            candidates.filter(expiration_date=expiration_date)
            .order_by('id').first()
        )
        return cleaned

    def save(self, user):
        data = self.cleaned_data
        quantity = data['quantity_received']
        token = data.get('submission_token') or uuid.uuid4()
        duplicate = VaccineStockMovement.objects.filter(
            submission_token=token).select_related('vaccine').first()
        if duplicate:
            return duplicate.vaccine, False, True

        try:
            with transaction.atomic():
                if self.existing_batch:
                    batch = VaccineInventory.objects.select_for_update().get(
                        pk=self.existing_batch.pk)
                    batch.quantity_received += quantity
                    batch.quantity_available += quantity
                    batch.save(update_fields=[
                        'quantity_received', 'quantity_available', 'updated_at'])
                    created = False
                else:
                    batch = VaccineInventory.objects.create(
                        vaccine_name=self.vaccine.vaccine_name,
                        brand=self.vaccine.brand,
                        strength=self.vaccine.strength,
                        dosage_form=self.vaccine.dosage_form,
                        unit=self.vaccine.unit,
                        batch_number=data['batch_number'],
                        expiration_date=data['expiration_date'],
                        quantity_received=quantity,
                        quantity_available=quantity,
                        source_supplier=data['source_supplier'],
                        date_received=data['date_received'],
                        remarks=data['remarks'],
                        created_by=user,
                    )
                    created = True
                VaccineStockMovement.objects.create(
                    vaccine=batch,
                    movement_type='stock_in',
                    quantity=quantity,
                    supplier=data['source_supplier'],
                    date_received=data['date_received'],
                    time_received=data['time_received'],
                    batch_number=data['batch_number'],
                    expiration_date=data['expiration_date'],
                    moved_by=user,
                    notes=data['remarks'],
                    submission_token=token,
                )
        except IntegrityError:
            duplicate = VaccineStockMovement.objects.filter(
                submission_token=token).select_related('vaccine').first()
            if duplicate:
                return duplicate.vaccine, False, True
            raise
        return batch, created, False

class SupplyInventoryForm(forms.ModelForm):
    class Meta:
        model = SupplyInventory
        fields = '__all__'
        widgets = {
            'supply_name': forms.TextInput(attrs={'class': 'form-input'}),
            'supply_type': forms.Select(attrs={'class': 'form-select'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-input'}),
            'unit': forms.TextInput(attrs={'class': 'form-input'}),
            'reorder_level': forms.NumberInput(attrs={'class': 'form-input'}),
            'source_supplier': forms.TextInput(attrs={'class': 'form-input'}),
            'remarks': forms.Textarea(attrs={'rows': 3, 'class': 'form-textarea'}),
        }

class MedicineInventoryForm(forms.ModelForm):
    class Meta:
        model = MedicineInventory
        exclude = ['quantity_available', 'quantity_dispensed', 'date_received', 'created_by']
        widgets = {
            'medicine_name': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g., Amoxicillin'}),
            'generic_name': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g., Amoxicillin trihydrate'}),
            'strength': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g., 500 mg'}),
            'dosage_form': forms.Select(attrs={'class': 'form-input'}),
            'unit': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'tablet / capsule / mL / vial'}),
            'batch_number': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g., LOT-2026-01'}),
            'expiration_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-input'}),
            'quantity_received': forms.NumberInput(attrs={'class': 'form-input', 'min': 0}),
            'reorder_level': forms.NumberInput(attrs={'class': 'form-input', 'min': 0}),
            'source_supplier': forms.TextInput(attrs={'class': 'form-input'}),
            'remarks': forms.Textarea(attrs={'rows': 3, 'class': 'form-input'}),
        }


class MedicineAddStockForm(forms.Form):
    """
    Records one delivery for an already selected medicine. A delivery either
    creates a new batch row or tops up an existing batch of the same product
    whose expiry matches. Product identity covers name, generic name, strength,
    form and unit, so batches that merely share a number are never merged.
    """

    batch_number = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={
            'class': 'form-input', 'placeholder': 'e.g., LOT-2026-002',
        }),
    )
    quantity_received = forms.IntegerField(
        min_value=1,
        widget=forms.NumberInput(attrs={
            'class': 'form-input', 'min': 1, 'placeholder': 'e.g., 50',
        }),
    )
    source_supplier = forms.CharField(
        max_length=200,
        widget=forms.TextInput(attrs={
            'class': 'form-input', 'placeholder': 'e.g., ABC Pharma',
        }),
    )
    date_received = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-input'}),
    )
    time_received = forms.TimeField(
        widget=forms.TimeInput(
            attrs={'type': 'time', 'class': 'form-input'}, format='%H:%M'),
    )
    expiration_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-input'}),
    )
    remarks = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            'rows': 3, 'class': 'form-textarea',
            'placeholder': 'e.g., New medicine stock delivery.',
        }),
    )
    submission_token = forms.UUIDField(
        required=False, widget=forms.HiddenInput(),
        help_text="Used to ignore duplicate submissions of the same delivery",
    )

    def __init__(self, *args, medicine=None, **kwargs):
        self.medicine = medicine
        self.existing_batch = None
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.initial.setdefault('date_received', timezone.localdate())
            self.initial.setdefault(
                'time_received',
                timezone.localtime().time().replace(second=0, microsecond=0))
            self.initial.setdefault('submission_token', uuid.uuid4())

    def _product_batches(self):
        return MedicineInventory.objects.filter(
            medicine_name__iexact=self.medicine.medicine_name,
            generic_name__iexact=self.medicine.generic_name,
            strength__iexact=self.medicine.strength,
            dosage_form=self.medicine.dosage_form,
            unit__iexact=self.medicine.unit,
        )

    def clean(self):
        cleaned = super().clean()
        batch_number = (cleaned.get('batch_number') or '').strip()
        date_received = cleaned.get('date_received')
        expiration_date = cleaned.get('expiration_date')
        if batch_number:
            cleaned['batch_number'] = batch_number

        if date_received and expiration_date:
            if expiration_date < date_received:
                self.add_error(
                    'expiration_date',
                    'Expiration date cannot be before the date received.')
            elif expiration_date < timezone.localdate():
                self.add_error(
                    'expiration_date',
                    'Expired stock cannot be added. Check the expiration date '
                    'before saving.')
        if self.errors or not (self.medicine and batch_number and expiration_date):
            return cleaned

        candidates = self._product_batches().filter(
            batch_number__iexact=batch_number)
        if candidates.exclude(expiration_date=expiration_date).exists():
            raise forms.ValidationError(
                f'Batch {batch_number} already exists for '
                f'{self.medicine.medicine_name} {self.medicine.strength} '
                f'({self.medicine.get_dosage_form_display()}) with a different '
                'expiration date. Verify the batch details before recording '
                'this delivery.'
            )
        selected_row_matches = (
            (self.medicine.batch_number or '').strip().lower() == batch_number.lower()
            and self.medicine.expiration_date == expiration_date
        )
        if selected_row_matches:
            self.existing_batch = self.medicine
        else:
            self.existing_batch = (
                candidates.filter(expiration_date=expiration_date)
                .order_by('id').first()
            )
        return cleaned

    def save(self, user):
        data = self.cleaned_data
        quantity = data['quantity_received']
        token = data.get('submission_token') or uuid.uuid4()
        duplicate = MedicineStockMovement.objects.filter(
            submission_token=token).select_related('medicine').first()
        if duplicate:
            return duplicate.medicine, False, True

        try:
            with transaction.atomic():
                if self.existing_batch:
                    batch = MedicineInventory.objects.select_for_update().get(
                        pk=self.existing_batch.pk)
                    batch.quantity_received += quantity
                    batch.quantity_available += quantity
                    batch.save(update_fields=[
                        'quantity_received', 'quantity_available', 'updated_at'])
                    created = False
                else:
                    batch = MedicineInventory.objects.create(
                        medicine_name=self.medicine.medicine_name,
                        generic_name=self.medicine.generic_name,
                        strength=self.medicine.strength,
                        dosage_form=self.medicine.dosage_form,
                        unit=self.medicine.unit,
                        batch_number=data['batch_number'],
                        expiration_date=data['expiration_date'],
                        quantity_received=quantity,
                        quantity_available=quantity,
                        reorder_level=self.medicine.reorder_level,
                        source_supplier=data['source_supplier'],
                        date_received=data['date_received'],
                        remarks=data['remarks'],
                        created_by=user,
                    )
                    created = True
                MedicineStockMovement.objects.create(
                    medicine=batch,
                    movement_type='stock_in',
                    quantity=quantity,
                    supplier=data['source_supplier'],
                    date_received=data['date_received'],
                    time_received=data['time_received'],
                    batch_number=data['batch_number'],
                    expiration_date=data['expiration_date'],
                    moved_by=user,
                    notes=data['remarks'],
                    submission_token=token,
                )
        except IntegrityError:
            duplicate = MedicineStockMovement.objects.filter(
                submission_token=token).select_related('medicine').first()
            if duplicate:
                return duplicate.medicine, False, True
            raise
        return batch, created, False


class VaccineStockMovementForm(forms.ModelForm):
    class Meta:
        model = VaccineStockMovement
        fields = ['movement_type', 'quantity', 'reference', 'notes']
        widgets = {
            'movement_type': forms.Select(attrs={'class': 'form-select'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-input'}),
            'reference': forms.TextInput(attrs={'class': 'form-input'}),
            'notes': forms.Textarea(attrs={'rows': 3, 'class': 'form-textarea'}),
        }
