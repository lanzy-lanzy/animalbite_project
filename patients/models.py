from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError

class Barangay(models.Model):
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "Barangays"
        ordering = ['name']

    def __str__(self):
        return self.name

class Patient(models.Model):
    SEX_CHOICES = [('male', 'Male'), ('female', 'Female')]
    CIVIL_STATUS = [('single', 'Single'), ('married', 'Married'), ('widowed', 'Widowed'), ('separated', 'Separated'), ('divorced', 'Divorced')]

    patient_number = models.CharField(max_length=20, unique=True)
    account = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='patient_profile',
    )
    first_name = models.CharField(max_length=100)
    middle_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100)
    suffix = models.CharField(max_length=20, blank=True)
    birthdate = models.DateField()
    age = models.IntegerField(editable=False, default=0)
    sex = models.CharField(max_length=10, choices=SEX_CHOICES)
    civil_status = models.CharField(max_length=20, choices=CIVIL_STATUS, default='single')
    contact_number = models.CharField(max_length=20)
    address = models.TextField()
    barangay = models.ForeignKey(Barangay, on_delete=models.SET_NULL, null=True)
    parent_guardian = models.CharField(max_length=200, blank=True, help_text="For minors")
    emergency_contact_name = models.CharField(max_length=200, blank=True)
    emergency_contact_number = models.CharField(max_length=20, blank=True)
    medical_history = models.TextField(blank=True)
    allergy_info = models.TextField(blank=True)
    previous_rabies_vax = models.TextField(blank=True)
    remarks = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_archived = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.last_name}, {self.first_name} ({self.patient_number})"

    def clean(self):
        super().clean()
        from utils.phone import validate_ph_number
        if self.contact_number:
            validate_ph_number(self.contact_number)
        if self.emergency_contact_number:
            validate_ph_number(self.emergency_contact_number)

    def save(self, *args, **kwargs):
        from datetime import date
        from utils.phone import normalize_to_09
        # Auto-normalize to 09... local format for consistency (Semaphore will convert to 639...)
        if self.contact_number:
            n09 = normalize_to_09(self.contact_number)
            if n09:
                self.contact_number = n09
        if self.emergency_contact_number:
            n09 = normalize_to_09(self.emergency_contact_number)
            if n09:
                self.emergency_contact_number = n09
        if self.birthdate:
            today = date.today()
            self.age = today.year - self.birthdate.year - ((today.month, today.day) < (self.birthdate.month, self.birthdate.day))
        # Validate before save (won't raise on already valid)
        try:
            self.full_clean(exclude=['patient_number', 'created_by', 'barangay', 'account'])
        except ValidationError as e:
            # If emergency/contact invalid, keep original but will still save? Let form handle validation.
            # For direct model saves (like pre_reg conversion) we want to ensure valid; if invalid, keep original and let error surface via form next time.
            # Re-raise only for contact_number which is critical for SMS
            if 'contact_number' in e.message_dict:
                raise
        super().save(*args, **kwargs)

    def full_name(self):
        parts = [self.first_name]
        if self.middle_name:
            parts.append(self.middle_name)
        parts.append(self.last_name)
        if self.suffix:
            parts.append(self.suffix)
        return " ".join(parts)
