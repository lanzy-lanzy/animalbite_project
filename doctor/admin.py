from django.contrib import admin
from .models import ClinicalAssessment

@admin.register(ClinicalAssessment)
class ClinicalAssessmentAdmin(admin.ModelAdmin):
    list_display = ('patient', 'bite_case', 'category_confirmed', 'assessed_by', 'status', 'is_finalized', 'encounter_date')
    list_filter = ('category_confirmed', 'status', 'is_finalized', 'pep_indicated', 'rig_indicated')
    search_fields = ('patient__first_name', 'patient__last_name', 'patient__patient_number', 'bite_case__case_number')
    readonly_fields = ('created_at', 'updated_at')
