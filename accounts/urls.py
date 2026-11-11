from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    path('', views.landing_view, name='landing'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('patient-portal/', views.patient_portal, name='patient_portal'),
    path('patient-portal/bite-records/', views.patient_bite_records, name='patient_bite_records'),
    path('patient-portal/vaccination-schedule/', views.patient_vaccination_schedule, name='patient_vaccination_schedule'),
    path('patient-portal/vaccination-history/', views.patient_vaccination_history, name='patient_vaccination_history'),
    path('patient-portal/my-qr/', views.patient_qr, name='patient_qr'),
    path('profile/', views.profile_view, name='profile'),
    path('profile/edit/', views.profile_edit, name='profile_edit'),
    path('profile/change-password/', views.change_password, name='change_password'),
    path('account/settings/', views.account_settings, name='account_settings'),
    path('users/', views.user_list, name='user_list'),
    path('users/create/', views.user_create, name='user_create'),
    path('users/<int:pk>/edit/', views.user_edit, name='user_edit'),
    path('users/<int:pk>/toggle/', views.user_toggle_active, name='user_toggle_active'),
]
