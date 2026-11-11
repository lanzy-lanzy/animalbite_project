from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, OuterRef, Subquery, Min
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_POST
from datetime import date, timedelta, datetime
import logging
import re
from django.utils import timezone
from .models import VaccinationSchedule, VaccineDose, FollowUpRecord, AnimalObservation, SMSLog
from .forms import VaccinationScheduleForm, VaccineDoseForm, FollowUpRecordForm, AnimalObservationForm
from .schedule_utils import prescribed_minimum_date
from bite_cases.models import AnimalBiteCase
from audit.models import AuditLog

logger = logging.getLogger(__name__)

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
        elif status == 'pending':
            schedules = schedules.filter(doses__dose_status='pending').distinct()
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

def _may_set_schedule(user):
    """Only nurses (and admins) may trigger/author a vaccination schedule."""
    role = getattr(user, 'role', '')
    return user.is_authenticated and role in ('nurse', 'admin')


def _block_admin_dose_administration(request):
    """Admins view vaccination records only; the Nurse workflow owns dose updates.

    Enforced server-side so a direct GET/POST to a dose-update endpoint is denied
    with 403 even though the admin timeline hides the controls.
    """
    if getattr(request.user, 'role', '') == 'admin':
        raise PermissionDenied(
            'Vaccination dose administration is restricted to the Nurse workflow.')


def _schedule_days():
    """Dose day offsets from SystemSetting 'vaccination_days', else the PEP default."""
    from .schedule_utils import get_dose_days
    return get_dose_days()


@login_required
def schedule_trigger(request, case_pk):
    """Nurse-triggered one-click schedule creation.

    GET  -> confirmation page (case summary + the dose days that will be created)
    POST -> creates the schedule + doses, then redirects to the schedule detail.
    """
    case = get_object_or_404(AnimalBiteCase.objects.select_related('patient'), pk=case_pk)

    if not _may_set_schedule(request.user):
        messages.error(request, 'Only a nurse or admin can set a vaccination schedule.')
        return redirect('bite_cases:case_detail', pk=case.pk)

    try:
        schedule = case.vaccination_schedule
        messages.info(request, 'Vaccination schedule is already set for this case.')
        return redirect('vaccination:schedule_detail', pk=schedule.pk)
    except VaccinationSchedule.DoesNotExist:
        pass

    # Doses are dated from the consultation date when present, else today.
    from .schedule_utils import schedule_base_date
    base_date = schedule_base_date(case)
    days = _schedule_days()

    if request.method == 'POST':
        from .schedule_utils import ensure_vaccination_schedule
        schedule, created = ensure_vaccination_schedule(case, created_by=request.user)
        if created:
            AuditLog.objects.create(
                user=request.user,
                action='CREATE_SCHEDULE',
                description=(
                    'Nurse %s set vaccination schedule for %s (%d doses, 1st on %s)'
                    % (request.user.username, case.case_number, len(days), base_date)
                ),
            )
            messages.success(
                request,
                'Vaccination schedule set for %s - 1st Dose on %s; assign the remaining dates as they are confirmed.'
                % (case.case_number, base_date),
            )
        return redirect('vaccination:schedule_detail', pk=schedule.pk)

    return render(request, 'vaccination/schedule_trigger.html', {
        'case': case,
        'days': days,
        'dose_dates': [(d, base_date if index == 0 else None) for index, d in enumerate(days)],
        'base_date': base_date,
    })


@login_required
def schedule_create(request, case_pk):
    """Manual schedule editor (kept for custom/protocol schedules)."""
    if not _may_set_schedule(request.user):
        messages.error(request, 'Only a nurse or admin can set a vaccination schedule.')
        return redirect('bite_cases:case_detail', pk=case_pk)

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
            # Only the 1st dose is dated; the nurse schedules the rest.
            from .schedule_utils import materialise_doses
            materialise_doses(
                schedule,
                base_date=(timezone.localtime(case.date_time_consultation).date()
                           if case.date_time_consultation else None),
            )
            AuditLog.objects.create(user=request.user, action='CREATE_SCHEDULE', description=f'Created vaccination schedule for {case.case_number}')
            messages.success(request, 'Vaccination schedule created.')
            return redirect('vaccination:schedule_detail', pk=schedule.pk)
    else:
        form = VaccinationScheduleForm()
    return render(request, 'vaccination/schedule_form.html', {'form': form, 'case': case})

@login_required
def schedule_detail(request, pk):
    """Staff schedule view with the per-dose SMS history attached.

    Ownership enforcement: a patient may only open a schedule that belongs to
    their own account, and is routed to the read-only portal view. Any attempt
    to reach another patient's schedule is denied — no cross-patient exposure.
    Staff (admin / nurse / doctor) keep their normal RBAC-scoped access.
    """
    schedule = get_object_or_404(
        VaccinationSchedule.objects
        .select_related('bite_case__patient')
        .prefetch_related('doses__sms_logs'),
        pk=pk,
    )
    if getattr(request.user, 'role', '') == 'patient':
        if schedule.bite_case.patient.account_id != request.user.pk:
            messages.error(request, 'You do not have access to this vaccination schedule.')
            return redirect('accounts:patient_portal')
        return redirect('vaccination:my_schedule_detail', pk=schedule.pk)

    today = timezone.localdate()
    total = schedule.doses.count()
    completed = schedule.doses.filter(dose_status='completed').count()
    progress = int((completed / total * 100) if total else 0)

    logs = (
        SMSLog.objects.filter(dose__schedule=schedule)
        .select_related('dose')
        .order_by('-sent_at')
    )
    sms_by_dose = {}
    for log in logs:
        sms_by_dose.setdefault(log.dose_id, []).append(log)
    for dose in schedule.doses.all():
        dose.sms_logs_cached = sms_by_dose.get(dose.id, [])

    return render(request, 'vaccination/schedule_detail.html', {
        'schedule': schedule, 'today': today,
        'total': total, 'completed': completed, 'progress': progress,
        'sms_by_dose': sms_by_dose,
        'sms_logs': logs[:20],
    })


@login_required
def my_schedule_detail(request, pk):
    """Read-only schedule view for the patient portal.

    Patients may only open schedules that belong to their own bite cases;
    anything else bounces back to the portal.
    """
    if getattr(request.user, 'role', '') != 'patient':
        return redirect('vaccination:schedule_detail', pk=pk)
    schedule = get_object_or_404(
        VaccinationSchedule.objects.select_related('bite_case__patient'),
        pk=pk,
        bite_case__patient__account=request.user,
    )
    today = timezone.localdate()
    total = schedule.doses.count()
    completed = schedule.doses.filter(dose_status='completed').count()
    progress = int((completed / total * 100) if total else 0)
    return render(request, 'vaccination/patient_schedule_detail.html', {
        'schedule': schedule, 'today': today,
        'total': total, 'completed': completed, 'progress': progress,
    })

