from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from datetime import date, timedelta, datetime
from django.utils import timezone
from .models import VaccinationSchedule, VaccineDose, FollowUpRecord, AnimalObservation, SMSLog
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
    today = timezone.localdate()
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
                    expiration_date__gt=timezone.localdate()
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
def patient_schedule_list(request):
    """
    Patient-centric Vaccination Schedule dashboard.
    Directly manage patient schedules from the sidebar.
    Search/filter by patient and see next due / progress per schedule.
    """
    q = request.GET.get('q', '').strip()
    status = request.GET.get('status', '')
    due = request.GET.get('due', '')
    barangay_id = request.GET.get('barangay', '')
    date_from = request.GET.get('from', '')
    date_to = request.GET.get('to', '')

    today = timezone.localdate()

    schedules = VaccinationSchedule.objects.select_related(
        'bite_case__patient', 'bite_case__patient__barangay'
    ).prefetch_related('doses').all()

    if q:
        schedules = schedules.filter(
            Q(bite_case__case_number__icontains=q)
            | Q(bite_case__patient__first_name__icontains=q)
            | Q(bite_case__patient__last_name__icontains=q)
            | Q(bite_case__patient__patient_number__icontains=q)
            | Q(bite_case__patient__contact_number__icontains=q)
        )
    if barangay_id:
        try:
            schedules = schedules.filter(bite_case__patient__barangay_id=int(barangay_id))
        except ValueError:
            pass
    if status:
        if status == 'completed':
            schedules = schedules.filter(doses__dose_status='completed').distinct()
        elif status == 'scheduled':
            schedules = schedules.filter(doses__dose_status='scheduled').distinct()
        elif status == 'missed':
            schedules = schedules.filter(doses__dose_status='missed').distinct()
        elif status == 'rescheduled':
            schedules = schedules.filter(doses__dose_status='rescheduled').distinct()
    if due == 'today':
        schedules = schedules.filter(doses__scheduled_date=today, doses__dose_status__in=['scheduled', 'rescheduled']).distinct()
    elif due == 'overdue':
        schedules = schedules.filter(doses__scheduled_date__lt=today, doses__dose_status__in=['scheduled', 'rescheduled']).distinct()
    elif due == 'upcoming':
        schedules = schedules.filter(doses__scheduled_date__gt=today, doses__dose_status__in=['scheduled', 'rescheduled']).distinct()
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

    # Stats (on filtered queryset base for header cards — use actual counts distinct)
    total = schedules.count()
    # For quick stats we compute on all schedules if filtered, but keep filtered total as 'total'
    # Also compute global stats for dashboard hints (using filtered qs)
    due_today_count = VaccinationSchedule.objects.filter(
        doses__scheduled_date=today, doses__dose_status__in=['scheduled', 'rescheduled']
    ).distinct().count()
    overdue_count = VaccinationSchedule.objects.filter(
        doses__scheduled_date__lt=today, doses__dose_status__in=['scheduled', 'rescheduled']
    ).distinct().count()
    completed_schedules = VaccinationSchedule.objects.filter(
        doses__dose_status='completed'
    ).distinct().count()

    # Attach per-schedule computed fields for template
    # We do this after pagination to avoid N queries, but doses are prefetched so cheap.
    paginator = Paginator(schedules, 12)
    page = paginator.get_page(request.GET.get('page'))

    for sched in page.object_list:
        doses = list(sched.doses.all())
        total_doses = len(doses)
        completed = sum(1 for d in doses if d.dose_status == 'completed')
        missed = sum(1 for d in doses if d.dose_status == 'missed')
        scheduled = sum(1 for d in doses if d.dose_status in ('scheduled', 'rescheduled'))
        sched.total_doses_computed = total_doses
        sched.completed_computed = completed
        sched.missed_computed = missed
        sched.progress_computed = int((completed / total_doses * 100) if total_doses else 0)
        # Find next due dose (earliest scheduled/rescheduled with date >= today, else earliest overdue)
        next_dose = None
        overdue_doses = [d for d in doses if d.dose_status in ('scheduled', 'rescheduled') and d.scheduled_date < today]
        upcoming_doses = [d for d in doses if d.dose_status in ('scheduled', 'rescheduled') and d.scheduled_date >= today]
        upcoming_doses.sort(key=lambda d: d.scheduled_date)
        overdue_doses.sort(key=lambda d: d.scheduled_date)
        if upcoming_doses:
            next_dose = upcoming_doses[0]
        elif overdue_doses:
            next_dose = overdue_doses[0]
        else:
            # fallback to last dose or None
            next_dose = doses[-1] if doses else None
        sched.next_dose = next_dose
        sched.overdue_doses_count = len(overdue_doses)
        sched.is_overdue = len(overdue_doses) > 0
        sched.is_due_today = any(d.scheduled_date == today and d.dose_status in ('scheduled', 'rescheduled') for d in doses)
        # SMS indicator for next dose
        if next_dose:
            try:
                sched.next_dose_sms_count = SMSLog.objects.filter(dose=next_dose).count()
                sched.next_dose_sms_sent_today = SMSLog.objects.filter(dose=next_dose, sent_at__date=today).exists()
                sched.next_dose_last_sms = SMSLog.objects.filter(dose=next_dose).order_by('-sent_at').first()
            except Exception:
                sched.next_dose_sms_count = 0
                sched.next_dose_sms_sent_today = False
                sched.next_dose_last_sms = None
        else:
            sched.next_dose_sms_count = 0
            sched.next_dose_sms_sent_today = False
            sched.next_dose_last_sms = None

    # Barangays for filter dropdown
    from patients.models import Barangay
    barangays = Barangay.objects.filter(is_active=True).order_by('name')

    return render(request, 'vaccination/patient_schedule_list.html', {
        'schedules': page,
        'page_obj': page,
        'q': q,
        'status': status,
        'due': due,
        'barangay_id': barangay_id,
        'barangays': barangays,
        'date_from': date_from,
        'date_to': date_to,
        'total': total,
        'due_today_count': due_today_count,
        'overdue_count': overdue_count,
        'completed_schedules': completed_schedules,
        'today': today,
        'semaphore_enabled': getattr(settings, 'SEMAPHORE_ENABLED', False),
        'semaphore_sender': getattr(settings, 'SEMAPHORE_SENDER_NAME', 'RHUDumingag'),
    })


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
        records = records.filter(follow_up_date__lt=timezone.localdate(), status__in=['pending','contacted'])

    # stats
    base_qs = FollowUpRecord.objects.all()
    total = base_qs.count()
    pending = base_qs.filter(status='pending').count()
    completed = base_qs.filter(status='completed').count()
    missed = base_qs.filter(status='missed').count()
    overdue_count = base_qs.filter(follow_up_date__lt=timezone.localdate(), status__in=['pending','contacted']).count()

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
        'today': timezone.localdate(),
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


