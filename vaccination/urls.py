from django.urls import path
from . import views

app_name = 'vaccination'

urlpatterns = [
    path('schedules/', views.schedule_list, name='schedule_list'),
    path('schedules/patients/', views.patient_schedule_list, name='patient_schedule_list'),
    path('schedules/trigger/<int:case_pk>/', views.schedule_trigger, name='schedule_trigger'),
    path('schedules/create/<int:case_pk>/', views.schedule_create, name='schedule_create'),
    path('schedules/<int:pk>/manage/', views.nurse_schedule_manage, name='nurse_schedule_manage'),
    path('schedules/<int:pk>/print/', views.nurse_schedule_print, name='nurse_schedule_print'),
    path('schedules/<int:pk>/', views.schedule_detail, name='schedule_detail'),
    path('records/<int:pk>/', views.record_detail, name='record_detail'),
    path('records/<int:pk>/assessment/', views.assessment_detail, name='assessment_detail'),
    # Patient portal — read-only view of the patient's own schedule
    path('patient-portal/schedules/<int:pk>/', views.my_schedule_detail, name='my_schedule_detail'),
    path('doses/<int:pk>/update/', views.dose_update, name='dose_update'),
    path('doses/<int:pk>/schedule/', views.dose_schedule, name='dose_schedule'),
    path('doses/<int:pk>/administer/', views.dose_administer, name='dose_administer'),
    path('doses/<int:pk>/early-administration-request/', views.dose_early_administration_request, name='dose_early_administration_request'),
    path('follow-ups/', views.follow_up_list, name='follow_up_list'),
    path('follow-ups/create/<int:case_pk>/', views.follow_up_create, name='follow_up_create'),
    path('observations/create/<int:case_pk>/', views.observation_create, name='observation_create'),
    path('observations/<int:pk>/', views.observation_detail, name='observation_detail'),
    # SMS reminders
    path('sms/logs/', views.sms_log_list, name='sms_log_list'),
    path('sms/logs/<int:pk>/', views.sms_log_detail, name='sms_log_detail'),
    path('sms/manual/<int:pk>/', views.sms_manual_send, name='sms_manual_send'),
    path('sms/send/dose/<int:pk>/', views.send_sms_for_dose, name='send_sms_for_dose'),
    path('sms/trigger/', views.trigger_sms_reminders, name='trigger_sms_reminders'),
]
