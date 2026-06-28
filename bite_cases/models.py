from django.db import models
from django.conf import settings
from patients.models import Patient

class AnimalBiteCase(models.Model):
    ANIMAL_TYPES = [('dog', 'Dog'), ('cat', 'Cat'), ('monkey', 'Monkey'), ('bat', 'Bat'), ('other', 'Other')]
    OWNERSHIP = [('owned', 'Owned'), ('stray', 'Stray'), ('unknown', 'Unknown')]
    VAX_STATUS = [('vaccinated', 'Vaccinated'), ('not_vaccinated', 'Not Vaccinated'), ('unknown', 'Unknown')]
    ANIMAL_CONDITION = [('alive', 'Alive'), ('dead', 'Dead'), ('missing', 'Missing'), ('under_observation', 'Under Observation')]
    EXPOSURE_TYPE = [('bite', 'Bite'), ('scratch', 'Scratch'), ('lick', 'Lick on broken skin'), ('saliva', 'Contact with saliva'), ('other', 'Other')]
    CASE_STATUS = [('new', 'New Case'), ('under_treatment', 'Under Treatment'), ('completed', 'Completed'), ('missed', 'Missed Appointment'), ('referred', 'Referred'), ('archived', 'Archived')]

    case_number = models.CharField(max_length=20, unique=True)
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='bite_cases')
    date_time_bite = models.DateTimeField()
    date_time_consultation = models.DateTimeField(auto_now_add=True)
    place_of_incident = models.CharField(max_length=255)
    incident_barangay = models.CharField(max_length=100)
    animal_type = models.CharField(max_length=20, choices=ANIMAL_TYPES)
    animal_other = models.CharField(max_length=100, blank=True)
    animal_ownership = models.CharField(max_length=20, choices=OWNERSHIP)
    animal_vax_status = models.CharField(max_length=20, choices=VAX_STATUS)
    animal_condition = models.CharField(max_length=20, choices=ANIMAL_CONDITION)
    exposure_type = models.CharField(max_length=20, choices=EXPOSURE_TYPE)
    body_part_affected = models.CharField(max_length=255)
    number_of_wounds = models.IntegerField(default=1)
    wound_description = models.TextField(blank=True)
    wound_washed_immediately = models.BooleanField(default=False)
    first_aid_given = models.TextField(blank=True)
    initial_consultation_notes = models.TextField(blank=True)
    case_status = models.CharField(max_length=20, choices=CASE_STATUS, default='new')
    is_locked = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='created_cases')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.case_number} - {self.patient.full_name()}"

class ExposureClassification(models.Model):
    CATEGORIES = [('category_i', 'Category I'), ('category_ii', 'Category II'), ('category_iii', 'Category III')]

    bite_case = models.OneToOneField(AnimalBiteCase, on_delete=models.CASCADE, related_name='exposure')
    category = models.CharField(max_length=20, choices=CATEGORIES)
    classification_date = models.DateTimeField(auto_now_add=True)
    classified_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    medical_notes = models.TextField(blank=True)
    recommended_action = models.TextField(blank=True)
    treatment_plan = models.TextField(blank=True)

    def __str__(self):
        return f"{self.bite_case.case_number} - {self.get_category_display()}"

class MedicalNote(models.Model):
    bite_case = models.ForeignKey(AnimalBiteCase, on_delete=models.CASCADE, related_name='medical_notes')
    note = models.TextField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Note on {self.bite_case.case_number} by {self.created_by}"