# --- SMS (Semaphore RHUDumingag) ---
from django.conf import settings

@login_required
def sms_log_list(request):
    """List SMS logs with filters for admin/staff monitoring."""
    q = request.GET.get('q', '').strip()
    reminder_type = request.GET.get('type', '')
    status = request.GET.get('status', '')
    date_from = request.GET.get('from', '')
    date_to = request.GET.get('to', '')

    logs = SMSLog.objects.select_related('patient', 'bite_case', 'dose__schedule').all()
    if q:
        logs = logs.filter(
            Q(recipient__icontains=q)
            | Q(patient__first_name__icontains=q)
            | Q(patient__last_name__icontains=q)
            | Q(patient__patient_number__icontains=q)
            | Q(bite_case__case_number__icontains=q)
            | Q(message__icontains=q)
        )
    if reminder_type:
        logs = logs.filter(reminder_type=reminder_type)
    if status:
        logs = logs.filter(status=status)
    if date_from:
        try:
            d = datetime.strptime(date_from, '%Y-%m-%d').date()
            logs = logs.filter(sent_at__date__gte=d)
        except ValueError:
            pass
    if date_to:
        try:
            d = datetime.strptime(date_to, '%Y-%m-%d').date()
            logs = logs.filter(sent_at__date__lte=d)
        except ValueError:
            pass

    logs = logs.order_by('-sent_at')
    total = logs.count()
    queued = SMSLog.objects.filter(status='queued').count()
    sent = SMSLog.objects.filter(status='sent').count()
    failed = SMSLog.objects.filter(status='failed').count()

    paginator = Paginator(logs, 20)
    page = paginator.get_page(request.GET.get('page'))
    return render(request, 'vaccination/sms_log_list.html', {
        'logs': page, 'page_obj': page,
        'q': q, 'reminder_type': reminder_type, 'status': status,
        'date_from': date_from, 'date_to': date_to,
        'total': total, 'queued': queued, 'sent': sent, 'failed': failed,
    })


