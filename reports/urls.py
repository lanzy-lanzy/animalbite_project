from django.urls import path
from . import views

app_name = 'reports'

urlpatterns = [
    path('', views.reports_index, name='reports_index'),
    path('cases/', views.cases_report, name='cases_report'),
    path('patient-treatment/', views.patient_treatment_report,
         name='patient_treatment_report'),
    path('vaccine/', views.vaccine_report, name='vaccine_report'),
    path('vaccines/', views.vaccine_inventory_report,
         name='vaccine_inventory_report'),
    path('medicines/', views.medicine_inventory_report,
         name='medicine_inventory_report'),
    path('stock-alerts/', views.stock_alerts_report,
         name='stock_alerts_report'),
    path('barangay/', views.barangay_report, name='barangay_report'),
    path('animal-type/', views.animal_type_report, name='animal_type_report'),
    path('missed/', views.missed_appointments_report, name='missed_appointments'),
]
