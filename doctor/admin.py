from django.contrib import admin
from .models import ClinicalAssessment, Prescription

@admin.register(ClinicalAssessment)
class ClinicalAssessmentAdmin(admin.ModelAdmin):
    list_display = ('patient', 'bite_case', 'category_confirmed', 'assessed_by', 'status', 'is_finalized', 'encounter_date')
    list_filter = ('category_confirmed', 'status', 'is_finalized', 'pep_indicated', 'rig_indicated')
    search_fields = ('patient__first_name', 'patient__last_name', 'patient__patient_number', 'bite_case__case_number')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Prescription)
class PrescriptionAdmin(admin.ModelAdmin):
    list_display = ('patient', 'medicine', 'dose', 'quantity', 'status',
                    'dispensed_by', 'dispensed_at')
    list_filter = ('status', 'medicine__dosage_form')
    search_fields = ('patient__first_name', 'patient__last_name',
                     'patient__patient_number', 'medicine__medicine_name',
                     'bite_case__case_number')
    readonly_fields = ('created_at', 'updated_at')
