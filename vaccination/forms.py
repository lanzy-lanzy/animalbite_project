from datetime import timedelta

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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.dose_status == 'pending':
            # Offer the protocol date and start the nurse on "Scheduled"; confirming
            # the date is still a separate explicit save.
            self.initial['scheduled_date'] = self.instance.expected_date()
            self.initial['dose_status'] = 'scheduled'

    def clean(self):
        cleaned = super().clean()
        dose = self.instance
        status = cleaned.get('dose_status')
        date = cleaned.get('scheduled_date')

        # Confirming a date is what moves a pending dose to Scheduled.
        if date and status == 'pending':
            cleaned['dose_status'] = status = 'scheduled'

        if status in ('scheduled', 'rescheduled') and not date:
            self.add_error('scheduled_date', 'Assign a date before scheduling this dose.')

        if date and status not in ('completed', 'cancelled') and dose.pk:
            first = dose.schedule.doses.order_by('id').first()
            if first and first.pk != dose.pk and first.scheduled_date:
                earliest = first.scheduled_date
                if dose.protocol_day is not None:
                    earliest = first.scheduled_date + timedelta(days=dose.protocol_day)
                if date < earliest:
                    label = ('Day %d of the protocol' % dose.protocol_day
                             if dose.protocol_day is not None else 'the 1st dose')
                    self.add_error(
                        'scheduled_date',
                        'This protocol requires %s or later (%s).'
                        % (earliest.strftime('%b %d, %Y'), label),
                    )

        if status == 'completed' and not cleaned.get('actual_date'):
            self.add_error('actual_date', 'Record the actual administration date when marking a dose Completed.')
        return cleaned

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
