from django.urls import path
from . import views

app_name = 'nurse'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('findings/<int:case_pk>/', views.doctor_finding_detail, name='doctor_finding_detail'),
]
