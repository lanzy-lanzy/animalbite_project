from django.urls import path
from . import views

app_name = 'bite_cases'

urlpatterns = [
    path('', views.case_list, name='case_list'),
    path('create/', views.case_create, name='case_create'),
    path('<int:pk>/', views.case_detail, name='case_detail'),
    path('<int:pk>/edit/', views.case_edit, name='case_edit'),
    path('<int:pk>/classify/', views.classify_exposure, name='classify_exposure'),
    path('<int:pk>/add-note/', views.add_medical_note, name='add_medical_note'),
]
