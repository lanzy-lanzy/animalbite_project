from django import forms

from .models import PreRegistration


INPUT_CLASS = "w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent"


class PreRegistrationForm(forms.ModelForm):
    class Meta:
        model = PreRegistration
        fields = [
            "first_name",
            "middle_name",
            "last_name",
            "suffix",
            "birthdate",
            "sex",
            "contact_number",
            "address",
            "barangay",
            "guardian_name",
            "guardian_contact_number",
            "emergency_contact_name",
            "emergency_contact_number",
            "bite_datetime",
            "incident_place",
            "incident_barangay",
            "animal_type",
            "animal_ownership",
            "animal_vaccination_status",
            "animal_condition",
            "exposure_type",
            "body_part_affected",
            "number_of_wounds",
            "wound_washed",
            "first_aid_given",
            "remarks",
            "consent_given",
        ]
        widgets = {
            "first_name": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "middle_name": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "last_name": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "suffix": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "birthdate": forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}),
            "sex": forms.Select(attrs={"class": INPUT_CLASS}),
            "contact_number": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "address": forms.Textarea(attrs={"rows": 3, "class": INPUT_CLASS}),
            "barangay": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "guardian_name": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "guardian_contact_number": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "emergency_contact_name": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "emergency_contact_number": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "bite_datetime": forms.DateTimeInput(attrs={"type": "datetime-local", "class": INPUT_CLASS}),
            "incident_place": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "incident_barangay": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "animal_type": forms.Select(attrs={"class": INPUT_CLASS}),
            "animal_ownership": forms.Select(attrs={"class": INPUT_CLASS}),
            "animal_vaccination_status": forms.Select(attrs={"class": INPUT_CLASS}),
            "animal_condition": forms.Select(attrs={"class": INPUT_CLASS}),
            "exposure_type": forms.Select(attrs={"class": INPUT_CLASS}),
            "body_part_affected": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "number_of_wounds": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 1}),
            "wound_washed": forms.CheckboxInput(attrs={"class": "h-4 w-4 text-[#8A0303] focus:ring-[#8A0303] border-gray-300 rounded"}),
            "first_aid_given": forms.Textarea(attrs={"rows": 3, "class": INPUT_CLASS}),
            "remarks": forms.Textarea(attrs={"rows": 3, "class": INPUT_CLASS}),
            "consent_given": forms.CheckboxInput(attrs={"class": "h-4 w-4 text-[#8A0303] focus:ring-[#8A0303] border-gray-300 rounded"}),
        }
        labels = {
            "animal_vaccination_status": "Animal vaccination status",
            "wound_washed": "Was the wound washed immediately?",
            "consent_given": "I confirm that the information I provided is true and correct. I agree that my information will be used by the Municipal Health Office for animal bite case recording, treatment processing, follow-up, and reporting purposes.",
        }

    def clean_consent_given(self):
        consent = self.cleaned_data.get("consent_given")
        if not consent:
            raise forms.ValidationError("You must confirm consent before submitting.")
        return consent


class StaffPreRegistrationForm(PreRegistrationForm):
    class Meta(PreRegistrationForm.Meta):
        fields = [field for field in PreRegistrationForm.Meta.fields if field != "consent_given"] + ["status"]
        widgets = {
            **PreRegistrationForm.Meta.widgets,
            "status": forms.Select(attrs={"class": INPUT_CLASS}),
        }

    def clean_consent_given(self):
        return self.cleaned_data.get("consent_given", True)


class StatusLookupForm(forms.Form):
    pre_registration_number = forms.CharField(
        label="Pre-registration number",
        max_length=20,
        widget=forms.TextInput(
            attrs={
                "class": INPUT_CLASS,
                "placeholder": "AB-YYYYMMDD-0001",
                "autocomplete": "off",
            }
        ),
    )
