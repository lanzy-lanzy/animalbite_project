from django.db import models
from django.conf import settings
from bite_cases.models import AnimalBiteCase

class VaccinationSchedule(models.Model):
    bite_case = models.OneToOneField(AnimalBiteCase, on_delete=models.CASCADE, related_name='vaccination_schedule')
    rig_given = models.BooleanField(default=False)
    tetanus_toxoid_given = models.BooleanField(default=False)
    antibiotics_given = models.BooleanField(default=False)
    other_medicine = models.TextField(blank=True)
    doctor_notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Schedule for {self.bite_case.case_number}"

class VaccineDose(models.Model):
    DOSE_STATUS = [('scheduled', 'Scheduled'), ('completed', 'Completed'), ('missed', 'Missed'), ('rescheduled', 'Rescheduled'), ('cancelled', 'Cancelled')]

    schedule = models.ForeignKey(VaccinationSchedule, on_delete=models.CASCADE, related_name='doses')
    dose_label = models.CharField(max_length=50)
    scheduled_date = models.DateField()
    actual_date = models.DateField(null=True, blank=True)
    vaccine_brand = models.CharField(max_length=100, blank=True)
    batch_number = models.CharField(max_length=100, blank=True)
    expiration_date = models.DateField(null=True, blank=True)
    dose_status = models.CharField(max_length=20, choices=DOSE_STATUS, default='scheduled')
    administered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='administered_doses')
    remarks = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['scheduled_date']

    def __str__(self):
        return f"{self.dose_label} - {self.schedule.bite_case.case_number} ({self.get_dose_status_display()})"

class FollowUpRecord(models.Model):
    FOLLOWUP_STATUS = [('pending', 'Pending'), ('completed', 'Completed'), ('missed', 'Missed'), ('rescheduled', 'Rescheduled'), ('contacted', 'Contacted')]

    bite_case = models.ForeignKey(AnimalBiteCase, on_delete=models.CASCADE, related_name='follow_ups')
    follow_up_date = models.DateField()
    status = models.CharField(max_length=20, choices=FOLLOWUP_STATUS, default='pending')
    notes = models.TextField(blank=True)
    contacted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-follow_up_date']

    def __str__(self):
        return f"Follow-up {self.bite_case.case_number} on {self.follow_up_date}"

class AnimalObservation(models.Model):
    FINAL_CONDITION = [('healthy', 'Healthy after observation'), ('sick', 'Sick'), ('died', 'Died'), ('missing', 'Missing')]

    bite_case = models.OneToOneField(AnimalBiteCase, on_delete=models.CASCADE, related_name='observation')
    animal_owner_name = models.CharField(max_length=200, blank=True)
    owner_contact = models.CharField(max_length=20, blank=True)
    animal_location = models.TextField(blank=True)
    start_date = models.DateField()
    observation_notes = models.TextField(blank=True)
    daily_status = models.TextField(blank=True)
    final_condition = models.CharField(max_length=20, choices=FINAL_CONDITION, null=True, blank=True)
    final_remarks = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Observation for {self.bite_case.case_number}"


class SMSLog(models.Model):
    REMINDER_CHOICES = [
        ('3days_before', '3 Days Before'),
        ('on_day', 'On Day'),
    ]
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('queued', 'Queued'),
        ('sent', 'Sent'),
        ('failed', 'Failed'),
    ]

    dose = models.ForeignKey(VaccineDose, on_delete=models.CASCADE, related_name='sms_logs', null=True, blank=True)
    patient = models.ForeignKey('patients.Patient', on_delete=models.CASCADE, related_name='sms_logs', null=True, blank=True)
    bite_case = models.ForeignKey(AnimalBiteCase, on_delete=models.CASCADE, related_name='sms_logs', null=True, blank=True)
    reminder_type = models.CharField(max_length=20, choices=REMINDER_CHOICES)
    recipient = models.CharField(max_length=30)
    message = models.TextField()
    scheduled_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    semaphore_message_id = models.CharField(max_length=100, blank=True)
    api_response = models.TextField(blank=True)
    sent_at = models.DateTimeField(auto_now_add=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-sent_at']
        indexes = [
            models.Index(fields=['scheduled_date', 'reminder_type']),
            models.Index(fields=['recipient']),
        ]

    def __str__(self):
        return f"SMS {self.get_reminder_type_display()} to {self.recipient} for {self.dose} - {self.status}"
