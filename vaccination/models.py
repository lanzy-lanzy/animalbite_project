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
    DOSE_STATUS = [
        ('pending', 'Pending Scheduling'),
        ('scheduled', 'Scheduled'),
        ('completed', 'Completed'),
        ('missed', 'Missed'),
        ('rescheduled', 'Rescheduled'),
        ('cancelled', 'Cancelled'),
    ]

    schedule = models.ForeignKey(VaccinationSchedule, on_delete=models.CASCADE, related_name='doses')
    dose_label = models.CharField(max_length=50)
    # Left NULL until the nurse schedules the dose; only the 1st dose is dated by the doctor.
    scheduled_date = models.DateField(null=True, blank=True)
    protocol_day = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Day offset prescribed by the doctor's protocol")
    actual_date = models.DateField(null=True, blank=True)
    actual_time = models.TimeField(null=True, blank=True, help_text="Time the dose was actually administered")
    vaccine_brand = models.CharField(max_length=100, blank=True)
    batch_number = models.CharField(max_length=100, blank=True)
    expiration_date = models.DateField(null=True, blank=True)
    dose_status = models.CharField(max_length=20, choices=DOSE_STATUS, default='scheduled')
    administered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='administered_doses')
    remarks = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # Dose order must survive NULL scheduled dates (pending doses), so order by creation.
        ordering = ['id']

    def __str__(self):
        return f"{self.dose_label} - {self.schedule.bite_case.case_number} ({self.get_dose_status_display()})"

    ORDINALS = ('1st', '2nd', '3rd', '4th', '5th')

    @property
    def number_label(self):
        """Ordinal label for the treatment plan table, e.g. '2nd Dose'."""
        if self.pk is None:
            return self.dose_label
        position = self.schedule.doses.filter(pk__lte=self.pk).count()
        if not position:
            return self.dose_label
        ordinal = self.ORDINALS[position - 1] if position <= len(self.ORDINALS) else f'{position}th'
        return f'{ordinal} Dose'

    @property
    def clinical_date(self):
        """Read-only record date: actual administration, else the nurse's appointment."""
        return self.actual_date or self.scheduled_date

    @property
    def clinical_status(self):
        """Read-only status label driven only by saved dose records."""
        if self.dose_status == 'completed':
            return 'Completed'
        if self.dose_status == 'missed':
            return 'Missed'
        if self.dose_status == 'cancelled':
            return 'Cancelled'
        if self.dose_status == 'rescheduled':
            return 'Rescheduled'
        if self.scheduled_date:
            return 'Scheduled'
        return 'Pending Scheduling'

    def expected_date(self):
        """Protocol date for this dose, measured from the 1st dose."""
        from datetime import timedelta

        if self.protocol_day is None:
            return None
        first = self.schedule.doses.order_by('id').first()
        if not first or first.pk == self.pk or not first.scheduled_date:
            return None
        return first.scheduled_date + timedelta(days=self.protocol_day)

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
    MANUAL_TYPES = ('manual_immediate', 'manual_2days_before', 'manual_1day_before', 'manual_custom')
    REMINDER_CHOICES = [
        ('schedule_set', 'Schedule Set'),
        ('3days_before', '3 Days Before'),
        ('on_day', 'On Day'),
        ('manual_immediate', 'Manual – Send Now'),
        ('manual_2days_before', 'Manual – 2 Days Before'),
        ('manual_1day_before', 'Manual – 1 Day Before'),
        ('manual_custom', 'Manual – Custom'),
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

    @property
    def is_manual(self):
        return self.reminder_type in self.MANUAL_TYPES
