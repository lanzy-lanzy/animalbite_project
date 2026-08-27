from django import forms
from .models import ClinicalAssessment

class ClinicalAssessmentForm(forms.ModelForm):
    class Meta:
        model = ClinicalAssessment
        fields = [
            'chief_complaint', 'history_present_illness',
            'exposure_narrative', 'animal_type_verified', 'animal_owned_verified', 'animal_vax_verified',
            'wound_washed_verified', 'body_part_verified', 'number_of_wounds_verified', 'date_time_bite_verified',
            'exam_heent', 'exam_chest_lungs', 'exam_cardiac', 'exam_abdomen',
            'skin_wound_description', 'systemic_review',
            'category_confirmed', 'pep_indicated', 'rig_indicated', 'tt_indicated',
            'vaccine_brand_plan', 'dose_schedule_plan', 'treatment_plan', 'prescription',
            'follow_up_instructions', 'additional_notes',
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
            'exam_heent': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'HEENT findings'}),
            'exam_chest_lungs': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Chest / Lungs'}),
            'exam_cardiac': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Cardiac'}),
            'exam_abdomen': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Abdomen'}),
            'skin_wound_description': forms.Textarea(attrs={'rows': 3, 'class': 'form-input', 'placeholder': 'Size, depth, edges, bleeding, contamination...'}),
            'systemic_review': forms.Textarea(attrs={'rows': 2, 'class': 'form-input', 'placeholder': 'Fever, neuro signs, other systems...'}),
            'category_confirmed': forms.Select(attrs={'class': 'form-input font-bold'}),
            'vaccine_brand_plan': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g., Verorab / Abhayrab'}),
            'dose_schedule_plan': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g., Days 0, 3, 7, 14, 28 (Essen)'}),
            'treatment_plan': forms.Textarea(attrs={'rows': 3, 'class': 'form-input', 'placeholder': 'Wound care, PEP, RIG, antibiotics, TT, referral...'}),
            'prescription': forms.Textarea(attrs={'rows': 2, 'class': 'form-input', 'placeholder': 'e.g., Amoxicillin 500mg TID x 5 days, wound wash, etc.'}),
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
        for f in ['chief_complaint','history_present_illness','exposure_narrative','skin_wound_description','treatment_plan','prescription']:
            self.fields[f].required = False
