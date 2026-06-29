from datetime import date

from django.conf import settings
from django.db import models, transaction
from django.urls import reverse
from django.utils import timezone


class PreRegistration(models.Model):
    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        FOR_VERIFICATION = "for_verification", "For Verification"
        VERIFIED = "verified", "Verified"
        CONVERTED = "converted", "Converted to Official Case"
        CANCELLED = "cancelled", "Cancelled"
        DUPLICATE = "duplicate", "Duplicate"
        NO_SHOW = "no_show", "No Show"

    SEX_CHOICES = [("male", "Male"), ("female", "Female")]
    ANIMAL_TYPES = [
        ("dog", "Dog"),
        ("cat", "Cat"),
        ("monkey", "Monkey"),
        ("bat", "Bat"),
        ("other", "Other"),
    ]
    OWNERSHIP = [("owned", "Owned"), ("stray", "Stray"), ("unknown", "Unknown")]
    VAX_STATUS = [
        ("vaccinated", "Vaccinated"),
        ("not_vaccinated", "Not Vaccinated"),
        ("unknown", "Unknown"),
    ]
    ANIMAL_CONDITION = [
        ("alive", "Alive"),
        ("dead", "Dead"),
        ("missing", "Missing"),
        ("under_observation", "Under Observation"),
    ]
    EXPOSURE_TYPE = [
        ("bite", "Bite"),
        ("scratch", "Scratch"),
        ("lick", "Lick on broken skin"),
        ("saliva", "Contact with saliva"),
        ("other", "Other"),
    ]

    pre_registration_number = models.CharField(max_length=20, unique=True, blank=True)
    qr_code = models.TextField(blank=True)
    first_name = models.CharField(max_length=100)
    middle_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100)
    suffix = models.CharField(max_length=20, blank=True)
    birthdate = models.DateField()
    age = models.PositiveIntegerField(default=0, editable=False)
    sex = models.CharField(max_length=10, choices=SEX_CHOICES)
    contact_number = models.CharField(max_length=20)
    address = models.TextField()
    barangay = models.CharField(max_length=100)
    guardian_name = models.CharField(max_length=200, blank=True)
    guardian_contact_number = models.CharField(max_length=20, blank=True)
    emergency_contact_name = models.CharField(max_length=200, blank=True)
    emergency_contact_number = models.CharField(max_length=20, blank=True)
    bite_datetime = models.DateTimeField()
    incident_place = models.CharField(max_length=255)
    incident_barangay = models.CharField(max_length=100)
    animal_type = models.CharField(max_length=20, choices=ANIMAL_TYPES)
    animal_ownership = models.CharField(max_length=20, choices=OWNERSHIP)
    animal_vaccination_status = models.CharField(max_length=20, choices=VAX_STATUS)
    animal_condition = models.CharField(max_length=20, choices=ANIMAL_CONDITION)
    exposure_type = models.CharField(max_length=20, choices=EXPOSURE_TYPE)
    body_part_affected = models.CharField(max_length=255)
    number_of_wounds = models.PositiveIntegerField(default=1)
    wound_washed = models.BooleanField(default=False)
    first_aid_given = models.TextField(blank=True)
    remarks = models.TextField(blank=True)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.SUBMITTED)
    consent_given = models.BooleanField(default=False)
    submitted_at = models.DateTimeField(default=timezone.now)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="verified_pre_registrations",
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    converted_patient = models.ForeignKey(
        "patients.Patient",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="source_pre_registrations",
    )
    converted_case = models.ForeignKey(
        "bite_cases.AnimalBiteCase",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="source_pre_registrations",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-submitted_at"]
        indexes = [
            models.Index(fields=["pre_registration_number"]),
            models.Index(fields=["status", "submitted_at"]),
            models.Index(fields=["barangay"]),
        ]

    def __str__(self):
        return f"{self.pre_registration_number} - {self.full_name()}"

    def save(self, *args, **kwargs):
        if self.birthdate:
            today = timezone.localdate()
            self.age = self._calculate_age(self.birthdate, today)
        if self.pre_registration_number:
            if not self.qr_code:
                self.qr_code = self._qr_payload()
            super().save(*args, **kwargs)
            return

        with transaction.atomic():
            self.pre_registration_number = self._next_number()
            self.qr_code = self._qr_payload()
            super().save(*args, **kwargs)

    def full_name(self):
        parts = [self.first_name]
        if self.middle_name:
            parts.append(self.middle_name)
        parts.append(self.last_name)
        if self.suffix:
            parts.append(self.suffix)
        return " ".join(parts)

    def possible_duplicates(self):
        return PreRegistration.objects.filter(
            first_name__iexact=self.first_name,
            last_name__iexact=self.last_name,
            birthdate=self.birthdate,
            contact_number=self.contact_number,
            bite_datetime__date=self.bite_datetime.date(),
        ).exclude(pk=self.pk)

    def _qr_payload(self):
        return reverse("pre_registrations:public_success", args=[self.pre_registration_number])

    @staticmethod
    def _calculate_age(birthdate, today):
        return today.year - birthdate.year - ((today.month, today.day) < (birthdate.month, birthdate.day))

    @classmethod
    def _next_number(cls):
        today = timezone.localdate()
        prefix = f"AB-{today:%Y%m%d}-"
        last = cls.objects.filter(pre_registration_number__startswith=prefix).order_by("-pre_registration_number").first()
        sequence = 1
        if last:
            sequence = int(last.pre_registration_number.rsplit("-", 1)[1]) + 1
        return f"{prefix}{sequence:04d}"
