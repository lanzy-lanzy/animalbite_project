from django.contrib import admin
from .models import Patient, Barangay

@admin.register(Barangay)
class BarangayAdmin(admin.ModelAdmin):
    list_display = ['name', 'is_active']
    search_fields = ['name']

@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ['patient_number', 'full_name', 'sex', 'barangay', 'created_at']
    search_fields = ['patient_number', 'first_name', 'last_name']
    list_filter = ['sex', 'barangay', 'is_archived']
