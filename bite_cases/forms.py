from django import forms
from .models import AnimalBiteCase, ExposureClassification, MedicalNote

class AnimalBiteCaseForm(forms.ModelForm):
    class Meta:
        model = AnimalBiteCase
        exclude = ['case_number', 'created_by', 'case_status', 'is_locked', 'date_time_consultation']
        widgets = {
            'patient': forms.Select(attrs={'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'date_time_bite': forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'place_of_incident': forms.TextInput(attrs={'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'incident_barangay': forms.TextInput(attrs={'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'animal_type': forms.Select(attrs={'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'animal_other': forms.TextInput(attrs={'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'animal_ownership': forms.Select(attrs={'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'animal_vax_status': forms.Select(attrs={'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'animal_condition': forms.Select(attrs={'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'exposure_type': forms.Select(attrs={'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'body_part_affected': forms.TextInput(attrs={'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'number_of_wounds': forms.NumberInput(attrs={'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'wound_description': forms.Textarea(attrs={'rows': 3, 'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'wound_washed_immediately': forms.CheckboxInput(attrs={'class': 'h-4 w-4 text-[#8A0303] focus:ring-[#8A0303] border-gray-300 rounded'}),
            'first_aid_given': forms.Textarea(attrs={'rows': 3, 'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'initial_consultation_notes': forms.Textarea(attrs={'rows': 3, 'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
        }

class ExposureClassificationForm(forms.ModelForm):
    class Meta:
        model = ExposureClassification
        exclude = ['bite_case', 'classified_by', 'classification_date']
        widgets = {
            'category': forms.Select(attrs={'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'medical_notes': forms.Textarea(attrs={'rows': 4, 'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'recommended_action': forms.Textarea(attrs={'rows': 3, 'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'treatment_plan': forms.Textarea(attrs={'rows': 3, 'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
        }

class MedicalNoteForm(forms.ModelForm):
    class Meta:
        model = MedicalNote
        fields = ['note']
        widgets = {
            'note': forms.Textarea(attrs={'rows': 3, 'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent', 'placeholder': 'Add medical note...'}),
        }


class PatientBiteReportForm(forms.ModelForm):
    """For logged-in patients to report another bite directly without re-entering personal info."""
    class Meta:
        model = AnimalBiteCase
        fields = [
            'date_time_bite',
            'place_of_incident',
            'incident_barangay',
            'animal_type',
            'animal_other',
            'animal_ownership',
            'animal_vax_status',
            'animal_condition',
            'exposure_type',
            'body_part_affected',
            'number_of_wounds',
            'wound_description',
            'wound_washed_immediately',
            'first_aid_given',
            'initial_consultation_notes',
        ]
        widgets = {
            'date_time_bite': forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': 'form-input'}),
            'place_of_incident': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g., Purok 3, Dumingag'}),
            'incident_barangay': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g., Poblacion'}),
            'animal_type': forms.Select(attrs={'class': 'form-input'}),
            'animal_other': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'If Other, specify'}),
            'animal_ownership': forms.Select(attrs={'class': 'form-input'}),
            'animal_vax_status': forms.Select(attrs={'class': 'form-input'}),
            'animal_condition': forms.Select(attrs={'class': 'form-input'}),
            'exposure_type': forms.Select(attrs={'class': 'form-input'}),
            'body_part_affected': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g., Left leg, Right hand'}),
            'number_of_wounds': forms.NumberInput(attrs={'class': 'form-input', 'min': 1}),
            'wound_description': forms.Textarea(attrs={'rows': 3, 'class': 'form-input', 'placeholder': 'Describe wounds...'}),
            'wound_washed_immediately': forms.CheckboxInput(attrs={'class': 'h-4 w-4 text-emerald-700 focus:ring-emerald-700 border-line rounded'}),
            'first_aid_given': forms.Textarea(attrs={'rows': 3, 'class': 'form-input', 'placeholder': 'What first aid was given?'}),
            'initial_consultation_notes': forms.Textarea(attrs={'rows': 3, 'class': 'form-input', 'placeholder': 'Any additional notes...'}),
        }
        labels = {
            'date_time_bite': 'Date & time of bite',
            'wound_washed_immediately': 'Was the wound washed immediately?',
        }
        help_texts = {
            'date_time_bite': 'When did the bite happen?',
            'place_of_incident': 'Where did it happen?',
        }
