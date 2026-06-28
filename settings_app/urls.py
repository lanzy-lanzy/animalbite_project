from django.urls import path
from . import views

app_name = 'settings_app'

urlpatterns = [
    path('', views.settings_list, name='settings_list'),
    path('<int:pk>/edit/', views.settings_edit, name='settings_edit'),
]
