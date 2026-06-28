from django import forms
from .models import VaccinationSchedule, VaccineDose, FollowUpRecord, AnimalObservation

class VaccinationScheduleForm(forms.ModelForm):
    class Meta:
        model = VaccinationSchedule
        exclude = ['bite_case', 'created_by']
        widgets = {
            'rig_given': forms.CheckboxInput(attrs={'class': 'h-4 w-4 text-[#8A0303] focus:ring-[#8A0303] border-gray-300 rounded'}),
            'tetanus_toxoid_given': forms.CheckboxInput(attrs={'class': 'h-4 w-4 text-[#8A0303] focus:ring-[#8A0303] border-gray-300 rounded'}),
            'antibiotics_given': forms.CheckboxInput(attrs={'class': 'h-4 w-4 text-[#8A0303] focus:ring-[#8A0303] border-gray-300 rounded'}),
            'other_medicine': forms.Textarea(attrs={'rows': 3, 'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'doctor_notes': forms.Textarea(attrs={'rows': 4, 'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
        }

class VaccineDoseForm(forms.ModelForm):
    class Meta:
        model = VaccineDose
        fields = ['dose_label', 'scheduled_date', 'actual_date', 'vaccine_brand', 'batch_number', 'expiration_date', 'dose_status', 'remarks']
        widgets = {
            'dose_label': forms.TextInput(attrs={'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'scheduled_date': forms.DateInput(attrs={'type': 'date', 'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'actual_date': forms.DateInput(attrs={'type': 'date', 'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'vaccine_brand': forms.TextInput(attrs={'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'batch_number': forms.TextInput(attrs={'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'expiration_date': forms.DateInput(attrs={'type': 'date', 'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'dose_status': forms.Select(attrs={'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'remarks': forms.Textarea(attrs={'rows': 3, 'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
        }

class FollowUpRecordForm(forms.ModelForm):
    class Meta:
        model = FollowUpRecord
        exclude = ['bite_case', 'contacted_by']
        widgets = {
            'follow_up_date': forms.DateInput(attrs={'type': 'date', 'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'status': forms.Select(attrs={'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'notes': forms.Textarea(attrs={'rows': 3, 'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
        }

class AnimalObservationForm(forms.ModelForm):
    class Meta:
        model = AnimalObservation
        exclude = ['bite_case', 'created_by']
        widgets = {
            'animal_owner_name': forms.TextInput(attrs={'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'owner_contact': forms.TextInput(attrs={'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'animal_location': forms.Textarea(attrs={'rows': 3, 'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'start_date': forms.DateInput(attrs={'type': 'date', 'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'observation_notes': forms.Textarea(attrs={'rows': 3, 'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'daily_status': forms.Textarea(attrs={'rows': 3, 'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'final_condition': forms.Select(attrs={'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'final_remarks': forms.Textarea(attrs={'rows': 3, 'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
        }
