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

    def get_latest_vitals(self):
        return self.vital_signs.order_by('-taken_at', '-created_at').first()


class VitalSign(models.Model):
    GLUCOSE_TYPE = [('', '—'), ('random', 'Random'), ('fasting', 'Fasting')]
    APPEARANCE_CHOICES = [('', '—'), ('well', 'Well'), ('ill', 'Ill'), ('toxic', 'Toxic')]

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='vital_signs')
    taken_at = models.DateTimeField(help_text="When vitals were taken")
    # Core vitals
    bp_systolic = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name="BP Systolic (mmHg)")
    bp_diastolic = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name="BP Diastolic (mmHg)")
    temperature = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True, verbose_name="Temperature (°C)")
    pulse_rate = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name="Pulse Rate (bpm)")
    respiratory_rate = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name="Respiratory Rate (/min)")
    spo2 = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name="SpO₂ (%)")
    weight = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, verbose_name="Weight (kg)")
    height = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, verbose_name="Height (cm)")
    bmi = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True, verbose_name="BMI")
    pain_score = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name="Pain Score (0-10)")
    blood_glucose = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True, verbose_name="Blood Glucose (mg/dL)")
    glucose_type = models.CharField(max_length=10, choices=GLUCOSE_TYPE, blank=True, default='')
    general_appearance = models.CharField(max_length=10, choices=APPEARANCE_CHOICES, blank=True, default='')
    notes = models.TextField(blank=True, help_text="Allergy re-check or other notes")
    taken_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='taken_vitals')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-taken_at', '-created_at']
        verbose_name = "Vital Sign"
        verbose_name_plural = "Vital Signs"

    def __str__(self):
        return f"Vitals {self.patient.patient_number} @ {self.taken_at:%Y-%m-%d %H:%M}"

    def save(self, *args, **kwargs):
        # Auto-calc BMI if weight and height present and BMI not manually set or should recalc
        if self.weight and self.height:
            try:
                h_m = float(self.height) / 100
                if h_m > 0:
                    calc = float(self.weight) / (h_m * h_m)
                    # Only auto-set if BMI is None or if weight/height changed and BMI was previously auto
                    # For simplicity, always recalc if both present and BMI is empty or if we want to keep in sync
                    # If BMI already has value, we still recalc to keep consistent unless user manually overrides
                    # We'll recalc if BMI is None or if we detect change — here simply set if not provided or always update
                    # Keep manual override: if BMI is provided and weight/height unchanged, preserve; but we don't track changes, so auto if BMI is None
                    if self.bmi is None:
                        self.bmi = round(calc, 2)
                    else:
                        # If BMI differs significantly, update to calculated (assume user wants auto)
                        # But allow manual override: if BMI was manually entered and weight/height also entered, keep manual if close? Instead, auto-update only if BMI not set
                        pass
                    # If BMI was auto-calculated previously, keep updating — for now we update only when BMI is None to allow manual edit
                    # If you want always auto, uncomment next line:
                    # self.bmi = round(calc, 2)
            except Exception:
                pass
        super().save(*args, **kwargs)

    @property
    def bp_display(self):
        if self.bp_systolic and self.bp_diastolic:
            return f"{self.bp_systolic}/{self.bp_diastolic}"
        if self.bp_systolic:
            return f"{self.bp_systolic}/___"
        if self.bp_diastolic:
            return f"___/{self.bp_diastolic}"
        return ""

    @property
    def is_complete(self):
        # At least one vital filled
        return any([self.bp_systolic, self.bp_diastolic, self.temperature, self.pulse_rate, self.respiratory_rate, self.spo2, self.weight, self.height])
