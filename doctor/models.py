from datetime import timedelta

from django.db import models
from django.conf import settings
from django.utils import timezone
from patients.models import Patient
from bite_cases.models import AnimalBiteCase


# PEP regimens the doctor can pick from on the encounter page. The day offsets
# become VaccineDose rows, so they must match a schedule the nurse can follow.
# The clinic-wide default (SystemSetting 'vaccination_days') is offered as its
# own option and is resolved at save time, not here.
DOSE_PROTOCOLS = {
    'essen': {'label': 'Essen — 5 doses IM (Days 0, 3, 7, 14, 28)', 'days': [0, 3, 7, 14, 28]},
    'zagreb': {'label': 'Zagreb — 3 visits IM (Days 0, 7, 21)', 'days': [0, 7, 21]},
    'clinic': {'label': 'Clinic default regimen', 'days': None},
}


class ClinicalAssessment(models.Model):
    """
    Doctor's clinical assessment for a bite case - digital replacement for Section 3
    of the patient information sheet (blank for clinician).
    Extended to capture findings, WHO category confirmation, and treatment recommendation
    in one unified record linked to patient + bite case.
    """
    CATEGORY_CHOICES = [
        ('category_i', 'Category I - Touching/feeding animal, licks on intact skin'),
        ('category_ii', 'Category II - Nibbling, minor scratches without bleeding'),
        ('category_iii', 'Category III - Transdermal bites/scratches, licks on broken skin, bat exposure'),
    ]
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('final', 'Final - Signed'),
    ]
    DOSE_PROTOCOL_CHOICES = (
        [('', '— Select protocol —')] + [(key, cfg['label']) for key, cfg in DOSE_PROTOCOLS.items()]
    )

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='clinical_assessments')
    bite_case = models.OneToOneField(AnimalBiteCase, on_delete=models.CASCADE, related_name='clinical_assessment', null=True, blank=True)
    # When bite_case is None, this is a general encounter (patient without case yet - doctor can create)
    encounter_date = models.DateTimeField(auto_now_add=True)
    assessed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='clinical_assessments')

    # --- Section 3A: Chief Complaint / HPI ---
    chief_complaint = models.TextField(blank=True, help_text="Chief complaint - patient's main concern")
    history_present_illness = models.TextField(blank=True, help_text="History of present illness - narrative")

    # --- Section 3B: Exposure Narrative (doctor-verified) ---
    exposure_narrative = models.TextField(blank=True, help_text="Detailed exposure narrative as verified by doctor")
    animal_type_verified = models.CharField(max_length=20, blank=True, help_text="Verified animal type")
    animal_owned_verified = models.CharField(max_length=20, blank=True, choices=[('owned','Owned'),('stray','Stray'),('unknown','Unknown')])
    animal_vax_verified = models.CharField(max_length=20, blank=True, choices=[('vaccinated','Vaccinated'),('not_vaccinated','Not Vaccinated'),('unknown','Unknown')])
    wound_washed_verified = models.BooleanField(null=True, blank=True, help_text="Wound washed immediately?")
    body_part_verified = models.CharField(max_length=255, blank=True)
    number_of_wounds_verified = models.PositiveIntegerField(null=True, blank=True)
    date_time_bite_verified = models.DateTimeField(null=True, blank=True)

    # --- Section 3C: Physical Exam / System Review ---
    exam_heent = models.TextField(blank=True, verbose_name="HEENT")
    exam_chest_lungs = models.TextField(blank=True, verbose_name="Chest / Lungs")
    exam_cardiac = models.TextField(blank=True, verbose_name="Cardiac")
    exam_abdomen = models.TextField(blank=True, verbose_name="Abdomen")
    skin_wound_description = models.TextField(blank=True, help_text="Detailed skin / wound description")
    systemic_review = models.TextField(blank=True, help_text="Systemic review notes")

    # --- Section 3D: Assessment & Plan ---
    category_confirmed = models.CharField(max_length=20, choices=CATEGORY_CHOICES, blank=True, help_text="WHO Bite Category confirmed by doctor")
    pep_indicated = models.BooleanField(default=False, verbose_name="PEP Indicated")
    rig_indicated = models.BooleanField(default=False, verbose_name="RIG Indicated")
    tt_indicated = models.BooleanField(default=False, verbose_name="TT Indicated")
    vaccine_brand_plan = models.CharField(max_length=100, blank=True, help_text="Vaccine brand / lot planned")
    dose_schedule_plan = models.TextField(blank=True, help_text="Dose schedule - e.g., Days 0,3,7,14,28")
    dose_protocol = models.CharField(max_length=20, blank=True, choices=DOSE_PROTOCOL_CHOICES,
                                     verbose_name="Dose schedule protocol",
                                     help_text="Protocol key used to generate the vaccination schedule")
    first_dose_date = models.DateField(null=True, blank=True, help_text="Day 0 of the schedule — later doses are dated from this")
    vaccine_inventory = models.ForeignKey('inventory.VaccineInventory', on_delete=models.SET_NULL, null=True, blank=True,
                                          related_name='assessment_plans',
                                          help_text="Planned vaccine lot — stock is only deducted when a dose is administered")
    vaccine_lot_number = models.CharField(max_length=100, blank=True, verbose_name="Lot / batch number")
    vaccine_dose_ml = models.CharField(max_length=20, blank=True, verbose_name="Volume per dose (mL)")
    vaccine_quantity = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name="Quantity (vials/doses)")
    treatment_plan = models.TextField(blank=True, help_text="Detailed treatment & management plan")
    prescription = models.TextField(blank=True, help_text="Prescription / medications")
    dosage_instructions = models.TextField(
        blank=True,
        help_text="Dosage instructions, frequency and duration — inventory strength is not the prescribed dose",
    )
    follow_up_instructions = models.TextField(blank=True, help_text="Follow-up instructions")
    additional_notes = models.TextField(blank=True, help_text="Additional notes / allergy re-check")

    # Doctor attestation
    prc_number = models.CharField(max_length=50, blank=True, help_text="PRC License No.")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='draft')
    is_finalized = models.BooleanField(default=False, help_text="Finalized and signed")
    finalized_at = models.DateTimeField(null=True, blank=True, help_text="When the doctor signed and finalized this assessment")
    attestation = models.TextField(blank=True, help_text="Electronic attestation recorded at finalization")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-encounter_date', '-created_at']
        verbose_name = "Clinical Assessment"
        verbose_name_plural = "Clinical Assessments"

    def __str__(self):
        cat = self.get_category_confirmed_display() if self.category_confirmed else "Unclassified"
        return f"Assessment {self.patient.patient_number} - {self.bite_case.case_number if self.bite_case else 'General'} - {cat}"

    @property
    def is_category_iii(self):
        return self.category_confirmed == 'category_iii'

    @property
    def category_label(self):
        """Short category name, e.g. 'Category II'."""
        display = self.get_category_confirmed_display()
        return display.split(' - ', 1)[0] if display else ''

    @property
    def category_description(self):
        """WHO description without the category prefix."""
        display = self.get_category_confirmed_display()
        parts = display.split(' - ', 1)
        return parts[1] if len(parts) > 1 else ''

    def save(self, *args, **kwargs):
        """A signed PEP assessment creates the dose schedule if the case has none.

        Planning never touches inventory stock — a vial is only counted as used
        when the nurse records the dose as administered.
        """
        super().save(*args, **kwargs)
        if not (self.bite_case_id and self.pep_indicated and self.is_finalized):
            return
        from vaccination.models import VaccinationSchedule
        from vaccination.schedule_utils import ensure_vaccination_schedule
        if VaccinationSchedule.objects.filter(bite_case_id=self.bite_case_id).exists():
            return
        days = self.protocol_days(self.dose_protocol) if self.dose_protocol else None
        ensure_vaccination_schedule(self.bite_case, created_by=self.assessed_by,
                                    days=days, base_date=self.first_dose_date)

    @staticmethod
    def build_dose_labels(protocol, base_date):
        """[(dose_label, date), ...] for a protocol. Empty without a Day 0 date."""
        if base_date is None:
            return []
        days = ClinicalAssessment.protocol_days(protocol)
        return [('Day %d' % d, base_date + timedelta(days=d)) for d in days]

    @staticmethod
    def protocol_days(protocol):
        """Dose offsets for a protocol key; 'clinic' / blank resolve to the clinic default."""
        from vaccination.schedule_utils import get_dose_days
        days = DOSE_PROTOCOLS.get(protocol, {}).get('days')
        if days is None:
            days = get_dose_days()
        return sorted(set(int(d) for d in days))

    @property
    def dose_plan_summary(self):
        """Human-readable dose dates for the selected protocol, e.g. 'Day 0 · Oct 30, 2026'."""
        if not self.dose_protocol:
            return ''
        base = self.first_dose_date or timezone.localdate()
        return ', '.join('%s · %s' % (label, date.strftime('%b %d, %Y'))
                         for label, date in ClinicalAssessment.build_dose_labels(self.dose_protocol, base))


