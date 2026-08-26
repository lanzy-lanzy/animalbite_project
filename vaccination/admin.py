from django.contrib import admin
from .models import VaccinationSchedule, VaccineDose, FollowUpRecord, AnimalObservation, SMSLog

@admin.register(VaccinationSchedule)
class VaccinationScheduleAdmin(admin.ModelAdmin):
    list_display = ['bite_case', 'rig_given', 'tetanus_toxoid_given', 'created_at']

@admin.register(VaccineDose)
class VaccineDoseAdmin(admin.ModelAdmin):
    list_display = ['dose_label', 'schedule', 'scheduled_date', 'dose_status']
    list_filter = ['dose_status']
    search_fields = ['schedule__bite_case__case_number']

@admin.register(FollowUpRecord)
class FollowUpRecordAdmin(admin.ModelAdmin):
    list_display = ['bite_case', 'follow_up_date', 'status']
    list_filter = ['status']

@admin.register(AnimalObservation)
class AnimalObservationAdmin(admin.ModelAdmin):
    list_display = ['bite_case', 'start_date', 'final_condition']


@admin.register(SMSLog)
class SMSLogAdmin(admin.ModelAdmin):
    list_display = ['recipient', 'reminder_type', 'status', 'scheduled_date', 'dose', 'patient', 'sent_at']
    list_filter = ['reminder_type', 'status', 'scheduled_date']
    search_fields = ['recipient', 'patient__patient_number', 'patient__first_name', 'bite_case__case_number']
    readonly_fields = ['api_response', 'semaphore_message_id', 'sent_at']