@login_required
def dose_update(request, pk):
    _block_admin_dose_administration(request)
    dose = get_object_or_404(VaccineDose.objects.select_related('schedule__bite_case'), pk=pk)
    if getattr(request.user, 'role', '') == 'patient':
        messages.error(request, 'Not authorized.')
        return redirect('accounts:patient_portal')
    if request.method == 'POST':
        previous_status = dose.dose_status
        form = VaccineDoseForm(request.POST, instance=dose)
        if form.is_valid():
            obj = form.save(commit=False)
            # Stock leaves the shelf only on the transition into Completed, so
            # re-saving an administered dose never deducts twice.
            if obj.dose_status == 'completed' and previous_status != 'completed':
                if not obj.administered_by:
                    obj.administered_by = request.user
                if obj.vaccine_brand:
                    from inventory.models import VaccineInventory
                    # The nurse types the brand ("Verorab"); the lot row also holds
                    # it in `brand` while `vaccine_name` may be the generic product.
                    lot = Q(brand__iexact=obj.vaccine_brand) | Q(
                        vaccine_name__icontains=obj.vaccine_brand)
                    if obj.batch_number:
                        lot &= Q(batch_number__iexact=obj.batch_number)
                    inv = VaccineInventory.objects.filter(
                        lot,
                        expiration_date__gt=timezone.localdate(),
                        quantity_available__gt=0,
                    ).order_by('expiration_date').first()
                    if inv:
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
    """Vaccination Records: one row per patient who has a vaccination schedule.

    Search and barangay filtering stay available; dose status, due date, and
    creation-date filters are intentionally gone. The latest scheduled bite
    case supplies the representative View/Manage target for patients with more
    than one case. This view is read-only and never creates schedules.
    """
    from patients.models import Barangay, Patient
    from pre_registrations.models import PreRegistration

    q = request.GET.get('q', '').strip()
    barangay_id = request.GET.get('barangay', '')

    latest_schedule = (
        VaccinationSchedule.objects
        .filter(bite_case__patient=OuterRef('pk'))
        .order_by('-bite_case__created_at', '-created_at')
    )
    approved_registration = (
        PreRegistration.objects
        .filter(converted_patient=OuterRef('pk'), verified_at__isnull=False)
        .order_by('-verified_at')
    )

    patients = (
        Patient.objects
        .filter(bite_cases__vaccination_schedule__isnull=False)
        .select_related('barangay')
        .annotate(
            schedule_pk=Subquery(latest_schedule.values('pk')[:1]),
            approved_at=Subquery(approved_registration.values('verified_at')[:1]),
        )
        .distinct()
    )

    if q:
        from utils.phone import normalize_to_63
        query_63 = normalize_to_63(q) or q
        patients = patients.filter(
            Q(first_name__icontains=q)
            | Q(middle_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(patient_number__icontains=q)
            | Q(contact_number__icontains=q)
            | Q(contact_number__icontains=query_63)
            | Q(address__icontains=q)
            | Q(barangay__name__icontains=q)
        )

    if barangay_id:
        try:
            patients = patients.filter(barangay_id=int(barangay_id))
        except ValueError:
            pass

    patients = patients.order_by('-created_at')
    total = patients.count()
    paginator = Paginator(patients, 12)
    page = paginator.get_page(request.GET.get('page'))

    barangays = Barangay.objects.filter(is_active=True).order_by('name')

    return render(request, 'vaccination/patient_schedule_list.html', {
        'patients': page,
        'page_obj': page,
        'start_index': page.start_index(),
        'q': q,
        'barangay_id': barangay_id,
        'barangays': barangays,
        'total': total,
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


# --- SMS reminders ---

SMS_LOG_ROLES = {'nurse', 'admin'}
_SENSITIVE_SMS_RE = re.compile(
    r'(?i)(api[\s_-]?key|token|secret|password|bearer)\s*[:=]\s*\S+')


def _sms_logs_denied(request):
    messages.error(request, 'SMS reminders are available to clinic staff only.')
    if getattr(request.user, 'role', '') == 'patient':
        return redirect('accounts:patient_portal')
    return redirect('dashboard:index')


def _sms_log_queryset():
    return SMSLog.objects.select_related(
        'patient',
        'bite_case__patient',
        'dose__schedule__bite_case__patient',
    )


def _decorate_sms_log(log):
    """Resolve patient/case/dose from whichever saved relation exists.

    Older rows can have a null patient FK even when their case is saved; the
    display always falls back to the real saved record instead of showing blank.
    """
    dose = log.dose
    case = log.bite_case
    if not case and dose:
        try:
            case = dose.schedule.bite_case
        except ObjectDoesNotExist:
            # Legacy rows can point at a schedule whose case no longer exists.
            case = None
    log.display_patient = log.patient or (case.patient if case else None)
    log.display_case = case
    if dose:
        log.display_dose_label = dose.number_label
        log.display_appointment = log.scheduled_date or dose.scheduled_date
    else:
        log.display_dose_label = ''
        log.display_appointment = log.scheduled_date
    return log


def _sms_failure_reason(log):
    """Saved failure text with credentials and provider branding removed."""
    raw = (log.api_response or '').strip()
    if not raw:
        return ''
    sanitized = _SENSITIVE_SMS_RE.sub(r'\1: [hidden]', raw)
    sanitized = re.sub(r'(?i)semaphore', 'SMS provider', sanitized)
    first_line = next((line.strip() for line in sanitized.splitlines() if line.strip()), '')
    return first_line[:300]


UPCOMING_FILTERS = (
    ('all', 'All Upcoming'),
    ('2days', '2 Days Before'),
    ('tomorrow', 'Tomorrow'),
    ('today', 'Today'),
)
_UPCOMING_STATUS_LABELS = {
    'sent': 'Sent',
    'queued': 'Queued',
    'pending': 'Queued',
    'failed': 'Failed',
}


def _mask_mobile(raw):
    digits = re.sub(r'\D', '', raw or '')
    if len(digits) >= 7:
        return f'{digits[:4]}••••{digits[-4:]}'
    return digits or '—'


def _reminder_date_badge(days_left):
    if days_left == 0:
        return 'Today'
    if days_left == 1:
        return 'Tomorrow'
    if days_left <= 3:
        return f'{days_left} Days Left'
    return 'Upcoming'


def _build_upcoming_reminders(request, today):
    """Scheduled 2nd+ doses still awaiting an appointment reminder."""
    from .sms import build_reminder_message, normalize_ph_number

    upcoming_filter = request.GET.get('upcoming', 'all')
    if upcoming_filter not in dict(UPCOMING_FILTERS):
        upcoming_filter = 'all'

    first_dose_ids = (
        VaccineDose.objects.values('schedule_id')
        .annotate(first_id=Min('id'))
        .values_list('first_id', flat=True)
    )
    doses = (
        VaccineDose.objects.filter(
            scheduled_date__gte=today,
            dose_status__in=['scheduled', 'rescheduled'],
        )
        .exclude(pk__in=first_dose_ids)
        .exclude(schedule__bite_case__case_status='archived')
        .select_related('schedule__bite_case__patient')
        .order_by('scheduled_date', 'id')
    )
    if upcoming_filter == '2days':
        doses = doses.filter(scheduled_date=today + timedelta(days=2))
    elif upcoming_filter == 'tomorrow':
        doses = doses.filter(scheduled_date=today + timedelta(days=1))
    elif upcoming_filter == 'today':
        doses = doses.filter(scheduled_date=today)
    doses = list(doses)

    latest_logs = {}
    if doses:
        for log in SMSLog.objects.filter(
            dose_id__in=[dose.pk for dose in doses],
        ).order_by('sent_at'):
            latest_logs[log.dose_id] = log

    for dose in doses:
        case = dose.schedule.bite_case
        patient = case.patient
        latest = latest_logs.get(dose.pk)
        status = latest.status if latest else 'not_sent'
        dose.reminder_status = status
        dose.reminder_status_label = _UPCOMING_STATUS_LABELS.get(status, 'Not Sent')
        dose.days_left = (dose.scheduled_date - today).days
        dose.date_badge = _reminder_date_badge(dose.days_left)
        dose.appointment_status = dose.get_dose_status_display()

        candidates = [
            patient.contact_number,
            getattr(patient, 'emergency_contact_number', ''),
            getattr(getattr(patient, 'account', None), 'mobile', ''),
        ]
        full_mobile = ''
        for value in candidates:
            normalized = normalize_ph_number(value) if value else None
            if normalized:
                full_mobile = normalized
                break
        dose.full_mobile = full_mobile
        dose.has_mobile = bool(full_mobile)
        dose.masked_mobile = _mask_mobile(full_mobile) if full_mobile else 'No registered number.'

        dose.has_sms = latest is not None
        if latest:
            sent_at = timezone.localtime(latest.sent_at)
            dose.last_sms_at = sent_at.strftime('%b %d, %Y %I:%M %p')
            dose.last_sms_type = latest.get_reminder_type_display()
            failure = _sms_failure_reason(latest) if latest.status == 'failed' else ''
            dose.last_sms_result = (
                f'{latest.get_status_display()} — {failure}' if failure
                else latest.get_status_display()
            )
        else:
            dose.last_sms_at = ''
            dose.last_sms_type = ''
            dose.last_sms_result = ''

        dose.manual_dose_label = dose.number_label
        dose.preview_immediate = build_reminder_message(patient, case, dose, 'manual_immediate')
        dose.preview_2days = build_reminder_message(patient, case, dose, 'manual_2days_before')
        dose.preview_1day = build_reminder_message(patient, case, dose, 'manual_1day_before')
        dose.preview_general = build_reminder_message(patient, case, dose, 'manual_custom')
        if dose.days_left == 2:
            dose.preview_details = dose.preview_2days
        elif dose.days_left == 1:
            dose.preview_details = dose.preview_1day
        else:
            dose.preview_details = dose.preview_general
        dose.custom_prefix = (
            f'ABTC Dumingag: Paalala para kay {patient.full_name()} ({case.case_number}) - '
            f'{dose.number_label} sa {dose.scheduled_date:%b %d, %Y (%a)}. '
        )
        dose.custom_suffix = ' -ABTC Dumingag'

    params = request.GET.copy()
    params.pop('page', None)
    links = []
    for value, label in UPCOMING_FILTERS:
        link_params = params.copy()
        if value == 'all':
            link_params.pop('upcoming', None)
        else:
            link_params['upcoming'] = value
        links.append((value, label, link_params.urlencode()))

    return doses, upcoming_filter, links


@login_required
def sms_log_list(request):
    """SMS reminder history shared by the Admin and Nurse portals."""
    if getattr(request.user, 'role', '') not in SMS_LOG_ROLES:
        return _sms_logs_denied(request)

    q = request.GET.get('q', '').strip()
    reminder_type = request.GET.get('type', '')
    status = request.GET.get('status', '')
    scheduled = request.GET.get('scheduled', '')
    date_from = request.GET.get('from', '')
    date_to = request.GET.get('to', '')

    base = _sms_log_queryset()
    logs = base
    if q:
        logs = logs.filter(
            Q(recipient__icontains=q)
            | Q(patient__first_name__icontains=q)
            | Q(patient__last_name__icontains=q)
            | Q(patient__patient_number__icontains=q)
            | Q(bite_case__case_number__icontains=q)
            | Q(bite_case__patient__first_name__icontains=q)
            | Q(bite_case__patient__last_name__icontains=q)
            | Q(dose__schedule__bite_case__case_number__icontains=q)
            | Q(message__icontains=q)
        )
    if reminder_type:
        logs = logs.filter(reminder_type=reminder_type)
    if status:
        logs = logs.filter(status=status)
    if scheduled:
        try:
            logs = logs.filter(scheduled_date=date.fromisoformat(scheduled))
        except ValueError:
            scheduled = ''
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
    total = base.count()
    sent = base.filter(status='sent').count()
    queued = base.filter(status='queued').count()
    failed = base.filter(status='failed').count()

    paginator = Paginator(logs, 20)
    page = paginator.get_page(request.GET.get('page'))
    for log in page:
        _decorate_sms_log(log)

    params = request.GET.copy()
    params.pop('page', None)
    today = timezone.localdate()
    upcoming_doses, upcoming_filter, upcoming_links = _build_upcoming_reminders(request, today)
    return render(request, 'vaccination/sms_log_list.html', {
        'logs': page, 'page_obj': page,
        'q': q, 'reminder_type': reminder_type, 'status': status,
        'scheduled': scheduled,
        'date_from': date_from, 'date_to': date_to,
        'total': total, 'queued': queued, 'sent': sent, 'failed': failed,
        'querystring': params.urlencode(),
        'can_send_due': _may_manage_vaccination(request.user),
        'upcoming_doses': upcoming_doses,
        'upcoming_filter': upcoming_filter,
        'upcoming_links': upcoming_links,
    })


@login_required
def sms_log_detail(request, pk):
    """Read-only SMS reminder details shared by both portals."""
    if getattr(request.user, 'role', '') not in SMS_LOG_ROLES:
        return _sms_logs_denied(request)

    log = get_object_or_404(_sms_log_queryset(), pk=pk)
    _decorate_sms_log(log)
    return render(request, 'vaccination/sms_log_detail.html', {
        'log': log,
        'failure_reason': _sms_failure_reason(log) if log.status == 'failed' else '',
        'can_send_due': _may_manage_vaccination(request.user),
    })


_MANUAL_KIND_DAYS = {
    'manual_2days_before': 2,
    'manual_1day_before': 1,
}


def _manual_send_error(message, status=400):
    return JsonResponse({'ok': False, 'error': message}, status=status)


@login_required
@require_POST
def sms_manual_send(request, pk):
    """Send an additional manual reminder for one upcoming dose."""
    if not _may_manage_vaccination(request.user):
        return _manual_send_error('Not authorized to send SMS reminders.', status=403)

    dose = get_object_or_404(
        VaccineDose.objects.select_related(
            'schedule__bite_case__patient', 'schedule__bite_case__patient__account'),
        pk=pk,
    )
    case = dose.schedule.bite_case
    patient = case.patient
    kind = request.POST.get('reminder_type', '')
    note = request.POST.get('custom_message', '').strip()
    confirm_duplicate = request.POST.get('confirm_duplicate') == '1'

    if kind not in SMSLog.MANUAL_TYPES:
        return _manual_send_error('Select a valid reminder type.')
    if not dose.scheduled_date:
        return _manual_send_error('This dose has no scheduled appointment date.')
    if dose.dose_status not in ('scheduled', 'rescheduled'):
        return _manual_send_error('This dose is no longer eligible for a reminder.')
    today = timezone.localdate()
    if dose.scheduled_date < today:
        return _manual_send_error('The appointment date has already passed.')
    first_dose = dose.schedule.doses.order_by('id').first()
    if first_dose and first_dose.pk == dose.pk:
        return _manual_send_error('Upcoming reminders are for the 2nd dose onwards.')

    days_left = (dose.scheduled_date - today).days
    required_days = _MANUAL_KIND_DAYS.get(kind)
    if required_days is not None and days_left != required_days:
        return _manual_send_error(
            f'This appointment is {days_left} day(s) away; choose the matching reminder type.')
    if kind == 'manual_custom':
        if not note:
            return _manual_send_error('Enter a short custom reminder message.')
        if len(note) > 160:
            return _manual_send_error('Keep the custom reminder under 160 characters.')

    from .sms import build_reminder_message, normalize_ph_number, send_dose_reminder

    candidates = [
        patient.contact_number,
        getattr(patient, 'emergency_contact_number', ''),
        getattr(getattr(patient, 'account', None), 'mobile', ''),
    ]
    if not any(value and normalize_ph_number(value) for value in candidates):
        return _manual_send_error('The registered mobile number is missing or invalid.')

    message = build_reminder_message(patient, case, dose, kind, custom_note=note)
    duplicates = SMSLog.objects.filter(
        dose=dose,
        reminder_type=kind,
        scheduled_date=dose.scheduled_date,
        status__in=['sent', 'queued', 'pending'],
    )
    if kind == 'manual_custom':
        duplicates = duplicates.filter(message=message)
    duplicate = duplicates.order_by('-sent_at').first()
    if duplicate and not confirm_duplicate:
        return JsonResponse({
            'ok': False,
            'duplicate': True,
            'warning': (
                f'An equivalent {duplicate.get_reminder_type_display()} reminder is already '
                f'{duplicate.get_status_display().lower()} for this dose and appointment date.'
            ),
        }, status=409)

    log, result = send_dose_reminder(dose, kind, force=True, custom_note=note)
    if log is None:
        return _manual_send_error('The SMS reminder could not be sent.', status=502)

    AuditLog.objects.create(
        user=request.user, action='SEND_SMS',
        description=(
            f'Manual SMS {kind} to {log.recipient} for {dose} '
            f'status={log.status}'
        ),
    )
    if log.status == 'sent':
        messages.success(request, 'SMS reminder sent successfully.')
    elif log.status == 'queued':
        messages.success(request, 'SMS reminder queued with the provider.')
    elif log.status == 'failed':
        messages.error(request, 'SMS reminder could not be sent. Please check the number and try again.')
    else:
        messages.info(request, 'SMS reminder is being processed.')
    return JsonResponse({
        'ok': True,
        'status': log.status,
        'redirect': reverse('vaccination:sms_log_list'),
    })


@login_required
def send_sms_for_dose(request, pk):
    """Send one reminder for a single scheduled dose."""
    dose = get_object_or_404(
        VaccineDose.objects.select_related(
            'schedule__bite_case__patient', 'schedule__bite_case'),
        pk=pk,
    )
    if getattr(request.user, 'role', '') == 'patient':
        messages.error(request, 'Not authorized.')
        return redirect('vaccination:schedule_detail', pk=dose.schedule.pk)
    if not dose.scheduled_date:
        messages.error(request, 'Assign a scheduled date before sending a reminder for this dose.')
        return redirect('vaccination:schedule_detail', pk=dose.schedule.pk)

    if request.method == 'POST':
        reminder_type = request.POST.get('reminder_type', 'on_day')
        if reminder_type not in ('3days_before', 'on_day'):
            reminder_type = 'on_day'
        force = request.POST.get('force') == '1'

        from .sms import normalize_ph_number, send_dose_reminder
        number = dose.schedule.bite_case.patient.contact_number
        if not normalize_ph_number(number):
            messages.error(request, f'Invalid patient number: {number}')
            return redirect('vaccination:schedule_detail', pk=dose.schedule.pk)

        log, result = send_dose_reminder(
            dose, reminder_type=reminder_type, force=force)
        if result.get('success'):
            messages.success(
                request,
                f'SMS reminder sent to {log.recipient} for {dose.dose_label} ({log.get_reminder_type_display()}).',
            )
            AuditLog.objects.create(
                user=request.user, action='SEND_SMS',
                description=(
                    f'Sent SMS {reminder_type} to {log.recipient} for {dose} '
                    f'status={log.status}'
                ),
            )
        elif result.get('skipped'):
            messages.warning(request, 'A reminder was already sent today for this dose.')
        else:
            messages.error(request, 'The SMS reminder could not be sent.')
            AuditLog.objects.create(
                user=request.user, action='SEND_SMS_FAILED',
                description=(
                    f'Failed SMS {reminder_type} to {number} for {dose}: '
                    f'{result.get("error")}'
                ),
            )
    return redirect('vaccination:schedule_detail', pk=dose.schedule.pk)


@login_required
def trigger_sms_reminders(request):
    """Send every reminder due today: 3 days before and on the appointment day."""
    if getattr(request.user, 'role', '') == 'patient':
        messages.error(request, 'Not authorized.')
        return redirect('dashboard:index')
    if request.method != 'POST':
        return redirect('vaccination:sms_log_list')

    rtype = request.POST.get('rtype', 'all')
    today = timezone.localdate()
    from .sms import normalize_ph_number, send_dose_reminder

    def _has_valid_number(patient):
        candidates = [
            patient.contact_number,
            getattr(patient, 'emergency_contact_number', ''),
            getattr(getattr(patient, 'account', None), 'mobile', '') if getattr(patient, 'account', None) else '',
        ]
        return any(normalize_ph_number(value) for value in candidates if value)

    targets = []
    if rtype in ('all', '3days'):
        due = VaccineDose.objects.filter(
            scheduled_date=today + timedelta(days=3),
            dose_status__in=['scheduled', 'rescheduled'],
        ).select_related(
            'schedule__bite_case__patient',
            'schedule__bite_case__patient__account',
        )
        targets.extend(
            (dose, '3days_before') for dose in due
            if _has_valid_number(dose.schedule.bite_case.patient)
        )
    if rtype in ('all', 'on_day'):
        due = VaccineDose.objects.filter(
            scheduled_date=today,
            dose_status__in=['scheduled', 'rescheduled'],
        ).select_related(
            'schedule__bite_case__patient',
            'schedule__bite_case__patient__account',
        )
        targets.extend(
            (dose, 'on_day') for dose in due
            if _has_valid_number(dose.schedule.bite_case.patient)
        )

    sent = skipped = failed = 0
    for dose, reminder_type in targets:
        log, result = send_dose_reminder(dose, reminder_type=reminder_type)
        if result.get('success'):
            sent += 1
        elif result.get('skipped'):
            skipped += 1
        else:
            failed += 1

    if targets:
        messages.success(
            request,
            f'Reminders sent: {sent}. Already sent today: {skipped}. Failed: {failed}.',
        )
    else:
        messages.info(request, 'No reminders are due right now.')
    AuditLog.objects.create(
        user=request.user, action='TRIGGER_SMS',
        description=(
            f'Triggered SMS reminders ({rtype}): sent={sent} skipped={skipped} failed={failed}'
        ),
    )
    return redirect('vaccination:sms_log_list')


def build_record_context(selected_case):
    """Read-only record data for exactly one bite case.

    Shared by the staff Vaccination Record sheet and the Nurse Portal's
    View Doctor Findings page. Nothing here writes; dose rows, stock, and
    assessment fields are rendered exactly as stored.
    """
    from doctor.models import ClinicalAssessment

    patient = selected_case.patient
    assessment = ClinicalAssessment.objects.filter(bite_case=selected_case).first()
    prescriptions = []
    if assessment:
        prescriptions = list(
            assessment.prescriptions.prefetch_related('medicine').order_by('id')
        )

    try:
        case_schedule = selected_case.vaccination_schedule
    except VaccinationSchedule.DoesNotExist:
        case_schedule = None
    doses = list(case_schedule.doses.all()) if case_schedule else []

    ordinals = ('1st', '2nd', '3rd', '4th', '5th')
    dose_rows = []
    for index, dose in enumerate(doses, start=1):
        ordinal = ordinals[index - 1] if index <= len(ordinals) else f'{index}th'
        if dose.actual_date:
            status, status_key = 'Completed', 'completed'
        elif not dose.scheduled_date:
            status, status_key = 'Pending Scheduling', 'pending'
        elif dose.dose_status == 'completed':
            # A completed flag without an administration date is not proof of
            # vaccination — the record only shows what actually happened.
            status, status_key = 'Scheduled', 'scheduled'
        else:
            status = dose.get_dose_status_display()
            status_key = dose.dose_status
        dose_rows.append({
            'label': f'{ordinal} Dose',
            'date': dose.scheduled_date or dose.actual_date,
            'status': status,
            'status_key': status_key,
        })

    first_dose_date = next(
        (d.scheduled_date or d.actual_date for d in doses if d.scheduled_date or d.actual_date),
        None,
    )
    if first_dose_date is None and assessment:
        first_dose_date = assessment.first_dose_date

    dose_count = 0
    if assessment and assessment.dose_protocol:
        dose_count = len(ClinicalAssessment.protocol_days(assessment.dose_protocol))
    if not dose_count:
        dose_count = len(doses)
    if dose_count:
        dose_schedule_label = f'{dose_count} Dose{"" if dose_count == 1 else "s"}'
    elif assessment and assessment.dose_schedule_plan:
        dose_schedule_label = assessment.dose_schedule_plan
    else:
        dose_schedule_label = ''

    vaccine_brand = (assessment.vaccine_brand_plan if assessment else '') or next(
        (d.vaccine_brand for d in doses if d.vaccine_brand), ''
    )

    category_label = category_description = ''
    if assessment and assessment.category_confirmed:
        category_label, _, category_description = (
            assessment.get_category_confirmed_display().partition(' - ')
        )

    return {
        'case_schedule': case_schedule,
        'patient': patient,
        'bite_case': selected_case,
        'assessment': assessment,
        'latest_vitals': patient.get_latest_vitals(),
        'doses': doses,
        'dose_rows': dose_rows,
        'prescriptions': prescriptions,
        'dose_schedule_label': dose_schedule_label,
        'first_dose_date': first_dose_date,
        'vaccine_brand': vaccine_brand,
        'category_label': category_label,
        'category_description': category_description,
    }


@login_required
def record_detail(request, pk):
    """Read-only Vaccination Record for one schedule (patient record sheet).

    The page is print-oriented: it shows the patient, the selected bite case,
    the latest vitals, and the doctor's assessment exactly as stored. Nothing
    here writes — inventory stock and dose rows stay untouched.
    """
    schedule = get_object_or_404(
        VaccinationSchedule.objects.select_related(
            'bite_case__patient__barangay', 'bite_case__patient__created_by',
            'bite_case__created_by', 'created_by',
        ).prefetch_related('doses'),
        pk=pk,
    )
    patient = schedule.bite_case.patient

    # Patients never see the staff record sheet; they keep their portal view.
    if getattr(request.user, 'role', '') == 'patient':
        if patient.account_id != request.user.pk:
            messages.error(request, 'You do not have access to this vaccination record.')
            return redirect('accounts:patient_portal')
        return redirect('vaccination:my_schedule_detail', pk=schedule.pk)

    cases = list(
        AnimalBiteCase.objects.filter(patient=patient)
        .select_related('patient')
        .order_by('-date_time_consultation', '-created_at')
    )
    selected_case = next(
        (c for c in cases if c.pk == schedule.bite_case_id), schedule.bite_case
    )
    case_param = request.GET.get('case', '').strip()
    if case_param:
        selected_case = next(
            (c for c in cases if str(c.pk) == case_param), selected_case
        )

    context = build_record_context(selected_case)
    context.update({'schedule': schedule, 'cases': cases})
    return render(request, 'vaccination/record_detail.html', context)


VACCINATION_MANAGER_ROLES = {'nurse', 'admin'}
ASSESSMENT_VIEW_ROLES = {'nurse', 'admin', 'doctor'}


def _may_manage_vaccination(user):
    return getattr(user, 'role', '') in VACCINATION_MANAGER_ROLES


@login_required
def assessment_detail(request, pk):
    """Doctor's saved assessment only — no patient, case, or vitals sections.

    Read-only by construction: this view renders stored data and never writes.
    """
    schedule = get_object_or_404(
        VaccinationSchedule.objects.select_related(
            'bite_case__patient__barangay', 'bite_case__patient__created_by',
            'bite_case__created_by', 'created_by',
        ),
        pk=pk,
    )
    role = getattr(request.user, 'role', '')
    if role == 'patient':
        messages.error(request, 'The doctor assessment is available to clinic staff only.')
        return redirect('accounts:patient_portal')
    if role not in ASSESSMENT_VIEW_ROLES:
        messages.error(request, 'Not authorized to view doctor assessments.')
        return redirect('dashboard:index')

    bite_case = schedule.bite_case
    context = build_record_context(bite_case)
    context.update({
        'schedule': schedule,
        'cases': [bite_case],
        'assessment_only': True,
    })
    return render(request, 'vaccination/record_detail.html', context)


def _dose_rows_for_management(schedule, assessment_is_final):
    ordinals = ('1st', '2nd', '3rd', '4th', '5th')
    rows = []
    for index, dose in enumerate(schedule.doses.all(), start=1):
        ordinal = ordinals[index - 1] if index <= len(ordinals) else f'{index}th'
        can_schedule = index > 1 and dose.dose_status not in ('completed', 'cancelled')
        can_administer = (
            assessment_is_final
            and dose.dose_status not in ('completed', 'cancelled')
        )
        rows.append({
            'dose': dose,
            'label': f'{ordinal} Dose',
            'expected_date': dose.expected_date(),
            'can_schedule': can_schedule,
            'can_administer': can_administer,
        })
    return rows


@login_required
def nurse_schedule_manage(request, pk):
    """Vaccine administration + prescribed medicine confirmation for one schedule."""
    from doctor.models import ClinicalAssessment

    if not _may_manage_vaccination(request.user):
        messages.error(request, 'Not authorized to manage vaccination records.')
        return redirect('dashboard:index')

    schedule = get_object_or_404(
        VaccinationSchedule.objects.select_related(
            'bite_case__patient__barangay', 'bite_case__created_by',
        ).prefetch_related('doses'),
        pk=pk,
    )
    bite_case = schedule.bite_case
    patient = bite_case.patient
    assessment = ClinicalAssessment.objects.select_related(
        'vaccine_inventory',
    ).filter(bite_case=bite_case).first()
    assessment_is_final = bool(assessment and assessment.is_finalized)
    prescriptions = []
    if assessment:
        prescriptions = list(
            assessment.prescriptions.select_related('medicine', 'dispensed_by').order_by('id')
        )

    doses = list(schedule.doses.all())
    completed = sum(1 for dose in doses if dose.dose_status == 'completed')
    total = len(doses)
    prescribed_vaccine = ''
    if assessment and assessment.vaccine_inventory_id:
        prescribed_vaccine = assessment.vaccine_inventory.vaccine_name
        if assessment.vaccine_inventory.brand:
            prescribed_vaccine = (
                f'{prescribed_vaccine} ({assessment.vaccine_inventory.brand})'
                if prescribed_vaccine else assessment.vaccine_inventory.brand
            )
    prescribed_vaccine = (
        prescribed_vaccine
        or (assessment.vaccine_brand_plan if assessment else '')
        or next((d.vaccine_brand for d in doses if d.vaccine_brand), '')
    )
    dose_schedule_label = ''
    if assessment and assessment.dose_protocol:
        dose_schedule_label = f"{len(ClinicalAssessment.protocol_days(assessment.dose_protocol))} Doses"
    elif total:
        dose_schedule_label = f'{total} Dose{"" if total == 1 else "s"}'
    product_name = ''
    if assessment:
        inventory_name = (
            assessment.vaccine_inventory.vaccine_name
            if assessment.vaccine_inventory_id
            else ''
        )
        product_name = f'{inventory_name} {assessment.vaccine_brand_plan}'
    prescribed_dose_count = _prescribed_dose_count(assessment, doses)
    rig_schedule_review = (
        prescribed_dose_count > 1
        and (
            'rabies immunoglobulin' in product_name.casefold()
            or product_name.strip().casefold() == 'rig'
        )
    )

    return render(request, 'vaccination/nurse_manage.html', {
        'schedule': schedule,
        'bite_case': bite_case,
        'patient': patient,
        'assessment': assessment,
        'assessment_is_final': assessment_is_final,
        'prescriptions': prescriptions,
        'dose_rows': _dose_rows_for_management(schedule, assessment_is_final),
        'total': total,
        'completed': completed,
        'progress': int((completed / total * 100) if total else 0),
        'prescribed_vaccine': prescribed_vaccine,
        'dose_schedule_label': dose_schedule_label,
        'rig_schedule_review': rig_schedule_review,
        'today': timezone.localdate(),
    })


def _ordinal_dose_label(number):
    suffix = 'th' if 10 <= number % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(number % 10, 'th')
    return f'{number}{suffix} Dose'


def _prescribed_dose_count(assessment, doses):
    """Doctor-selected dose count, falling back to the saved dose records."""
    from doctor.models import ClinicalAssessment
    count = 0
    if assessment:
        if assessment.dose_protocol:
            count = len(ClinicalAssessment.protocol_days(assessment.dose_protocol))
        elif assessment.dose_schedule_plan:
            match = re.search(r'(\d+)\s*doses?\b', assessment.dose_schedule_plan, re.I)
            if match:
                count = int(match.group(1))
    return count or len(doses)


def _prescribed_vaccine_name(assessment, doses):
    """Prescribed product as stored: inventory classification + brand, the
    doctor's plan text, or the brand recorded on a dose."""
    inventory = getattr(assessment, 'vaccine_inventory', None) if assessment else None
    if inventory:
        name = inventory.vaccine_name or ''
        if inventory.brand:
            name = f'{name} ({inventory.brand})' if name else inventory.brand
        if name:
            return name
    if assessment and assessment.vaccine_brand_plan:
        return assessment.vaccine_brand_plan
    return next((dose.vaccine_brand for dose in doses if dose.vaccine_brand), '') or 'Not recorded'


@login_required
def nurse_schedule_print(request, pk):
    """Read-only A4 appointment slip for one patient's vaccination schedule."""
    if not _may_manage_vaccination(request.user):
        messages.error(request, 'Not authorized to print vaccination schedules.')
        return redirect('dashboard:index')

    schedule = get_object_or_404(
        VaccinationSchedule.objects.select_related(
            'bite_case__patient__barangay', 'bite_case__created_by',
        ).prefetch_related('doses'),
        pk=pk,
    )
    bite_case = schedule.bite_case
    patient = bite_case.patient
    from doctor.models import ClinicalAssessment
    assessment = (
        ClinicalAssessment.objects
        .select_related('vaccine_inventory')
        .filter(bite_case=bite_case)
        .first()
    )
    doses = list(schedule.doses.all())
    dose_count = _prescribed_dose_count(assessment, doses)

    status_keys = {
        'Completed': 'completed',
        'Scheduled': 'scheduled',
        'Pending Scheduling': 'pending',
        'Missed': 'missed',
        'Cancelled': 'cancelled',
        'Rescheduled': 'rescheduled',
    }
    dose_rows = []
    for index in range(1, dose_count + 1):
        dose = doses[index - 1] if index - 1 < len(doses) else None
        if dose is None:
            status = 'Pending Scheduling'
        elif dose.actual_date:
            status = 'Completed'
        elif not dose.scheduled_date:
            status = (
                dose.get_dose_status_display()
                if dose.dose_status in ('missed', 'cancelled', 'rescheduled')
                else 'Pending Scheduling'
            )
        elif dose.dose_status in ('missed', 'cancelled', 'rescheduled'):
            status = dose.get_dose_status_display()
        else:
            status = 'Scheduled'
        dose_rows.append({
            'label': _ordinal_dose_label(index),
            'date': dose.scheduled_date if dose else None,
            'status': status,
            'status_key': status_keys.get(status, 'pending'),
        })

    first_dose_date = assessment.first_dose_date if assessment else None
    if first_dose_date is None:
        first_dose_date = next(
            (dose.clinical_date for dose in doses if dose.clinical_date), None)

    return render(request, 'vaccination/schedule_print.html', {
        'patient': patient,
        'bite_case': bite_case,
        'assessment': assessment,
        'schedule': schedule,
        'dose_rows': dose_rows,
        'dose_count': dose_count,
        'prescribed_vaccine': _prescribed_vaccine_name(assessment, doses),
        'first_dose_date': first_dose_date,
    })


def _send_schedule_set_reminder(dose, user):
    """Text the patient their dose date. Returns (log, reason); never raises.

    reason is 'missing_number' or 'failed' when no log was written.
    """
    from .sms import normalize_ph_number, send_dose_reminder

    patient = dose.schedule.bite_case.patient
    candidates = [
        patient.contact_number,
        getattr(patient, 'emergency_contact_number', ''),
        getattr(getattr(patient, 'account', None), 'mobile', '') if getattr(patient, 'account', None) else '',
    ]
    if not any(normalize_ph_number(value) for value in candidates if value):
        return None, 'missing_number'
    try:
        log, result = send_dose_reminder(dose, reminder_type='schedule_set')
    except Exception:
        logger.exception('Schedule reminder failed for dose %s', dose.pk)
        return None, 'failed'
    if not result.get('success'):
        return None, 'failed'
    AuditLog.objects.create(
        user=user, action='SEND_SMS',
        description=(
            f'Sent schedule reminder to {log.recipient} for {dose.dose_label} '
            f'on {dose.scheduled_date}'
        ),
    )
    return log, None


@login_required
@require_POST
def dose_schedule(request, pk):
    """Assign a date to one pending dose; never touches stock.

    Setting or changing the date also texts the patient their appointment.
    """
    _block_admin_dose_administration(request)
    if not _may_manage_vaccination(request.user):
        messages.error(request, 'Not authorized to schedule doses.')
        return redirect('dashboard:index')

    dose = get_object_or_404(
        VaccineDose.objects.select_related(
            'schedule__bite_case__patient',
            'schedule__bite_case__patient__account',
        ),
        pk=pk,
    )
    manage_url = redirect('vaccination:nurse_schedule_manage', pk=dose.schedule_id)

    first_dose = dose.schedule.doses.order_by('id').first()
    if first_dose and first_dose.pk == dose.pk:
        messages.error(request, "The 1st dose date is set by the doctor's finalized assessment and cannot be changed here.")
        return manage_url

    if dose.dose_status in ('completed', 'cancelled'):
        messages.error(request, 'A completed or cancelled dose cannot be rescheduled.')
        return manage_url

    raw_date = request.POST.get('scheduled_date', '').strip()
    try:
        scheduled_date = date.fromisoformat(raw_date)
    except ValueError:
        messages.error(request, 'Enter a valid scheduled date.')
        return manage_url

    minimum = prescribed_minimum_date(dose)
    if minimum and scheduled_date < minimum['date']:
        messages.error(
            request,
            "The doctor's finalized %s regimen places this dose on Day %d; "
            'the earliest date is %s.'
            % (
                minimum['protocol'],
                minimum['day'],
                minimum['date'].strftime('%b %d, %Y'),
            ),
        )
        return manage_url

    date_changed = dose.scheduled_date != scheduled_date
    dose.scheduled_date = scheduled_date
    if dose.dose_status in ('pending', 'missed', 'rescheduled'):
        dose.dose_status = 'scheduled'
    dose.save(update_fields=['scheduled_date', 'dose_status', 'updated_at'])
    AuditLog.objects.create(
        user=request.user, action='SCHEDULE_DOSE',
        description=(
            f'Scheduled {dose.dose_label} for {dose.schedule.bite_case.case_number} '
            f'on {scheduled_date}'
        ),
    )
    messages.success(
        request,
        'Vaccination schedule updated successfully.',
    )

    if date_changed:
        log, reason = _send_schedule_set_reminder(dose, request.user)
        if log:
            messages.info(request, f'SMS reminder sent to {log.recipient}.')
        elif reason == 'missing_number':
            messages.warning(
                request,
                'Schedule saved, but the patient has no valid mobile number for the SMS reminder.',
            )
        else:
            messages.warning(
                request,
                'Schedule saved, but the SMS reminder could not be sent.',
            )
    return manage_url


def _deduct_one_vial(dose):
    """Remove one vial from stock for a confirmed administration, if a lot matches."""
    from inventory.models import VaccineInventory

    brand = (dose.vaccine_brand or '').strip()
    if not brand and not dose.batch_number:
        return False

    usable = VaccineInventory.objects.select_for_update().filter(
        expiration_date__gt=timezone.localdate(), quantity_available__gt=0)

    inventory = None
    if dose.batch_number:
        # The lot on the dose is the exact stock item administered; the brand
        # recorded on the encounter is a display label and may not match the
        # inventory's brand field.
        inventory = usable.filter(
            batch_number__iexact=dose.batch_number).order_by('expiration_date').first()
    if not inventory and brand:
        lot = Q(brand__iexact=brand) | Q(vaccine_name__icontains=brand)
        inventory = usable.filter(lot).order_by('expiration_date').first()
    if not inventory:
        return False
    inventory.quantity_available -= 1
    inventory.quantity_used += 1
    inventory.save(update_fields=['quantity_available', 'quantity_used', 'updated_at'])
    return True


@login_required
@require_POST
def dose_early_administration_request(request, pk):
    """Record a pending review request without changing the dose or inventory."""
    _block_admin_dose_administration(request)
    if not _may_manage_vaccination(request.user):
        messages.error(request, 'Not authorized to request early vaccine administration.')
        return redirect('dashboard:index')

    dose = get_object_or_404(
        VaccineDose.objects.select_related(
            'schedule__bite_case__patient',
        ),
        pk=pk,
    )
    manage_url = redirect('vaccination:nurse_schedule_manage', pk=dose.schedule_id)
    reason = request.POST.get('reason', '').strip()
    if not reason:
        messages.error(request, 'A reason is required to request early administration.')
        return manage_url
    if len(reason) > 2000:
        messages.error(request, 'The reason must be 2000 characters or fewer.')
        return manage_url
    if dose.scheduled_date is None:
        messages.error(request, 'This dose must have a scheduled date before requesting early administration.')
        return manage_url
    if dose.dose_status in ('completed', 'cancelled') or dose.actual_date is not None:
        messages.error(request, 'An administered or cancelled dose cannot have an early administration request.')
        return manage_url

    today = timezone.localdate()
    if dose.scheduled_date <= today:
        messages.error(request, 'Early administration requests are only available for doses scheduled in the future.')
        return manage_url

    bite_case = dose.schedule.bite_case
    patient = bite_case.patient
    requested_at = timezone.localtime()
    AuditLog.objects.create(
        user=request.user,
        action='EARLY_ADMIN_REQUEST',
        description=(
            f'Pending authorized review: early administration requested for '
            f'{dose.dose_label} (dose ID {dose.pk}) for patient {patient.full_name()} '
            f'in case {bite_case.case_number}; scheduled {dose.scheduled_date}; '
            f'requested {requested_at.strftime("%Y-%m-%d %H:%M:%S %Z")}. '
            f'Reason: {reason}. No administration or inventory change was made.'
        ),
        target_type='bite_case',
        target_id=bite_case.pk,
        ip_address=request.META.get('REMOTE_ADDR'),
    )
    messages.success(
        request,
        'Your reason has been recorded. This vaccine dose cannot be administered before its scheduled date without the required authorization.',
    )
    return manage_url


@login_required
@require_POST
def dose_administer(request, pk):
    """Confirm actual administration: mark one dose Completed and deduct stock once."""
    _block_admin_dose_administration(request)
    if not _may_manage_vaccination(request.user):
        messages.error(request, 'Not authorized to record vaccine administration.')
        return redirect('dashboard:index')

    with transaction.atomic():
        dose = get_object_or_404(
            VaccineDose.objects.select_for_update().select_related(
                'schedule__bite_case__patient'
            ),
            pk=pk,
        )
        manage_url = redirect('vaccination:nurse_schedule_manage', pk=dose.schedule_id)
        bite_case = dose.schedule.bite_case

        from doctor.models import ClinicalAssessment
        assessment = ClinicalAssessment.objects.filter(bite_case=bite_case).first()
        if not assessment or not assessment.is_finalized:
            messages.error(
                request,
                'The doctor assessment must be finalized before a vaccine can be administered.',
            )
            return manage_url

        if dose.dose_status == 'completed' or dose.actual_date is not None:
            messages.info(request, 'This dose has already been recorded as completed.')
            return manage_url
        if dose.dose_status == 'cancelled':
            messages.error(request, 'A cancelled dose cannot be administered.')
            return manage_url
        if dose.scheduled_date is None:
            messages.error(request, 'A scheduled date is required before this dose can be administered.')
            return manage_url

        today = timezone.localdate()
        if dose.scheduled_date > today:
            messages.error(
                request,
                'This dose is scheduled for a future date and cannot be administered yet.',
            )
            return manage_url

        raw_date = request.POST.get('actual_date', '').strip()
        try:
            actual_date = date.fromisoformat(raw_date)
        except ValueError:
            messages.error(request, 'Enter a valid administration date.')
            return manage_url
        if actual_date > today:
            messages.error(request, 'Administration date cannot be in the future.')
            return manage_url
        if actual_date < dose.scheduled_date:
            messages.error(
                request,
                'Administration date cannot be earlier than the dose scheduled date.',
            )
            return manage_url

        actual_time = None
        raw_time = request.POST.get('actual_time', '').strip()
        if raw_time:
            try:
                actual_time = datetime.strptime(raw_time, '%H:%M').time()
            except ValueError:
                messages.error(request, 'Enter a valid administration time.')
                return manage_url

        brand = request.POST.get('vaccine_brand', '').strip() or assessment.vaccine_brand_plan
        batch = request.POST.get('batch_number', '').strip() or assessment.vaccine_lot_number
        dose.actual_date = actual_date
        dose.actual_time = actual_time
        dose.vaccine_brand = brand
        dose.batch_number = batch
        dose.dose_status = 'completed'
        dose.administered_by = request.user
        dose.save(update_fields=[
            'actual_date', 'actual_time', 'vaccine_brand', 'batch_number',
            'dose_status', 'administered_by', 'updated_at',
        ])
        deducted = _deduct_one_vial(dose)
        AuditLog.objects.create(
            user=request.user, action='ADMINISTER_VACCINE',
            description=(
                f'Administered {dose.dose_label} ({brand or "brand not recorded"}) '
                f'for {bite_case.case_number} on {actual_date}'
                + (f' at {actual_time.strftime("%H:%M")}' if actual_time else '')
                + ('; stock deducted' if deducted else '; no stock lot matched')
            ),
        )

    messages.success(
        request,
        f'{dose.dose_label} recorded as completed for {bite_case.patient.full_name()}.',
    )
    return manage_url
