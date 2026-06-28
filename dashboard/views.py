from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum, Q
from django.db import models
from datetime import date, timedelta, datetime
from patients.models import Patient
from bite_cases.models import AnimalBiteCase
from vaccination.models import VaccineDose, FollowUpRecord
from inventory.models import VaccineInventory

@login_required
def index(request):
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)

    total_cases = AnimalBiteCase.objects.count()
    new_today = AnimalBiteCase.objects.filter(created_at__date=today).count()
    this_week = AnimalBiteCase.objects.filter(created_at__date__gte=week_start).count()
    this_month = AnimalBiteCase.objects.filter(created_at__date__gte=month_start).count()
    active_cases = AnimalBiteCase.objects.filter(case_status__in=['new', 'under_treatment']).count()
    completed_cases = AnimalBiteCase.objects.filter(case_status='completed').count()
    missed_doses = VaccineDose.objects.filter(dose_status='missed').count()
    upcoming_doses = VaccineDose.objects.filter(dose_status='scheduled', scheduled_date__gte=today).count()
    total_patients = Patient.objects.filter(is_archived=False).count()

    total_vaccines = VaccineInventory.objects.aggregate(total=models.Sum('quantity_available'))['total'] or 0
    low_stock = VaccineInventory.objects.filter(quantity_available__lte=5).count()
    expired = VaccineInventory.objects.filter(expiration_date__lt=today).count()

    cases_by_barangay = AnimalBiteCase.objects.values('incident_barangay').annotate(count=Count('id')).order_by('-count')[:10]
    cases_by_animal = AnimalBiteCase.objects.values('animal_type').annotate(count=Count('id'))
    cases_by_exposure = AnimalBiteCase.objects.values('exposure_type').annotate(count=Count('id'))

    recent_cases = AnimalBiteCase.objects.select_related('patient').order_by('-created_at')[:10]

    context = {
        'total_cases': total_cases, 'new_today': new_today, 'this_week': this_week,
        'this_month': this_month, 'active_cases': active_cases, 'completed_cases': completed_cases,
        'missed_doses': missed_doses, 'upcoming_doses': upcoming_doses, 'total_patients': total_patients,
        'total_vaccines': total_vaccines, 'low_stock': low_stock, 'expired': expired,
        'cases_by_barangay': cases_by_barangay, 'cases_by_animal': cases_by_animal,
        'cases_by_exposure': cases_by_exposure, 'recent_cases': recent_cases,
    }
    return render(request, 'dashboard/index.html', context)
