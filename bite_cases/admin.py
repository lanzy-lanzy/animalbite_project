from django.contrib import admin
from .models import AnimalBiteCase, ExposureClassification, MedicalNote

@admin.register(AnimalBiteCase)
class AnimalBiteCaseAdmin(admin.ModelAdmin):
    list_display = ['case_number', 'patient', 'animal_type', 'case_status', 'created_at']
    list_filter = ['case_status', 'animal_type']
    search_fields = ['case_number', 'patient__first_name', 'patient__last_name']

@admin.register(ExposureClassification)
class ExposureClassificationAdmin(admin.ModelAdmin):
    list_display = ['bite_case', 'category', 'classified_by', 'classification_date']

@admin.register(MedicalNote)
class MedicalNoteAdmin(admin.ModelAdmin):
    list_display = ['bite_case', 'created_by', 'created_at']
