from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from datetime import date, timedelta, datetime
import csv
import json
from patients.models import Patient
from bite_cases.models import AnimalBiteCase, ExposureClassification
from vaccination.models import VaccineDose, FollowUpRecord
from inventory.models import VaccineInventory, VaccineStockMovement

@login_required
def reports_index(request):
    return render(request, 'reports/index.html')

@login_required
def cases_report(request):
    report_type = request.GET.get('type', 'daily')
    barangay = request.GET.get('barangay', '')
    animal = request.GET.get('animal', '')
    status = request.GET.get('status', '')
    today = date.today()

    if report_type == 'daily':
        cases = AnimalBiteCase.objects.filter(created_at__date=today)
        title = f'Daily Cases - {today}'
    elif report_type == 'weekly':
        week_start = today - timedelta(days=today.weekday())
        cases = AnimalBiteCase.objects.filter(created_at__date__gte=week_start, created_at__date__lte=today)
        title = f'Weekly Cases - Week of {week_start}'
    elif report_type == 'monthly':
        month_start = today.replace(day=1)
        cases = AnimalBiteCase.objects.filter(created_at__date__gte=month_start)
        title = f'Monthly Cases - {today.strftime("%B %Y")}'
    elif report_type == 'annual':
        year_start = today.replace(month=1, day=1)
        cases = AnimalBiteCase.objects.filter(created_at__date__gte=year_start)
        title = f'Annual Cases - {today.year}'
    else:
        start_date = request.GET.get('start_date', '')
        end_date = request.GET.get('end_date', '')
        cases = AnimalBiteCase.objects.all()
        title = 'Custom Date Range'
        if start_date and end_date:
            try:
                start = datetime.strptime(start_date, '%Y-%m-%d').date()
                end = datetime.strptime(end_date, '%Y-%m-%d').date()
                cases = cases.filter(created_at__date__gte=start, created_at__date__lte=end)
                title = f'Cases from {start_date} to {end_date}'
            except ValueError:
                pass

    if barangay:
        cases = cases.filter(incident_barangay=barangay)
    if animal:
        cases = cases.filter(animal_type=animal)
    if status:
        cases = cases.filter(case_status=status)

    export = request.GET.get('export', '')
    if export == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="cases_report_{today}.csv"'
        writer = csv.writer(response)
        writer.writerow(['Case #', 'Patient', 'Date', 'Animal', 'Exposure', 'Status'])
        for c in cases:
            writer.writerow([c.case_number, str(c.patient), c.created_at.date(), c.get_animal_type_display(), c.get_exposure_type_display(), c.get_case_status_display()])
        return response

    context = {'cases': cases.order_by('-created_at'), 'title': title, 'report_type': report_type}
    return render(request, 'reports/cases_report.html', context)

@login_required
def vaccine_report(request):
    items = VaccineInventory.objects.all().order_by('vaccine_name')
    total_received = items.aggregate(total=Sum('quantity_received'))['total'] or 0
    total_used = items.aggregate(total=Sum('quantity_used'))['total'] or 0
    total_available = items.aggregate(total=Sum('quantity_available'))['total'] or 0

    export = request.GET.get('export', '')
    if export == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="vaccine_report_{date.today()}.csv"'
        writer = csv.writer(response)
        writer.writerow(['Vaccine', 'Brand', 'Batch', 'Received', 'Used', 'Available', 'Expiry'])
        for i in items:
            writer.writerow([i.vaccine_name, i.brand, i.batch_number, i.quantity_received, i.quantity_used, i.quantity_available, i.expiration_date])
        return response

    return render(request, 'reports/vaccine_report.html', {
        'items': items, 'total_received': total_received,
        'total_used': total_used, 'total_available': total_available,
    })

@login_required
def missed_appointments_report(request):
    missed = FollowUpRecord.objects.filter(status='missed').select_related('bite_case__patient').order_by('-follow_up_date')
    return render(request, 'reports/missed_appointments.html', {'records': missed})
