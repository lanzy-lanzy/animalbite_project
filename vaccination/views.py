from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from datetime import date, timedelta
from .models import VaccinationSchedule, VaccineDose, FollowUpRecord, AnimalObservation
from .forms import VaccinationScheduleForm, VaccineDoseForm, FollowUpRecordForm, AnimalObservationForm
from bite_cases.models import AnimalBiteCase
from audit.models import AuditLog

@login_required
def schedule_list(request):
    schedules = VaccinationSchedule.objects.all().order_by('-created_at')
    paginator = Paginator(schedules, 20)
    page = paginator.get_page(request.GET.get('page'))
    return render(request, 'vaccination/schedule_list.html', {'schedules': page})

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
    schedule = get_object_or_404(VaccinationSchedule, pk=pk)
    return render(request, 'vaccination/schedule_detail.html', {'schedule': schedule})

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
    records = FollowUpRecord.objects.all()
    if status_filter:
        records = records.filter(status=status_filter)
    paginator = Paginator(records.order_by('-follow_up_date'), 20)
    page = paginator.get_page(request.GET.get('page'))
    return render(request, 'vaccination/follow_up_list.html', {'records': page, 'status_filter': status_filter})

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
