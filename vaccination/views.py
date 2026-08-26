from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from datetime import date, timedelta, datetime
from .models import VaccinationSchedule, VaccineDose, FollowUpRecord, AnimalObservation
from .forms import VaccinationScheduleForm, VaccineDoseForm, FollowUpRecordForm, AnimalObservationForm
from bite_cases.models import AnimalBiteCase
from audit.models import AuditLog

@login_required
def schedule_list(request):
    q = request.GET.get('q','').strip()
    status = request.GET.get('status','')
    date_from = request.GET.get('from','')
    date_to = request.GET.get('to','')
    schedules = VaccinationSchedule.objects.select_related('bite_case__patient').all()
    if q:
        schedules = schedules.filter(Q(bite_case__case_number__icontains=q) | Q(bite_case__patient__first_name__icontains=q) | Q(bite_case__patient__last_name__icontains=q))
    if status:
        # filter by dose status existence
        if status == 'completed':
            schedules = schedules.filter(doses__dose_status='completed').distinct()
        elif status == 'scheduled':
            schedules = schedules.filter(doses__dose_status='scheduled').distinct()
        elif status == 'missed':
            schedules = schedules.filter(doses__dose_status='missed').distinct()
    if date_from:
        try:
            d = datetime.strptime(date_from, '%Y-%m-%d').date()
            schedules = schedules.filter(created_at__date__gte=d)
        except ValueError:
            pass
    if date_to:
        try:
            d = datetime.strptime(date_to, '%Y-%m-%d').date()
            schedules = schedules.filter(created_at__date__lte=d)
        except ValueError:
            pass
    schedules = schedules.order_by('-created_at')
    # stats for header
    total = schedules.count()
    # quick counts for filter badges
    from django.db.models import Count
    paginator = Paginator(schedules, 12)
    page = paginator.get_page(request.GET.get('page'))
    return render(request, 'vaccination/schedule_list.html', {
        'schedules': page, 'page_obj': page,
        'q': q, 'status': status, 'date_from': date_from, 'date_to': date_to,
        'total': total,
    })

@login_required
def schedule_create(request, case_pk):
    case = get_object_or_404(AnimalBiteCase, pk=case_pk)
    try:
        schedule = case.vaccination_schedule
        messages.info(request, 'Schedule already exists for this case.')
        return redirect('vaccination:schedule_detail', pk=schedule.pk)
    except VaccinationSchedule.DoesNotExist:
        pass

    if request.method == 'POST':
        form = VaccinationScheduleForm(request.POST)
        if form.is_valid():
            schedule = form.save(commit=False)
            schedule.bite_case = case
            schedule.created_by = request.user
            schedule.save()
            from settings_app.models import SystemSetting
            days_str = SystemSetting.objects.filter(key='vaccination_days').first()
            days = [0, 3, 7, 14, 28]
            if days_str and days_str.value:
                try:
                    days = [int(d.strip()) for d in days_str.value.split(',')]
                except ValueError:
                    pass
            for d in days:
                VaccineDose.objects.create(
                    schedule=schedule,
                    dose_label=f"Day {d}",
                    scheduled_date=case.date_time_consultation.date() + timedelta(days=d),
                )
            AuditLog.objects.create(user=request.user, action='CREATE_SCHEDULE', description=f'Created vaccination schedule for {case.case_number}')
            messages.success(request, 'Vaccination schedule created.')
            return redirect('vaccination:schedule_detail', pk=schedule.pk)
    else:
        form = VaccinationScheduleForm()
    return render(request, 'vaccination/schedule_form.html', {'form': form, 'case': case})

@login_required
def schedule_detail(request, pk):
    schedule = get_object_or_404(VaccinationSchedule.objects.select_related('bite_case__patient'), pk=pk)
    # for badge "Today" and progress
    today = date.today()
    total = schedule.doses.count()
    completed = schedule.doses.filter(dose_status='completed').count()
    progress = int((completed / total * 100) if total else 0)
    return render(request, 'vaccination/schedule_detail.html', {
        'schedule': schedule, 'today': today,
        'total': total, 'completed': completed, 'progress': progress,
    })

@login_required
def dose_update(request, pk):
    dose = get_object_or_404(VaccineDose, pk=pk)
    if request.method == 'POST':
        form = VaccineDoseForm(request.POST, instance=dose)
        if form.is_valid():
            obj = form.save(commit=False)
            if obj.dose_status == 'completed' and not obj.administered_by:
                obj.administered_by = request.user
            if obj.dose_status == 'completed' and obj.vaccine_brand:
                from inventory.models import VaccineInventory
                inv = VaccineInventory.objects.filter(
                    vaccine_name__icontains=obj.vaccine_brand,
                    batch_number=obj.batch_number,
                    expiration_date__gt=date.today()
                ).first()
                if inv and inv.quantity_available > 0:
                    inv.quantity_available -= 1
                    inv.quantity_used += 1
                    inv.save()
            obj.save()
            AuditLog.objects.create(user=request.user, action='UPDATE_DOSE', description=f'Updated dose {dose.dose_label} for {dose.schedule.bite_case.case_number} to {obj.get_dose_status_display()}')
            messages.success(request, f'Dose {obj.dose_label} updated.')
            return redirect('vaccination:schedule_detail', pk=dose.schedule.pk)
    else:
        form = VaccineDoseForm(instance=dose)
    return render(request, 'vaccination/dose_form.html', {'form': form, 'dose': dose})