@login_required
def send_sms_for_dose(request, pk):
    """Manual send SMS for a single dose (3days_before or on_day)."""
    dose = get_object_or_404(VaccineDose.objects.select_related('schedule__bite_case__patient', 'schedule__bite_case'), pk=pk)
    # Only staff allowed (non-patient)
    if getattr(request.user, 'role', '') == 'patient':
        messages.error(request, 'Not authorized.')
        return redirect('vaccination:schedule_detail', pk=dose.schedule.pk)

    if request.method == 'POST':
        reminder_type = request.POST.get('reminder_type', 'on_day')
        if reminder_type not in ('3days_before', 'on_day'):
            reminder_type = 'on_day'
        force = request.POST.get('force') == '1'
        dry_run = request.POST.get('dry_run') == '1' or not getattr(settings, 'SEMAPHORE_ENABLED', False) and not request.POST.get('force_send') == '1'

        from .sms import send_dose_reminder, normalize_ph_number
        number = dose.schedule.bite_case.patient.contact_number
        normalized = normalize_ph_number(number)
        if not normalized:
            messages.error(request, f'Invalid patient number: {number}')
            return redirect('vaccination:schedule_detail', pk=dose.schedule.pk)

        # If dry_run explicitly or SEMAPHORE_ENABLED False and not force_send, simulate
        if dry_run:
            from .sms import build_reminder_message
            msg = build_reminder_message(dose.schedule.bite_case.patient, dose.schedule.bite_case, dose, reminder_type)
            messages.info(request, f'[DRY RUN] Would send to {normalized}: {msg[:120]}... (Enable SEMAPHORE_ENABLED to actually send)')
            # Still log as queued simulated? Don't save yet
            # Create a log with status queued simulated for audit
            SMSLog.objects.create(
                dose=dose,
                patient=dose.schedule.bite_case.patient,
                bite_case=dose.schedule.bite_case,
                reminder_type=reminder_type,
                recipient=normalized,
                message=msg,
                scheduled_date=dose.scheduled_date,
                status='queued',
                api_response='DRY RUN - not sent (SEMAPHORE_ENABLED=False or dry_run)',
            )
            AuditLog.objects.create(user=request.user, action='SEND_SMS_DRYRUN', description=f'Dry run SMS {reminder_type} to {normalized} for {dose}')
        else:
            log, result = send_dose_reminder(dose, reminder_type=reminder_type, dry_run=False, force=force)
            if result.get('success'):
                messages.success(request, f'SMS sent to {log.recipient} ({log.get_reminder_type_display()}) for {dose.dose_label} - {log.status}')
                AuditLog.objects.create(user=request.user, action='SEND_SMS', description=f'Sent SMS {reminder_type} to {log.recipient} for {dose} status={log.status}')
            else:
                if result.get('skipped'):
                    messages.warning(request, f'Skipped: {result.get("error")}')
                else:
                    messages.error(request, f'SMS failed: {result.get("error")}')
                    # Log already created inside send_dose_reminder for failed; if not, create failed log
                AuditLog.objects.create(user=request.user, action='SEND_SMS_FAILED', description=f'Failed SMS {reminder_type} to {number} for {dose}: {result.get("error")}')
        return redirect('vaccination:schedule_detail', pk=dose.schedule.pk)

    # GET shows confirmation? Redirect
    return redirect('vaccination:schedule_detail', pk=dose.schedule.pk)


