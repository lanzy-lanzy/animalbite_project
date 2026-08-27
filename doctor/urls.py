from django.urls import path
from . import views

app_name = 'doctor'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('queue/', views.queue_list, name='queue'),
    path('assessments/', views.assessment_list, name='assessment_list'),
    path('case/<int:pk>/assess/', views.assess_case, name='assess_case'),
    path('patient/<int:pk>/assess/', views.assess_patient, name='assess_patient'),
]