@login_required
def follow_up_list(request):
    status_filter = request.GET.get('status', '')
    q = request.GET.get('q','').strip()
    date_from = request.GET.get('from','')
    date_to = request.GET.get('to','')
    overdue = request.GET.get('overdue','')

    records = FollowUpRecord.objects.select_related('bite_case__patient').all()
    if status_filter:
        records = records.filter(status=status_filter)
    if q:
        records = records.filter(Q(bite_case__case_number__icontains=q) | Q(bite_case__patient__first_name__icontains=q) | Q(bite_case__patient__last_name__icontains=q))
    if date_from:
        try:
            d = datetime.strptime(date_from, '%Y-%m-%d').date()
            records = records.filter(follow_up_date__gte=d)
        except ValueError:
            pass
    if date_to:
        try:
            d = datetime.strptime(date_to, '%Y-%m-%d').date()
            records = records.filter(follow_up_date__lte=d)
        except ValueError:
            pass
    if overdue == '1':
        records = records.filter(follow_up_date__lt=date.today(), status__in=['pending','contacted'])

    # stats
    base_qs = FollowUpRecord.objects.all()
    total = base_qs.count()
    pending = base_qs.filter(status='pending').count()
    completed = base_qs.filter(status='completed').count()
    missed = base_qs.filter(status='missed').count()
    overdue_count = base_qs.filter(follow_up_date__lt=date.today(), status__in=['pending','contacted']).count()

    # quick inline status update via POST
    if request.method == 'POST':
        rec_id = request.POST.get('record_id')
        new_status = request.POST.get('new_status')
        if rec_id and new_status in dict(FollowUpRecord.FOLLOWUP_STATUS):
            rec = get_object_or_404(FollowUpRecord, pk=rec_id)
            rec.status = new_status
            rec.save()
            AuditLog.objects.create(user=request.user, action='UPDATE_FOLLOWUP', description=f'Updated follow-up {rec.pk} to {new_status}')
            messages.success(request, f'Follow-up marked as {rec.get_status_display()}.')
            return redirect(request.get_full_path())

    records = records.order_by('-follow_up_date')
    paginator = Paginator(records, 12)
    page = paginator.get_page(request.GET.get('page'))
    return render(request, 'vaccination/follow_up_list.html', {
        'records': page, 'page_obj': page,
        'status_filter': status_filter, 'q': q,
        'date_from': date_from, 'date_to': date_to, 'overdue': overdue,
        'total': total, 'pending': pending, 'completed': completed, 'missed': missed, 'overdue_count': overdue_count,
        'today': date.today(),
        'status_choices': FollowUpRecord.FOLLOWUP_STATUS,
    })

@login_required
def follow_up_create(request, case_pk):
    case = get_object_or_404(AnimalBiteCase, pk=case_pk)
    if request.method == 'POST':
        form = FollowUpRecordForm(request.POST)
        if form.is_valid():
            record = form.save(commit=False)
            record.bite_case = case
            record.contacted_by = request.user
            record.save()
            messages.success(request, 'Follow-up record created.')
            return redirect('bite_cases:case_detail', pk=case.pk)
    else:
        form = FollowUpRecordForm()
    return render(request, 'vaccination/follow_up_form.html', {'form': form, 'case': case})

@login_required
def observation_create(request, case_pk):
    case = get_object_or_404(AnimalBiteCase, pk=case_pk)
    try:
        obs = case.observation
        messages.info(request, 'Observation record already exists.')
        return redirect('vaccination:observation_detail', pk=obs.pk)
    except AnimalObservation.DoesNotExist:
        pass
    if request.method == 'POST':
        form = AnimalObservationForm(request.POST)
        if form.is_valid():
            obs = form.save(commit=False)
            obs.bite_case = case
            obs.created_by = request.user
            obs.save()
            messages.success(request, 'Animal observation record created.')
            return redirect('vaccination:observation_detail', pk=obs.pk)
    else:
        form = AnimalObservationForm()
    return render(request, 'vaccination/observation_form.html', {'form': form, 'case': case})

@login_required
def observation_detail(request, pk):
    obs = get_object_or_404(AnimalObservation, pk=pk)
    return render(request, 'vaccination/observation_detail.html', {'observation': obs})
