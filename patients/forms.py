from django import forms
from utils.phone import normalize_to_09, validate_ph_number
from .models import Patient, Barangay

class PatientForm(forms.ModelForm):
    class Meta:
        model = Patient
        fields = '__all__'
        exclude = ['patient_number', 'age', 'created_by', 'is_archived']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'middle_name': forms.TextInput(attrs={'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'last_name': forms.TextInput(attrs={'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'suffix': forms.TextInput(attrs={'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'birthdate': forms.DateInput(attrs={'type': 'date', 'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'sex': forms.Select(attrs={'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'civil_status': forms.Select(attrs={'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'contact_number': forms.TextInput(attrs={'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent', 'type': 'tel', 'placeholder': '09123456789', 'pattern': '^(09\\d{9}|639\\d{9}|\\+639\\d{9})$', 'title': '09XXXXXXXXX or 639XXXXXXXXX or +639XXXXXXXXX'}),
            'address': forms.Textarea(attrs={'rows': 3, 'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'barangay': forms.Select(attrs={'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'parent_guardian': forms.TextInput(attrs={'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'emergency_contact_name': forms.TextInput(attrs={'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'emergency_contact_number': forms.TextInput(attrs={'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent', 'type': 'tel', 'placeholder': '09123456789', 'pattern': '^(09\\d{9}|639\\d{9}|\\+639\\d{9})$', 'title': '09XXXXXXXXX or 639XXXXXXXXX or +639XXXXXXXXX'}),
            'medical_history': forms.Textarea(attrs={'rows': 3, 'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'allergy_info': forms.Textarea(attrs={'rows': 3, 'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'previous_rabies_vax': forms.Textarea(attrs={'rows': 3, 'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'remarks': forms.Textarea(attrs={'rows': 3, 'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
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
