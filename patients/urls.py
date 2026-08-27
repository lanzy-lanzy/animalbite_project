from django.urls import path
from . import views

app_name = 'patients'

urlpatterns = [
    path('', views.patient_list, name='patient_list'),
    path('create/', views.patient_create, name='patient_create'),
    path('<int:pk>/', views.patient_detail, name='patient_detail'),
    path('<int:pk>/edit/', views.patient_edit, name='patient_edit'),
    path('<int:pk>/reset-password/', views.patient_reset_password, name='patient_reset_password'),
    path('<int:pk>/print/', views.patient_print, name='patient_print'),
    path('<int:pk>/print/pdf/', views.patient_print_pdf, name='patient_print_pdf'),
    path('<int:pk>/vitals/add/', views.patient_vital_create, name='patient_vital_create'),
    path('vitals/<int:vital_id>/edit/', views.patient_vital_edit, name='patient_vital_edit'),
    path('vitals/<int:vital_id>/delete/', views.patient_vital_delete, name='patient_vital_delete'),
]
