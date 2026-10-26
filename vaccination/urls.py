from django.urls import path
from . import views

app_name = 'vaccination'

urlpatterns = [
    path('schedules/', views.schedule_list, name='schedule_list'),
    path('schedules/patients/', views.patient_schedule_list, name='patient_schedule_list'),
    path('schedules/trigger/<int:case_pk>/', views.schedule_trigger, name='schedule_trigger'),
    path('schedules/create/<int:case_pk>/', views.schedule_create, name='schedule_create'),
    path('schedules/<int:pk>/', views.schedule_detail, name='schedule_detail'),
    # Patient portal — read-only view of the patient's own schedule
    path('patient-portal/schedules/<int:pk>/', views.my_schedule_detail, name='my_schedule_detail'),
    path('doses/<int:pk>/update/', views.dose_update, name='dose_update'),
    path('follow-ups/', views.follow_up_list, name='follow_up_list'),
    path('follow-ups/create/<int:case_pk>/', views.follow_up_create, name='follow_up_create'),
    path('observations/create/<int:case_pk>/', views.observation_create, name='observation_create'),
    path('observations/<int:pk>/', views.observation_detail, name='observation_detail'),
    # SMS (Semaphore RHUDumingag)
    path('sms/logs/', views.sms_log_list, name='sms_log_list'),
    path('sms/send/dose/<int:pk>/', views.send_sms_for_dose, name='send_sms_for_dose'),
    path('sms/trigger/', views.trigger_sms_reminders, name='trigger_sms_reminders'),
]
