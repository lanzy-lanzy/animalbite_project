from django.contrib import admin

from .models import PreRegistration


@admin.register(PreRegistration)
class PreRegistrationAdmin(admin.ModelAdmin):
    list_display = (
        "pre_registration_number",
        "full_name",
        "contact_number",
        "barangay",
        "status",
        "submitted_at",
    )
    list_filter = ("status", "barangay", "animal_type", "submitted_at")
    search_fields = (
        "pre_registration_number",
        "first_name",
        "last_name",
        "contact_number",
    )
    readonly_fields = ("pre_registration_number", "qr_code", "age", "submitted_at", "created_at", "updated_at")
