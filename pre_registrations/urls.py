from django.urls import path

from . import views

app_name = "pre_registrations"

urlpatterns = [
    path("patient/report-bite/", views.patient_report_bite, name="patient_report"),
    path("pre-register/", views.public_create, name="public_create"),
    path("pre-register/success/<str:number>/", views.public_success, name="public_success"),
    path("pre-register/slip/<str:number>/", views.public_slip, name="public_slip"),
    path("pre-register/status/", views.public_status, name="public_status"),
    path("pre-register/qr/<str:number>.svg", views.qr_code_svg, name="qr_code"),
    path("staff/pre-registrations/", views.staff_list, name="staff_list"),
    path("staff/pre-registrations/<int:pk>/", views.staff_detail, name="staff_detail"),
    path("staff/pre-registrations/<int:pk>/edit/", views.staff_edit, name="staff_edit"),
    path("staff/pre-registrations/<int:pk>/duplicate/", views.mark_duplicate, name="mark_duplicate"),
    path("staff/pre-registrations/<int:pk>/no-show/", views.mark_no_show, name="mark_no_show"),
    path("staff/pre-registrations/<int:pk>/convert/", views.convert, name="convert"),
    path("staff/pre-registrations/<int:pk>/slip/", views.staff_slip, name="staff_slip"),
]
