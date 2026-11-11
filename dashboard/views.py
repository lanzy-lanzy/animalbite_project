from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum, Q
from django.db import models
from django.utils import timezone
from datetime import date, timedelta, datetime
from patients.models import Patient
from bite_cases.models import AnimalBiteCase
from vaccination.models import VaccineDose, FollowUpRecord
from inventory.models import VaccineInventory
from reports.services import (
    animal_type_distribution,
    barangay_distribution,
    completed_schedule_case_ids,
    effective_status,
    grouped_medicine_products,
    grouped_vaccine_products,
)


def _recent_month_keys(today, count=6):
    first = today.replace(day=1)
    keys = []
    for _ in range(count):
        keys.append((first.year, first.month))
        first = (first - timedelta(days=1)).replace(day=1)
    return list(reversed(keys))


def _month_label(key):
    return date(key[0], key[1], 1).strftime('%b %Y')


def _vaccine_category(name):
    lowered = name.casefold()
    if 'rig' in lowered or 'immunoglobulin' in lowered or 'serum' in lowered:
        return 'RIG'
    if 'tetanus' in lowered or 'toxoid' in lowered:
        return 'Biologic'
    return 'Vaccine'


def _admin_chart_data(today):
    """Read-only chart payload; shares the Reports Center data builders so
    dashboard totals and reports always agree."""
    cases_qs = AnimalBiteCase.objects.all()
    total_cases = cases_qs.count()

    barangay = barangay_distribution(cases_qs)
    animal = animal_type_distribution(cases_qs)

    month_keys = _recent_month_keys(today)
    month_index = {key: i for i, key in enumerate(month_keys)}
    monthly_counts = [0] * len(month_keys)
    for bite_dt in cases_qs.values_list('date_time_bite', flat=True):
        local = timezone.localtime(bite_dt)
        idx = month_index.get((local.year, local.month))
        if idx is not None:
            monthly_counts[idx] += 1

    completed_ids = completed_schedule_case_ids()
    status_counts = {}
    for case in cases_qs:
        label = effective_status(case, completed_ids)
        status_counts[label] = status_counts.get(label, 0) + 1
    status_order = [label for _, label in AnimalBiteCase.CASE_STATUS]
    treatment_rows = [
        {'label': label, 'count': status_counts[label]}
        for label in status_order if status_counts.get(label)
    ]

    inventory_rows = []
    for product in grouped_vaccine_products():
        name = ' '.join(part for part in (product['name'], product['brand']) if part)
        inventory_rows.append({
            'name': name,
            'quantity': product['total_available'],
            'unit': 'vials',
            'category': _vaccine_category(product['name']),
            'status': product['stock_status'],
            'threshold': product['threshold'],
        })
    for product in grouped_medicine_products():
        inventory_rows.append({
            'name': product['display_name'],
            'quantity': product['total_available'],
            'unit': product['unit'] or 'units',
            'category': 'Medicine',
            'status': product['stock_status'],
            'threshold': product['threshold'],
        })
    category_order = {'Vaccine': 0, 'RIG': 1, 'Biologic': 2, 'Medicine': 3}
    inventory_rows.sort(key=lambda r: (category_order[r['category']], r['name'].casefold()))

    dose_datasets = {status: [0] * len(month_keys) for status in ('completed', 'scheduled', 'missed')}
    pending_count = 0
    for dose in VaccineDose.objects.all():
        if dose.dose_status == 'pending':
            pending_count += 1
            continue
        if dose.dose_status not in dose_datasets:
            continue
        dose_date = dose.actual_date if dose.dose_status == 'completed' else None
        dose_date = dose_date or dose.scheduled_date
        if not dose_date:
            continue
        idx = month_index.get((dose_date.year, dose_date.month))
        if idx is not None:
            dose_datasets[dose.dose_status][idx] += 1
    dose_labels = [_month_label(key) for key in month_keys]
    dose_series = [
        {'label': 'Completed', 'data': dose_datasets['completed']},
        {'label': 'Scheduled', 'data': dose_datasets['scheduled']},
        {'label': 'Missed', 'data': dose_datasets['missed']},
    ]
    if pending_count:
        dose_labels.append('Pending')
        for series in dose_series:
            series['data'].append(0)
        dose_series.append({
            'label': 'Pending Scheduling',
            'data': [0] * len(month_keys) + [pending_count],
        })

    return {
        'barangay': {
            'labels': [row['name'] for row in barangay['rows']],
            'counts': [row['count'] for row in barangay['rows']],
            'total': barangay['total'],
        },
        'animal': {
            'labels': [row['name'] for row in animal['rows']],
            'counts': [row['count'] for row in animal['rows']],
            'percents': [row['pct'] for row in animal['rows']],
            'total': animal['total'],
        },
        'monthly': {
            'labels': [_month_label(key) for key in month_keys],
            'counts': monthly_counts,
        },
        'treatment': {
            'labels': [row['label'] for row in treatment_rows],
            'counts': [row['count'] for row in treatment_rows],
            'total': total_cases,
        },
        'inventory': {'rows': inventory_rows},
        'doses': {'labels': dose_labels, 'series': dose_series},
    }


@login_required
def index(request):
    # Doctors and nurses use their own portals only (RBAC) — keep admin dashboard staff-only
    if getattr(request.user, 'role', '') == 'doctor':
        return redirect('doctor:dashboard')
    if getattr(request.user, 'role', '') == 'nurse':
        return redirect('nurse:dashboard')
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
    if getattr(request.user, 'role', '') == 'admin':
        context['chart_data'] = _admin_chart_data(today)
        return render(request, 'dashboard/admin_index.html', context)
    return render(request, 'dashboard/index.html', context)