@login_required
def trigger_sms_reminders(request):
    """Trigger the same logic as management command but via web (for daily cron testing)."""
    if getattr(request.user, 'role', '') == 'patient':
        messages.error(request, 'Not authorized.')
        return redirect('dashboard:index')

    if request.method == 'POST':
        rtype = request.POST.get('rtype', 'all')
        dry_run = request.POST.get('dry_run') == '1'
        # Run the logic inline (reuse command logic simplified)
        today = timezone.localdate()
        from .models import VaccineDose
        from .sms import send_dose_reminder, normalize_ph_number
        from django.db.models import Q

        def _has_valid_sms_number(patient):
            cands = [
                patient.contact_number,
                getattr(patient, "emergency_contact_number", ""),
                getattr(getattr(patient, "account", None), "mobile", "") if getattr(patient, "account", None) else "",
            ]
            for cand in cands:
                if cand and normalize_ph_number(cand):
                    return True
            return False

        def _get_valid_number(patient):
            cands = [
                patient.contact_number,
                getattr(patient, "emergency_contact_number", ""),
                getattr(getattr(patient, "account", None), "mobile", "") if getattr(patient, "account", None) else "",
            ]
            for cand in cands:
                n = normalize_ph_number(cand)
                if n:
                    return n
            return normalize_ph_number(patient.contact_number) or patient.contact_number

        targets = []
        if rtype in ('all', '3days'):
            target_3 = today + timedelta(days=3)
            qs = VaccineDose.objects.filter(scheduled_date=target_3, dose_status__in=['scheduled', 'rescheduled']).select_related('schedule__bite_case__patient', 'schedule__bite_case__patient__account')
            for dose in qs:
                if not _has_valid_sms_number(dose.schedule.bite_case.patient):
                    continue
                # skip if already sent today
                if not dry_run and SMSLog.objects.filter(dose=dose, reminder_type='3days_before', scheduled_date=dose.scheduled_date, sent_at__date=today).exists():
                    continue
                targets.append((dose, '3days_before'))
        if rtype in ('all', 'on_day'):
            qs2 = VaccineDose.objects.filter(scheduled_date=today, dose_status__in=['scheduled', 'rescheduled']).select_related('schedule__bite_case__patient', 'schedule__bite_case__patient__account')
            for dose in qs2:
                if not _has_valid_sms_number(dose.schedule.bite_case.patient):
                    continue
                if not dry_run and SMSLog.objects.filter(dose=dose, reminder_type='on_day', scheduled_date=dose.scheduled_date, sent_at__date=today).exists():
                    continue
                targets.append((dose, 'on_day'))

        sent = 0
        failed = 0
        for dose, rtype_mapped in targets:
            if dry_run:
                from .sms import build_reminder_message
                msg = build_reminder_message(dose.schedule.bite_case.patient, dose.schedule.bite_case, dose, rtype_mapped)
                normalized = _get_valid_number(dose.schedule.bite_case.patient)
                SMSLog.objects.create(
                    dose=dose, patient=dose.schedule.bite_case.patient, bite_case=dose.schedule.bite_case,
                    reminder_type=rtype_mapped, recipient=normalized, message=msg,
                    scheduled_date=dose.scheduled_date, status='queued', api_response='DRY RUN via trigger button'
                )
                sent += 1
            else:
                log, result = send_dose_reminder(dose, reminder_type=rtype_mapped)
                if result.get('success'):
                    sent += 1
                else:
                    failed += 1
        messages.success(request, f'Trigger done: {sent} sent/queued, {failed} failed (dry_run={dry_run}) for {rtype}')
        AuditLog.objects.create(user=request.user, action='TRIGGER_SMS', description=f'Trigger SMS {rtype} dry_run={dry_run} sent={sent} failed={failed}')
        return redirect('vaccination:sms_log_list')
    return redirect('vaccination:sms_log_list')


# Update schedule_detail to include SMS logs
_orig_schedule_detail = schedule_detail
@login_required
def schedule_detail(request, pk):
    # Override to add SMS context
    schedule = get_object_or_404(VaccinationSchedule.objects.select_related('bite_case__patient').prefetch_related('doses__sms_logs'), pk=pk)
    today = timezone.localdate()
    total = schedule.doses.count()
    completed = schedule.doses.filter(dose_status='completed').count()
    progress = int((completed / total * 100) if total else 0)
    # SMS logs per dose
    sms_by_dose = {}
    try:
        logs = SMSLog.objects.filter(dose__schedule=schedule).select_related('dose').order_by('-sent_at')
        for log in logs:
            sms_by_dose.setdefault(log.dose_id, []).append(log)
    except Exception:
        sms_by_dose = {}
        logs = SMSLog.objects.none()

    # Attach per-dose logs for template convenience (avoid dict lookup in template)
    for dose in schedule.doses.all():
        dose.sms_logs_cached = sms_by_dose.get(dose.id, [])

    # Also overall setting info for template
    semaphore_enabled = getattr(settings, 'SEMAPHORE_ENABLED', False)
    semaphore_sender = getattr(settings, 'SEMAPHORE_SENDER_NAME', 'RHUDumingag')

    return render(request, 'vaccination/schedule_detail.html', {
        'schedule': schedule, 'today': today,
        'total': total, 'completed': completed, 'progress': progress,
        'sms_by_dose': sms_by_dose,
        'sms_logs': logs[:20],
        'semaphore_enabled': semaphore_enabled,
        'semaphore_sender': semaphore_sender,
    })
