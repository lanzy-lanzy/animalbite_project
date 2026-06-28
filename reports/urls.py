from django.urls import path
from . import views

app_name = 'reports'

urlpatterns = [
    path('', views.reports_index, name='reports_index'),
    path('cases/', views.cases_report, name='cases_report'),
    path('vaccine/', views.vaccine_report, name='vaccine_report'),
    path('missed/', views.missed_appointments_report, name='missed_appointments'),
]
