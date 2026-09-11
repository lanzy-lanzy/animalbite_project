from django.urls import path
from . import views

app_name = 'nurse'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
]
