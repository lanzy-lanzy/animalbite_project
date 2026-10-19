from django import forms
from .models import VaccineInventory, SupplyInventory, VaccineStockMovement

class VaccineInventoryForm(forms.ModelForm):
    class Meta:
        model = VaccineInventory
        exclude = ['quantity_available', 'quantity_used', 'created_by']
        widgets = {
            'vaccine_name': forms.TextInput(attrs={'class': 'form-input'}),
            'brand': forms.TextInput(attrs={'class': 'form-input'}),
            'batch_number': forms.TextInput(attrs={'class': 'form-input'}),
            'expiration_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-input'}),
            'quantity_received': forms.NumberInput(attrs={'class': 'form-input'}),
            'source_supplier': forms.TextInput(attrs={'class': 'form-input'}),
            'remarks': forms.Textarea(attrs={'rows': 3, 'class': 'form-textarea'}),
        }

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
