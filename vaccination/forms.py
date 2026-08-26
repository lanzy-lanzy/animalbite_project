from django import forms
from .models import VaccinationSchedule, VaccineDose, FollowUpRecord, AnimalObservation

class VaccinationScheduleForm(forms.ModelForm):
    class Meta:
        model = VaccinationSchedule
        exclude = ['bite_case', 'created_by']
        widgets = {
            'rig_given': forms.CheckboxInput(attrs={'class': 'h-4 w-4 text-emerald-700 focus:ring-emerald-700 border-line rounded'}),
            'tetanus_toxoid_given': forms.CheckboxInput(attrs={'class': 'h-4 w-4 text-emerald-700 focus:ring-emerald-700 border-line rounded'}),
            'antibiotics_given': forms.CheckboxInput(attrs={'class': 'h-4 w-4 text-emerald-700 focus:ring-emerald-700 border-line rounded'}),
            'other_medicine': forms.Textarea(attrs={'rows': 3, 'class': 'form-input'}),
            'doctor_notes': forms.Textarea(attrs={'rows': 4, 'class': 'form-input'}),
        }

class VaccineDoseForm(forms.ModelForm):
    class Meta:
        model = VaccineDose
        fields = ['dose_label', 'scheduled_date', 'actual_date', 'vaccine_brand', 'batch_number', 'expiration_date', 'dose_status', 'remarks']
        widgets = {
            'dose_label': forms.TextInput(attrs={'class': 'form-input'}),
            'scheduled_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-input'}),
            'actual_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-input'}),
            'vaccine_brand': forms.TextInput(attrs={'class': 'form-input'}),
            'batch_number': forms.TextInput(attrs={'class': 'form-input'}),
            'expiration_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-input'}),
            'dose_status': forms.Select(attrs={'class': 'form-input'}),
            'remarks': forms.Textarea(attrs={'rows': 3, 'class': 'form-input'}),
        }

class FollowUpRecordForm(forms.ModelForm):
    class Meta:
        model = FollowUpRecord
        exclude = ['bite_case', 'contacted_by']
        widgets = {
            'follow_up_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-input'}),
            'status': forms.Select(attrs={'class': 'form-input'}),
            'notes': forms.Textarea(attrs={'rows': 3, 'class': 'form-input'}),
        }

class AnimalObservationForm(forms.ModelForm):
    class Meta:
        model = AnimalObservation
        exclude = ['bite_case', 'created_by']
        widgets = {
            'animal_owner_name': forms.TextInput(attrs={'class': 'form-input'}),
            'owner_contact': forms.TextInput(attrs={'class': 'form-input'}),
            'animal_location': forms.Textarea(attrs={'rows': 3, 'class': 'form-input'}),
            'start_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-input'}),
            'observation_notes': forms.Textarea(attrs={'rows': 3, 'class': 'form-input'}),
            'daily_status': forms.Textarea(attrs={'rows': 3, 'class': 'form-input'}),
            'final_condition': forms.Select(attrs={'class': 'form-input'}),
            'final_remarks': forms.Textarea(attrs={'rows': 3, 'class': 'form-input'}),
        }
