from django.urls import path
from . import views

app_name = 'vaccination'

urlpatterns = [
    path('schedules/', views.schedule_list, name='schedule_list'),
    path('schedules/create/<int:case_pk>/', views.schedule_create, name='schedule_create'),
    path('schedules/<int:pk>/', views.schedule_detail, name='schedule_detail'),
    path('doses/<int:pk>/update/', views.dose_update, name='dose_update'),
    path('follow-ups/', views.follow_up_list, name='follow_up_list'),
    path('follow-ups/create/<int:case_pk>/', views.follow_up_create, name='follow_up_create'),
    path('observations/create/<int:case_pk>/', views.observation_create, name='observation_create'),
    path('observations/<int:pk>/', views.observation_detail, name='observation_detail'),
]
