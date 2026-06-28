from django.contrib import admin
from .models import VaccinationSchedule, VaccineDose, FollowUpRecord, AnimalObservation

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
