from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("accounts.urls")),
    path("dashboard/", include("dashboard.urls")),
    path("patients/", include("patients.urls")),
    path("bite-cases/", include("bite_cases.urls")),
    path("vaccination/", include("vaccination.urls")),
    path("doctor/", include("doctor.urls")),
    path("inventory/", include("inventory.urls")),
    path("reports/", include("reports.urls")),
    path("settings/", include("settings_app.urls")),
    path("audit/", include("audit.urls")),
    path("", include("pre_registrations.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
