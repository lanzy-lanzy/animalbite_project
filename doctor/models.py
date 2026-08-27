from django.db import models
from django.conf import settings
from patients.models import Patient
from bite_cases.models import AnimalBiteCase


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
    treatment_plan = models.TextField(blank=True, help_text="Detailed treatment & management plan")
    prescription = models.TextField(blank=True, help_text="Prescription / medications")
    follow_up_instructions = models.TextField(blank=True, help_text="Follow-up instructions")
    additional_notes = models.TextField(blank=True, help_text="Additional notes / allergy re-check")

    # Doctor attestation
    prc_number = models.CharField(max_length=50, blank=True, help_text="PRC License No.")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='draft')
    is_finalized = models.BooleanField(default=False, help_text="Finalized and signed")

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
