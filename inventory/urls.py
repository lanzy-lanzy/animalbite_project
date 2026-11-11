from django.urls import path
from . import views

app_name = 'inventory'

urlpatterns = [
    path('vaccines/', views.vaccine_list, name='vaccine_list'),
    path('vaccines/create/', views.vaccine_create, name='vaccine_create'),
    path('vaccines/<int:pk>/add-stock/', views.vaccine_add_stock, name='vaccine_add_stock'),
    path('vaccines/<int:pk>/pull-out/', views.vaccine_pull_out, name='vaccine_pull_out'),
    path('vaccines/<int:pk>/', views.vaccine_detail, name='vaccine_detail'),
    path('supplies/', views.supply_list, name='supply_list'),
    path('supplies/create/', views.supply_create, name='supply_create'),
    path('medicines/', views.medicine_list, name='medicine_list'),
    path('medicines/create/', views.medicine_create, name='medicine_create'),
    path('medicines/<int:pk>/add-stock/', views.medicine_add_stock, name='medicine_add_stock'),
    path('medicines/<int:pk>/pull-out/', views.medicine_pull_out, name='medicine_pull_out'),
    path('medicines/<int:pk>/', views.medicine_detail, name='medicine_detail'),
    path('prescriptions/', views.prescription_list, name='prescription_list'),
    path('prescriptions/<int:pk>/dispense/', views.prescription_dispense, name='prescription_dispense'),
]
