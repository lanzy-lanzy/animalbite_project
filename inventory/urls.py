from django.urls import path
from . import views

app_name = 'inventory'

urlpatterns = [
    path('vaccines/', views.vaccine_list, name='vaccine_list'),
    path('vaccines/create/', views.vaccine_create, name='vaccine_create'),
    path('vaccines/<int:pk>/', views.vaccine_detail, name='vaccine_detail'),
    path('supplies/', views.supply_list, name='supply_list'),
    path('supplies/create/', views.supply_create, name='supply_create'),
]
