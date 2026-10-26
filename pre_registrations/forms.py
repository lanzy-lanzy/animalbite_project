from datetime import datetime

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from utils.phone import normalize_to_09, validate_ph_number

from .models import PreRegistration


INPUT_CLASS = "form-input"
SELECT_CLASS = "form-select"
TEXTAREA_CLASS = "form-textarea"
NOTES_CLASS = f"{TEXTAREA_CLASS} form-notes"
User = get_user_model()


class PreRegistrationForm(forms.ModelForm):
    bite_date = forms.DateField(
        label="Date of bite",
        widget=forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}),
    )
    bite_time = forms.TimeField(
        label="Time of bite",
        widget=forms.TimeInput(attrs={"type": "time", "class": INPUT_CLASS}),
    )

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
            "bite_date",
            "bite_time",
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
            "sex": forms.Select(attrs={"class": SELECT_CLASS}),
            "contact_number": forms.TextInput(attrs={"class": INPUT_CLASS, "type": "tel", "placeholder": "09123456789", "pattern": r"^(09\d{9}|639\d{9}|\+639\d{9})$", "title": "09XXXXXXXXX or 639XXXXXXXXX or +639XXXXXXXXX"}),
            "address": forms.Textarea(attrs={"rows": 3, "class": TEXTAREA_CLASS}),
            "barangay": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "guardian_name": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "guardian_contact_number": forms.TextInput(attrs={"class": INPUT_CLASS, "type": "tel", "placeholder": "09123456789", "pattern": r"^(09\d{9}|639\d{9}|\+639\d{9})$", "title": "09XXXXXXXXX or 639XXXXXXXXX or +639XXXXXXXXX"}),
            "emergency_contact_name": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "emergency_contact_number": forms.TextInput(attrs={"class": INPUT_CLASS, "type": "tel", "placeholder": "09123456789", "pattern": r"^(09\d{9}|639\d{9}|\+639\d{9})$", "title": "09XXXXXXXXX or 639XXXXXXXXX or +639XXXXXXXXX"}),
            "incident_place": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "incident_barangay": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "animal_type": forms.Select(attrs={"class": SELECT_CLASS}),
            "animal_ownership": forms.Select(attrs={"class": SELECT_CLASS}),
            "animal_vaccination_status": forms.Select(attrs={"class": SELECT_CLASS}),
            "animal_condition": forms.Select(attrs={"class": SELECT_CLASS}),
            "exposure_type": forms.Select(attrs={"class": SELECT_CLASS}),
            "body_part_affected": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "number_of_wounds": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 1}),
            "wound_washed": forms.CheckboxInput(attrs={"class": "form-check h-4 w-4 rounded border-cream-300"}),
            "first_aid_given": forms.Textarea(attrs={"rows": 3, "class": TEXTAREA_CLASS}),
            "remarks": forms.Textarea(attrs={"rows": 3, "class": NOTES_CLASS}),
            "consent_given": forms.CheckboxInput(attrs={"class": "form-check h-4 w-4 rounded border-cream-300"}),
        }
        labels = {
            "animal_vaccination_status": "Animal vaccination status",
            "wound_washed": "Was the wound washed immediately?",
            "consent_given": "I confirm that the information I provided is true and correct. I agree that my information will be used by the Municipal Health Office for animal bite case recording, treatment processing, follow-up, and reporting purposes.",
        }
        help_texts = {
            "contact_number": "Required for SMS reminders (RHUDumingag): 09123456789 / 639123456789 / +639123456789.",
            "guardian_contact_number": "Optional, must be valid PH mobile if for minor (fallback SMS).",
            "emergency_contact_number": "Optional, valid PH mobile for fallback.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and self.instance.bite_datetime:
            local_bite = timezone.localtime(self.instance.bite_datetime)
            self.initial.setdefault("bite_date", local_bite.date())
            self.initial.setdefault(
                "bite_time", local_bite.time().replace(second=0, microsecond=0)
            )

    def clean(self):
        cleaned_data = super().clean()
        bite_date = cleaned_data.get("bite_date")
        bite_time = cleaned_data.get("bite_time")
        if bite_date and bite_time:
            bite_datetime = datetime.combine(bite_date, bite_time)
            if timezone.is_naive(bite_datetime):
                bite_datetime = timezone.make_aware(
                    bite_datetime, timezone.get_current_timezone()
                )
            cleaned_data["bite_datetime"] = bite_datetime

        if self.instance.pk is None:
            duplicates = PreRegistration.find_duplicates(
                first_name=cleaned_data.get("first_name", ""),
                last_name=cleaned_data.get("last_name", ""),
                contact_number=cleaned_data.get("contact_number", ""),
                exclude_account_id=getattr(getattr(self, "account", None), "pk", None),
            )
            if duplicates.exists():
                numbers = ", ".join(
                    duplicates.values_list("pre_registration_number", flat=True)[:3]
                )
                self.add_error(
                    None,
                    "A registration with the same name or contact number already "
                    f"exists ({numbers}). If you already registered, please check "
                    "your registration status or log in to your patient account.",
                )
        return cleaned_data

    def _post_clean(self):
        # bite_datetime is intentionally not a form field (the UI collects a
        # separate date and time), so apply the combined value before the
        # model instance is validated and saved.
        self.instance.bite_datetime = (
            self.cleaned_data.get("bite_datetime") or self.instance.bite_datetime
        )
        super()._post_clean()

    def clean_contact_number(self):
        val = self.cleaned_data.get("contact_number", "").strip()
        validate_ph_number(val)
        return normalize_to_09(val) or val

    def clean_guardian_contact_number(self):
        val = self.cleaned_data.get("guardian_contact_number", "").strip()
        if not val:
            return val
        validate_ph_number(val)
        return normalize_to_09(val) or val

    def clean_emergency_contact_number(self):
        val = self.cleaned_data.get("emergency_contact_number", "").strip()
        if not val:
            return val
        validate_ph_number(val)
        return normalize_to_09(val) or val

    def clean_consent_given(self):
        consent = self.cleaned_data.get("consent_given")
        if not consent:
            raise forms.ValidationError("You must confirm consent before submitting.")
        return consent


class PatientSignupPreRegistrationForm(PreRegistrationForm):
    username = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "autocomplete": "username"}),
        help_text="Use this username when you return to track your treatment.",
    )
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={"class": INPUT_CLASS, "autocomplete": "email"})
    )
    password1 = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={"class": INPUT_CLASS, "autocomplete": "new-password"}),
    )
    password2 = forms.CharField(
        label="Confirm password",
        widget=forms.PasswordInput(attrs={"class": INPUT_CLASS, "autocomplete": "new-password"}),
    )

    def __init__(self, *args, account=None, **kwargs):
        self.account = account if getattr(account, "is_authenticated", False) else None
        super().__init__(*args, **kwargs)
        if self.account:
            for field_name in ("username", "email", "password1", "password2"):
                self.fields.pop(field_name)

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("That username is already in use.")
        return username

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists. Please log in instead.")
        return email

    def clean(self):
        cleaned_data = super().clean()
        if self.account:
            return cleaned_data

        password1 = cleaned_data.get("password1")
        password2 = cleaned_data.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "The two passwords do not match.")
        if password1:
            candidate = User(
                username=cleaned_data.get("username", ""),
                email=cleaned_data.get("email", ""),
                first_name=cleaned_data.get("first_name", ""),
                last_name=cleaned_data.get("last_name", ""),
                role="patient",
            )
            try:
                validate_password(password1, candidate)
            except ValidationError as error:
                self.add_error("password1", error)
        return cleaned_data

    @transaction.atomic
    def save(self, commit=True):
        if not commit:
            raise ValueError("Patient signup preregistrations must be saved atomically.")

        account = self.account
        if account is None:
            account = User.objects.create_user(
                username=self.cleaned_data["username"],
                email=self.cleaned_data["email"],
                password=self.cleaned_data["password1"],
                first_name=self.cleaned_data["first_name"],
                last_name=self.cleaned_data["last_name"],
                mobile=self.cleaned_data["contact_number"],
                role="patient",
            )
        record = super().save(commit=False)
        record.account = account
        record.save()
        self.save_m2m()
        self.account = account
        return record


class StaffPreRegistrationForm(PreRegistrationForm):
    class Meta(PreRegistrationForm.Meta):
        fields = [field for field in PreRegistrationForm.Meta.fields if field != "consent_given"] + ["status"]
        widgets = {
            **PreRegistrationForm.Meta.widgets,
            "status": forms.Select(attrs={"class": SELECT_CLASS}),
        }

    def clean_consent_given(self):
        return self.cleaned_data.get("consent_given", True)


class StatusLookupForm(forms.Form):
    pre_registration_number = forms.CharField(
        label="Registration number",
        max_length=20,
        widget=forms.TextInput(
            attrs={
                "class": INPUT_CLASS,
                "placeholder": "AB-YYYYMMDD-0001",
                "autocomplete": "off",
            }
        ),
    )
