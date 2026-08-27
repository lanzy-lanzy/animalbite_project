from django import forms
from django.utils import timezone
from utils.phone import normalize_to_09, validate_ph_number
from .models import Patient, Barangay, VitalSign

class PatientForm(forms.ModelForm):
    class Meta:
        model = Patient
        fields = '__all__'
        exclude = ['patient_number', 'age', 'created_by', 'is_archived']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Juan'}),
            'middle_name': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'M.'}),
            'last_name': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Dela Cruz'}),
            'suffix': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Jr / Sr'}),
            'birthdate': forms.DateInput(attrs={'type': 'date', 'class': 'form-input'}),
            'sex': forms.Select(attrs={'class': 'form-input'}),
            'civil_status': forms.Select(attrs={'class': 'form-input'}),
            'contact_number': forms.TextInput(attrs={'class': 'form-input', 'type': 'tel', 'placeholder': '09123456789', 'pattern': '^(09\\d{9}|639\\d{9}|\\+639\\d{9})$', 'title': '09XXXXXXXXX or 639XXXXXXXXX or +639XXXXXXXXX'}),
            'address': forms.Textarea(attrs={'rows': 2, 'class': 'form-input', 'placeholder': 'House #, Street, Barangay, Municipality'}),
            'barangay': forms.Select(attrs={'class': 'form-input'}),
            'parent_guardian': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'For minors'}),
            'emergency_contact_name': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Emergency contact person'}),
            'emergency_contact_number': forms.TextInput(attrs={'class': 'form-input', 'type': 'tel', 'placeholder': '09123456789', 'pattern': '^(09\\d{9}|639\\d{9}|\\+639\\d{9})$', 'title': '09XXXXXXXXX or 639XXXXXXXXX or +639XXXXXXXXX'}),
            'medical_history': forms.Textarea(attrs={'rows': 3, 'class': 'form-input', 'placeholder': 'Chronic illness, maintenance meds...'}),
            'allergy_info': forms.Textarea(attrs={'rows': 3, 'class': 'form-input', 'placeholder': 'Drug/food allergies — will be highlighted'}),
            'previous_rabies_vax': forms.Textarea(attrs={'rows': 3, 'class': 'form-input', 'placeholder': 'Previous anti-rabies vaccination history'}),
            'remarks': forms.Textarea(attrs={'rows': 2, 'class': 'form-input', 'placeholder': 'Additional notes'}),
        }
        help_texts = {
            'contact_number': 'Required for SMS reminders via RHUDumingag: 09123456789, 639123456789, or +639123456789.',
            'emergency_contact_number': 'Optional, but must be valid PH mobile if provided (for fallback SMS).',
        }

    def clean_contact_number(self):
        val = self.cleaned_data.get('contact_number', '').strip()
        validate_ph_number(val)
        n09 = normalize_to_09(val)
        return n09 or val

    def clean_emergency_contact_number(self):
        val = self.cleaned_data.get('emergency_contact_number', '').strip()
        if not val:
            return val
        validate_ph_number(val)
        n09 = normalize_to_09(val)
        return n09 or val


class VitalSignForm(forms.ModelForm):
    class Meta:
        model = VitalSign
        fields = ['taken_at', 'bp_systolic', 'bp_diastolic', 'temperature', 'pulse_rate', 'respiratory_rate', 'spo2', 'weight', 'height', 'bmi', 'pain_score', 'blood_glucose', 'glucose_type', 'general_appearance', 'notes']
        widgets = {
            'taken_at': forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': 'form-input'}),
            'bp_systolic': forms.NumberInput(attrs={'class': 'form-input', 'placeholder': '120', 'min': '50', 'max': '300'}),
            'bp_diastolic': forms.NumberInput(attrs={'class': 'form-input', 'placeholder': '80', 'min': '30', 'max': '200'}),
            'temperature': forms.NumberInput(attrs={'class': 'form-input', 'placeholder': '36.8', 'step': '0.1', 'min': '30', 'max': '45'}),
            'pulse_rate': forms.NumberInput(attrs={'class': 'form-input', 'placeholder': '72', 'min': '30', 'max': '220'}),
            'respiratory_rate': forms.NumberInput(attrs={'class': 'form-input', 'placeholder': '18', 'min': '8', 'max': '60'}),
            'spo2': forms.NumberInput(attrs={'class': 'form-input', 'placeholder': '98', 'min': '70', 'max': '100'}),
            'weight': forms.NumberInput(attrs={'class': 'form-input', 'placeholder': '68.5', 'step': '0.1', 'min': '1', 'max': '300'}),
            'height': forms.NumberInput(attrs={'class': 'form-input', 'placeholder': '165', 'step': '0.1', 'min': '30', 'max': '250'}),
            'bmi': forms.NumberInput(attrs={'class': 'form-input', 'placeholder': 'Auto', 'step': '0.01', 'min': '10', 'max': '60'}),
            'pain_score': forms.NumberInput(attrs={'class': 'form-input', 'placeholder': '0-10', 'min': '0', 'max': '10'}),
            'blood_glucose': forms.NumberInput(attrs={'class': 'form-input', 'placeholder': '95', 'step': '0.1', 'min': '20', 'max': '600'}),
            'glucose_type': forms.Select(attrs={'class': 'form-input'}),
            'general_appearance': forms.Select(attrs={'class': 'form-input'}),
            'notes': forms.Textarea(attrs={'rows': 2, 'class': 'form-input', 'placeholder': 'Allergy re-check, additional notes...'}),
        }
        help_texts = {
            'taken_at': 'Date and time vitals were taken (defaults to now).',
            'bmi': 'Auto-calculated from weight/height if left blank.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set default taken_at to now if not set
        if not self.instance.pk and not self.initial.get('taken_at'):
            self.fields['taken_at'].initial = timezone.localtime(timezone.now()).strftime('%Y-%m-%dT%H:%M')
        # Make taken_at required but provide default
        self.fields['taken_at'].required = True
        # Style: add required asterisk handled in template