class Prescription(models.Model):
    """One prescribed medicine on an encounter. Stock moves only on dispensing."""

    STATUS_CHOICES = [
        ('pending', 'Pending Dispensing'),
        ('dispensed', 'Dispensed'),
        ('cancelled', 'Cancelled'),
    ]

    assessment = models.ForeignKey(ClinicalAssessment, on_delete=models.CASCADE,
                                   related_name='prescriptions')
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='prescriptions')
    bite_case = models.ForeignKey(AnimalBiteCase, on_delete=models.CASCADE,
                                  related_name='prescriptions', null=True, blank=True)
    medicine = models.ForeignKey('inventory.MedicineInventory', on_delete=models.PROTECT,
                                 related_name='prescriptions')
    dose = models.CharField(
        max_length=100, blank=True,
        help_text="Legacy dose / volume per dose. New prescriptions record dosage in the assessment instructions",
    )
    quantity = models.PositiveIntegerField(help_text="Number of units dispensed")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    dispensed_at = models.DateTimeField(null=True, blank=True)
    dispensed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                     null=True, blank=True, related_name='dispensed_prescriptions')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['id']
        verbose_name = "Prescription"
        verbose_name_plural = "Prescriptions"

    def __str__(self):
        return f"{self.medicine.medicine_name} for {self.patient.patient_number}"

    @property
    def dose_unit(self):
        return self.medicine.dose_unit()

    MEASURE_UNITS = {'ml', 'l', 'g', 'mg', 'mcg', 'cc', 'iu', '%'}

    @property
    def quantity_display(self):
        """Readable quantity, e.g. '10 tablets' — measures like mL are never pluralized."""
        unit = (self.medicine.unit or '').strip()
        if not unit:
            return str(self.quantity)
        if unit.lower() in self.MEASURE_UNITS or '(' in unit:
            return f'{self.quantity} {unit}'
        if self.quantity == 1:
            singular = unit[:-1] if unit.lower().endswith('s') and not unit.lower().endswith('ss') else unit
            return f'1 {singular}'
        plural = unit if unit.lower().endswith('s') else f'{unit}s'
        return f'{self.quantity} {plural}'

    @property
    def summary(self):
        """Single-line text used by print sheets and legacy prescription displays."""
        if self.dose:
            return f"{self.medicine.label()} — {self.dose} × {self.quantity} {self.medicine.unit}"
        return f"{self.medicine.label()} — {self.quantity} {self.medicine.unit}"

    def dispense(self, staff):
        """Deduct stock once and record the transaction; raises ValueError on conflicts."""
        from django.db import transaction
        from inventory.models import MedicineInventory, MedicineStockMovement

        if self.status != 'pending':
            raise ValueError('This prescription has already been processed.')
        if not self.assessment_id or not self.assessment.is_finalized:
            raise ValueError('Only a finalized, signed prescription can be dispensed.')
        with transaction.atomic():
            medicine = MedicineInventory.objects.select_for_update().get(pk=self.medicine_id)
            if medicine.is_expired():
                raise ValueError(f'{medicine.medicine_name} batch {medicine.batch_number} is expired.')
            if medicine.quantity_available < self.quantity:
                raise ValueError(
                    f'Only {medicine.quantity_available} {medicine.unit} of '
                    f'{medicine.medicine_name} left in stock.'
                )
            medicine.quantity_available -= self.quantity
            medicine.quantity_dispensed += self.quantity
            medicine.save(update_fields=['quantity_available', 'quantity_dispensed', 'updated_at'])
            self.status = 'dispensed'
            self.dispensed_at = timezone.now()
            self.dispensed_by = staff
            self.save(update_fields=['status', 'dispensed_at', 'dispensed_by', 'updated_at'])
            MedicineStockMovement.objects.create(
                medicine=medicine, movement_type='dispense', quantity=self.quantity,
                reference=(self.bite_case.case_number if self.bite_case_id else self.patient.patient_number),
                moved_by=staff,
                notes=f'Prescription #{self.pk} — {self.patient.full_name()}',
            )
