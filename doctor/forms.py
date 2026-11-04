from django import forms
from django.db.models import Q
from django.forms import BaseModelFormSet, modelformset_factory
from django.utils import timezone
from inventory.models import MedicineInventory, VaccineInventory
from .models import ClinicalAssessment, Prescription

DOSE_SCHEDULE_CHOICES = [
    ('1', '1 Dose'),
    ('2', '2 Doses'),
    ('3', '3 Doses'),
    ('4', '4 Doses'),
    ('5', '5 Doses'),
]

class ClinicalAssessmentForm(forms.ModelForm):
    # The selected option is the total number of doses in the schedule (1–5).
    dose_schedule = forms.ChoiceField(
        label='Dose schedule', choices=DOSE_SCHEDULE_CHOICES, required=True,
        widget=forms.Select(attrs={'class': 'form-input'}),
    )
    dose_schedule_date = forms.DateField(
        label='Date', required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-input'}),
    )

    class Meta:
        model = ClinicalAssessment
        fields = [
            'chief_complaint', 'history_present_illness',
            'exposure_narrative', 'animal_type_verified', 'animal_owned_verified', 'animal_vax_verified',
            'wound_washed_verified', 'body_part_verified', 'number_of_wounds_verified', 'date_time_bite_verified',
            'category_confirmed', 'pep_indicated', 'rig_indicated', 'tt_indicated',
            'vaccine_inventory', 'vaccine_brand_plan', 'vaccine_lot_number', 'vaccine_dose_ml',
            'vaccine_quantity', 'dose_protocol', 'first_dose_date', 'dose_schedule_plan',
            'treatment_plan', 'dosage_instructions', 'follow_up_instructions', 'additional_notes',
            'prc_number', 'status',
        ]
        widgets = {
            'chief_complaint': forms.Textarea(attrs={'rows': 2, 'class': 'form-input', 'placeholder': 'e.g., Dog bite on left leg 2 hours ago'}),
            'history_present_illness': forms.Textarea(attrs={'rows': 3, 'class': 'form-input', 'placeholder': 'Onset, duration, associated symptoms, prior first aid...'}),
            'exposure_narrative': forms.Textarea(attrs={'rows': 3, 'class': 'form-input', 'placeholder': 'Detailed exposure narrative as verified - animal, circumstance, location...'}),
            'animal_type_verified': forms.Select(attrs={'class': 'form-input'}),
            'animal_owned_verified': forms.Select(attrs={'class': 'form-input'}),
            'animal_vax_verified': forms.Select(attrs={'class': 'form-input'}),
            'body_part_verified': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g., Left lower leg, anterior'}),
            'number_of_wounds_verified': forms.NumberInput(attrs={'class': 'form-input', 'min': 0}),
            'date_time_bite_verified': forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': 'form-input'}),
            'category_confirmed': forms.Select(attrs={'class': 'form-input font-bold'}),
            'vaccine_inventory': forms.Select(attrs={'class': 'form-input'}),
            'vaccine_lot_number': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g., 24A0123'}),
            'vaccine_dose_ml': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.1', 'min': '0', 'placeholder': '0.5 mL'}),
            'vaccine_quantity': forms.NumberInput(attrs={'class': 'form-input', 'min': '0', 'placeholder': 'Vials / doses'}),
            'vaccine_brand_plan': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g., Verorab / Abhayrab'}),
            'dose_protocol': forms.Select(attrs={'class': 'form-input font-bold'}),
            'first_dose_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-input'}),
            'dose_schedule_plan': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g., Days 0, 3, 7, 14, 28 (Essen)'}),
            'treatment_plan': forms.Textarea(attrs={'rows': 3, 'class': 'form-input', 'placeholder': 'Wound care, PEP, RIG, antibiotics, TT, referral...'}),
            'dosage_instructions': forms.Textarea(attrs={
                'rows': 2, 'class': 'form-input',
                'placeholder': 'e.g., Amoxicillin 500 mg TID × 7 days; Paracetamol 500 mg every 4 hours as needed'}),
            'follow_up_instructions': forms.Textarea(attrs={'rows': 2, 'class': 'form-input', 'placeholder': 'Return on Day 3 for next dose, bring sheet, watch for signs...'}),
            'additional_notes': forms.Textarea(attrs={'rows': 2, 'class': 'form-input', 'placeholder': 'Allergy re-check, warnings, special instructions...'}),
            'prc_number': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'PRC License No.'}),
            'status': forms.Select(attrs={'class': 'form-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Make animal_type choices friendly
        self.fields['animal_type_verified'].widget = forms.Select(
            choices=[('', '— Select —'), ('dog','Dog'),('cat','Cat'),('monkey','Monkey'),('bat','Bat'),('other','Other')],
            attrs={'class': 'form-input'}
        )
        # Only live lots can be planned against; expired stock stays selectable on
        # an assessment that already recorded it, so history still renders.
        today = timezone.localdate()
        usable = Q(expiration_date__gt=today, quantity_available__gt=0)
        if self.instance.pk and self.instance.vaccine_inventory_id:
            usable |= Q(pk=self.instance.vaccine_inventory_id)
        self.fields['vaccine_inventory'].queryset = (
            VaccineInventory.objects.filter(usable).order_by('brand', 'expiration_date')
        )
        self.fields['vaccine_inventory'].empty_label = '— Not from stock list —'
        self.fields['vaccine_inventory'].label = 'Vaccine brand (from stock)'
        self.fields['vaccine_brand_plan'].label = 'Brand / preparation'
        self.fields['chief_complaint'].required = True
        for f in ['history_present_illness','exposure_narrative','treatment_plan']:
            self.fields[f].required = False
        if not self.is_bound:
            schedule = None
            if self.instance.bite_case_id:
                from vaccination.models import VaccinationSchedule
                schedule = VaccinationSchedule.objects.filter(
                    bite_case_id=self.instance.bite_case_id).first()
            if schedule:
                doses = list(schedule.doses.order_by('id'))
                if doses:
                    self.initial['dose_schedule'] = str(min(len(doses), len(DOSE_SCHEDULE_CHOICES)))
                    if doses[0].scheduled_date:
                        self.initial['dose_schedule_date'] = doses[0].scheduled_date
                        self.initial['first_dose_date'] = doses[0].scheduled_date
            if not self.initial.get('first_dose_date'):
                # An unbound form renders from self.initial (snapshotted from the instance in
                # super().__init__), so the Day 0 default belongs there, not on field.initial.
                self.initial['first_dose_date'] = self.instance.first_dose_date or timezone.localdate()
            if not self.initial.get('dose_schedule'):
                days = ClinicalAssessment.protocol_days(self.instance.dose_protocol or '')
                self.initial['dose_schedule'] = str(min(len(days), len(DOSE_SCHEDULE_CHOICES)))
            if not self.initial.get('dose_schedule_date'):
                self.initial['dose_schedule_date'] = self.initial['first_dose_date']

    def clean(self):
        cleaned = super().clean()
        protocol = cleaned.get('dose_protocol')
        date = cleaned.get('dose_schedule_date') or cleaned.get('first_dose_date')
        if date:
            cleaned['first_dose_date'] = date
        count = int(cleaned.get('dose_schedule') or 0)
        if protocol:
            days = ClinicalAssessment.protocol_days(protocol)
            count = count or len(days)
            cleaned['dose_schedule_plan'] = '%s (%d doses)' % (
                dict(ClinicalAssessment.DOSE_PROTOCOL_CHOICES).get(protocol, protocol), count)
            if not date:
                self.add_error(
                    'dose_schedule_date',
                    'Needed to date the %d doses of this protocol.' % count,
                )
        return cleaned


class PrescriptionForm(forms.ModelForm):
    """One medicine row on the encounter. Rows are saved as separate records."""

    class Meta:
        model = Prescription
        fields = ['medicine', 'quantity']
        widgets = {
            'medicine': forms.Select(attrs={'class': 'form-input rx-medicine'}),
            'quantity': forms.NumberInput(attrs={
                'class': 'form-input rx-quantity', 'min': 1, 'placeholder': 'Qty'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        today = timezone.localdate()
        usable = Q(expiration_date__gt=today, quantity_available__gt=0)
        if self.instance.pk and self.instance.medicine_id:
            # Keep the recorded medicine selectable so history still renders.
            usable |= Q(pk=self.instance.medicine_id)
        self.fields['medicine'].queryset = (
            MedicineInventory.objects.filter(usable)
            .order_by('medicine_name', 'strength', 'expiration_date')
        )
        self.fields['medicine'].label = 'Prescription (from medicine inventory)'
        self.fields['medicine'].empty_label = '— Select medicine —'
        self.fields['medicine'].label_from_instance = lambda med: med.label()
        self.fields['quantity'].required = False
        if self.instance.pk and self.instance.status == 'dispensed':
            for name in ('medicine', 'quantity'):
                self.fields[name].disabled = True

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('DELETE'):
            if self.instance.pk and self.instance.status == 'dispensed':
                raise forms.ValidationError(
                    'Dispensed medicines stay in the prescription history and cannot be removed.')
            return cleaned

        medicine = cleaned.get('medicine')
        quantity = cleaned.get('quantity')
        if not medicine:
            if quantity:
                raise forms.ValidationError('Select a medicine from inventory for this row.')
            return cleaned
        if quantity is None:
            self.add_error('quantity', 'Enter the quantity to dispense.')
        elif quantity < 1:
            self.add_error('quantity', 'Quantity must be at least 1.')
        elif (self.instance.pk is None
              or medicine.pk != self.instance.medicine_id
              or quantity != self.instance.quantity):
            # New or changed rows cannot ask for more than the shelf holds.
            # Unchanged rows are not blocked when stock drops later — that is
            # caught when staff confirms dispensing.
            if quantity > medicine.quantity_available:
                self.add_error(
                    'quantity',
                    'Only %d %s of %s available in stock.'
                    % (medicine.quantity_available, medicine.unit, medicine.medicine_name),
                )
        return cleaned


class BasePrescriptionFormSet(BaseModelFormSet):
    def add_fields(self, form, index):
        super().add_fields(form, index)
        if 'DELETE' not in form.fields:
            return
        form.fields['DELETE'].widget = forms.CheckboxInput(attrs={'class': 'hidden rx-delete'})
        if form.instance.pk and form.instance.status == 'dispensed':
            form.fields['DELETE'].disabled = True

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        seen = set()
        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get('DELETE'):
                continue
            medicine = form.cleaned_data.get('medicine')
            if not medicine:
                continue
            if medicine.pk in seen:
                form.add_error(
                    'medicine',
                    'This medicine is already prescribed above — combine it into one row '
                    'or choose a different medicine.',
                )
            seen.add(medicine.pk)


PrescriptionFormSet = modelformset_factory(
    Prescription, form=PrescriptionForm, formset=BasePrescriptionFormSet,
    extra=1, can_delete=True,
)
