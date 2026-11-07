from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.http import require_GET
from django.db.models import Q, Count
from django.core.paginator import Paginator
from django.utils import timezone
from django.db import transaction
from django.urls import reverse
from functools import wraps
from urllib.parse import quote
import datetime

from patients.models import Patient
from bite_cases.models import AnimalBiteCase, ExposureClassification
from inventory.models import MedicineInventory
from .models import ClinicalAssessment, DOSE_PROTOCOLS, Prescription
from .forms import ClinicalAssessmentForm, PrescriptionFormSet
from audit.models import AuditLog
from vaccination.models import VaccineDose, VaccinationSchedule
from vaccination.schedule_utils import (
    ensure_vaccination_schedule, schedule_base_date, sync_schedule_doses,
)
from django.db.models import Sum


def is_doctor_user(user):
    """RBAC: only users with role == 'doctor' may input/edit Clinical Assessments (Section 3).

    This is the single source of truth for Doctor access. Any other role —
    including 'admin', 'nurse', 'encoder', 'health_worker', 'patient' — is
    considered unauthorized here, even if the account is flagged as
    is_staff/is_superuser. Superuser status intentionally does NOT bypass.
    """
    return bool(
        user
        and user.is_authenticated
        and getattr(user, 'is_active', True)
        and getattr(user, 'role', '') == 'doctor'
    )


def doctor_required(view_func):
    """Restrict a view exclusively to authenticated users with the Doctor role.

    - Unauthenticated requests are sent to the login page (preserving next).
    - Any authenticated user whose role is NOT 'doctor' (e.g. Admin, Nurse) is
      explicitly denied and redirected to the dashboard with an error message.
    """

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            login_url = reverse('accounts:login')
            return redirect(f"{login_url}?next={quote(request.get_full_path())}")
        if not is_doctor_user(request.user):
            messages.error(request, 'Not authorized — Clinical Assessment (Section 3) is restricted to Doctor role.')
            return redirect('dashboard:index')
        return view_func(request, *args, **kwargs)
    return _wrapped


def dose_protocol_options():
    """Protocol data for the encounter form, including each regimen's dose days."""
    from vaccination.schedule_utils import get_dose_days
    default_days = get_dose_days()
    return [{'key': key, 'label': cfg['label'],
             'days': cfg['days'] if cfg['days'] is not None else default_days}
            for key, cfg in DOSE_PROTOCOLS.items()]


def prescription_medicine_meta():
    """Unit and stock hints for the prescription dropdown rows."""
    return {
        str(med.pk): {
            'unit': med.dose_unit(),
            'available': med.quantity_available,
            'stock_unit': med.unit,
            'form': med.get_dosage_form_display(),
        }
        for med in MedicineInventory.objects.filter(
            expiration_date__gt=timezone.localdate(), quantity_available__gt=0)
    }


def prescription_formset(data, assessment):
    queryset = (assessment.prescriptions.all() if assessment.pk
                else Prescription.objects.none())
    return PrescriptionFormSet(data, queryset=queryset, prefix='rx')


def save_prescriptions(formset, assessment, patient, bite_case):
    """Persist each row as its own record, then refresh the printed summary."""
    changed = formset.has_changed()
    saved = formset.save(commit=False)
    for prescription in saved:
        prescription.assessment = assessment
        prescription.patient = patient
        prescription.bite_case = bite_case
        prescription.save()
    for prescription in formset.deleted_objects:
        prescription.delete()

    # Leave pre-existing free-text prescriptions untouched until the doctor
    # actually changes the structured rows or the dosage instructions.
    if not (changed or assessment.prescriptions.exists() or assessment.dosage_instructions):
        return
    summary = '; '.join(
        p.summary for p in assessment.prescriptions.select_related('medicine').order_by('id'))
    instructions = (assessment.dosage_instructions or '').strip()
    if instructions:
        summary = f'{summary} — Instructions: {instructions}' if summary else instructions
    if summary != assessment.prescription:
        ClinicalAssessment.objects.filter(pk=assessment.pk).update(prescription=summary)
        assessment.prescription = summary


def sync_planned_vaccine(obj):
    """Keep the printed brand/lot in step with the inventory lot chosen above."""
    if not obj.vaccine_inventory_id:
        return
    lot = obj.vaccine_inventory
    obj.vaccine_brand_plan = f'{lot.vaccine_name} ({lot.brand})'.strip()
    obj.vaccine_lot_number = lot.batch_number


FINALIZATION_ATTESTATION = (
    'I attest that I personally reviewed this clinical assessment, that its findings '
    'and treatment plan are complete and clinically accurate, and that I approve the '
    'recorded vaccine, dose schedule, and prescribed medicines for administration '
    'and dispensing.'
)


def finalization_blockers(cleaned, bite_case=None):
    """Clinical reasons a completed encounter cannot be signed yet.

    Saving is still allowed so the doctor keeps their work, but the record stays
    Draft and no treatment order is released to the nurse.
    """
    def text(key):
        value = cleaned.get(key)
        return value.strip() if isinstance(value, str) else value

    blockers = []
    if not text('chief_complaint'):
        blockers.append('Chief complaint is required.')
    if not text('exposure_narrative'):
        blockers.append('Verified exposure narrative is required.')
    if not cleaned.get('category_confirmed'):
        blockers.append('WHO bite category must be confirmed.')
    if not text('treatment_plan'):
        blockers.append('Treatment plan is required.')
    # A bite case with a dose schedule is a PEP order even though the PEP flag
    # itself is not exposed in the encounter UI.
    if cleaned.get('pep_indicated') or (bite_case and cleaned.get('dose_schedule')):
        if not (cleaned.get('vaccine_inventory')
                or text('vaccine_brand_plan') or text('vaccine_lot_number')):
            blockers.append('Select the vaccine lot or record the vaccine brand for the PEP schedule.')
        if not (cleaned.get('dose_schedule_date') or cleaned.get('first_dose_date')):
            blockers.append('The 1st dose date is required to schedule the PEP doses.')
        if not cleaned.get('dose_schedule'):
            blockers.append('Select how many doses the protocol requires.')
    return blockers


def save_encounter(request, form, rx_formset, assessment, patient, bite_case):
    """Save an encounter; only a complete, consistent one is finalized.

    Returns (assessment, schedule, schedule_created). A previously signed
    assessment is never downgraded or overwritten with incomplete data.
    """
    blockers = finalization_blockers(form.cleaned_data, bite_case)
    if blockers and assessment.pk and assessment.is_finalized:
        for blocker in blockers:
            messages.error(request, blocker)
        messages.error(
            request,
            'The signed assessment was not changed. Complete the required fields before saving again.',
        )
        return assessment, None, False

    finalized = not blockers
    with transaction.atomic():
        obj = form.save(commit=False)
        obj.patient = patient
        obj.bite_case = bite_case
        obj.assessed_by = request.user
        obj.status = 'final' if finalized else 'draft'
        obj.is_finalized = finalized
        if finalized:
            obj.finalized_at = timezone.now()
            obj.attestation = FINALIZATION_ATTESTATION
        else:
            obj.finalized_at = None
            obj.attestation = ''
        sync_planned_vaccine(obj)
        dose_count = form.cleaned_data.get('dose_schedule')
        dose_date = form.cleaned_data.get('dose_schedule_date')
        if dose_date:
            # The confirmed 1st Dose date is the schedule's Day 0.
            obj.first_dose_date = dose_date
        obj.save()
        if rx_formset.is_bound:
            save_prescriptions(rx_formset, obj, patient, bite_case)

        schedule = None
        schedule_created = False
        if bite_case:
            # Every encounter keeps its dose records: the doctor's dose count
            # decides how many rows exist, only the 1st dose is dated, and the
            # nurse schedules the rest. Stock is never deducted here.
            days = obj.protocol_days(obj.dose_protocol) if obj.dose_protocol else None
            first_date = obj.first_dose_date or schedule_base_date(bite_case)
            schedule, schedule_created = ensure_vaccination_schedule(
                bite_case, created_by=request.user, days=days,
                base_date=first_date, dose_count=dose_count,
            )
            sync_schedule_doses(schedule, dose_count, first_date, days=days)

            if obj.category_confirmed:
                exp, _ = ExposureClassification.objects.get_or_create(
                    bite_case=bite_case,
                    defaults={'category': obj.category_confirmed, 'classified_by': request.user},
                )
                if exp.category != obj.category_confirmed:
                    exp.category = obj.category_confirmed
                    exp.classified_by = request.user
                    exp.save()
                if obj.treatment_plan:
                    exp.treatment_plan = obj.treatment_plan
                    exp.save()

            if finalized and bite_case.case_status == 'new':
                bite_case.case_status = 'under_treatment'
                bite_case.save(update_fields=['case_status'])

        case_suffix = f' case {bite_case.case_number}' if bite_case else ''
        AuditLog.objects.create(
            user=request.user, action='DOCTOR_ASSESSMENT',
            description=(
                f'Doctor assessment for {patient.patient_number}{case_suffix} - '
                f'{obj.get_category_confirmed_display() if obj.category_confirmed else "draft"} '
                f'({"finalized" if finalized else "draft"})'
            ),
        )
        if finalized:
            finalized_at = timezone.localtime(obj.finalized_at).strftime('%Y-%m-%d %H:%M:%S')
            AuditLog.objects.create(
                user=request.user, action='DOCTOR_ASSESSMENT_FINALIZED',
                description=(
                    f'Assessment #{obj.pk} finalized by user {request.user.pk} '
                    f'({request.user.username}) on {finalized_at}. '
                    f'Attestation: {FINALIZATION_ATTESTATION}'
                ),
                target_type='bite_case' if bite_case else '',
                target_id=bite_case.pk if bite_case else None,
            )

    if blockers:
        for blocker in blockers:
            messages.warning(request, blocker)
        messages.warning(
            request,
            'Saved as Draft — treatment orders stay locked until the assessment is complete and finalized.',
        )
    else:
        messages.success(
            request,
            f'Clinical assessment finalized for {patient.full_name()}'
            f'{f" ({bite_case.case_number})" if bite_case else ""}. '
            'The nurse can now administer the approved vaccine and dispense prescribed medicines.',
        )
    return obj, schedule, schedule_created


@login_required
@doctor_required
def dashboard(request):
    """Personal dashboard — the doctor only sees/manages their own clinical work."""
    today = timezone.localdate()
    me = request.user
    my_qs = ClinicalAssessment.objects.filter(assessed_by=me)

    my_total = my_qs.count()
    draft_assess = my_qs.filter(status='draft').count()
    # Compare against an explicit local-day range: MariaDB timezone tables may be
    # missing, which makes __date lookups silently return nothing.
    tz = timezone.get_current_timezone()
    today_start = timezone.make_aware(datetime.datetime.combine(today, datetime.time.min), tz)
    final_today = my_qs.filter(
        updated_at__gte=today_start,
        updated_at__lt=today_start + datetime.timedelta(days=1),
        status='final',
    ).count()
    pending_assess = AnimalBiteCase.objects.filter(clinical_assessment__isnull=True).count()

    # WHO category donuts — my confirmed categories only
    category_meta = [
        ('category_i', 'Category I', 'Licks on intact skin', '#2e9975'),
        ('category_ii', 'Category II', 'Minor scratches, no bleeding', '#d97706'),
        ('category_iii', 'Category III', 'Transdermal bites or scratches', '#dc2626'),
    ]
    cat_map = {key: 0 for key, _, _, _ in category_meta}
    for row in (my_qs.exclude(category_confirmed='')
                .values('category_confirmed').annotate(c=Count('id'))):
        if row['category_confirmed'] in cat_map:
            cat_map[row['category_confirmed']] = row['c']
    categorized_total = sum(cat_map.values())
    who_categories = [{
        'key': key,
        'label': label,
        'description': description,
        'color': color,
        'count': cat_map[key],
        'pct': round(cat_map[key] * 100 / categorized_total) if categorized_total else 0,
    } for key, label, description, color in category_meta]

    # Monthly bite-case volume for a chosen year (clinic-wide counts, read-only data).
    # Bucket in Python so the chart does not depend on DB timezone conversion.
    years = sorted({
        timezone.localtime(created).year
        for created in AnimalBiteCase.objects.values_list('created_at', flat=True)
        if created
    }, reverse=True)
    if today.year not in years:
        years.insert(0, today.year)
    try:
        selected_year = int(request.GET.get('year', today.year))
    except (TypeError, ValueError):
        selected_year = today.year
    if selected_year not in years:
        years = sorted(set(years) | {selected_year}, reverse=True)

    monthly_counts = {month: 0 for month in range(1, 13)}
    year_start = timezone.make_aware(datetime.datetime(selected_year, 1, 1), tz)
    year_end = timezone.make_aware(datetime.datetime(selected_year + 1, 1, 1), tz)
    for created in (AnimalBiteCase.objects
                    .filter(created_at__gte=year_start, created_at__lt=year_end)
                    .values_list('created_at', flat=True)):
        monthly_counts[timezone.localtime(created).month] += 1
    month_names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                   'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    monthly_series = [{'month': month, 'label': month_names[month - 1], 'count': monthly_counts[month]}
                      for month in range(1, 13)]
    monthly_total = sum(monthly_counts.values())

    # Optional month drill-down from the chart filter
    selected_month = None
    try:
        value = int(request.GET.get('month', ''))
        if 1 <= value <= 12:
            selected_month = value
    except (TypeError, ValueError):
        pass

    # My queue: unclaimed cases + my own assessments (other doctors' work stays hidden)
    queue_qs = AnimalBiteCase.objects.filter(
        Q(clinical_assessment__isnull=True) | Q(clinical_assessment__assessed_by=me)
    ).select_related('patient', 'exposure').order_by('-created_at')
    if selected_month:
        month_start = timezone.make_aware(
            datetime.datetime(selected_year, selected_month, 1), tz)
        if selected_month == 12:
            month_end = timezone.make_aware(datetime.datetime(selected_year + 1, 1, 1), tz)
        else:
            month_end = timezone.make_aware(
                datetime.datetime(selected_year, selected_month + 1, 1), tz)
        queue_qs = queue_qs.filter(created_at__gte=month_start, created_at__lt=month_end)
    queue = list(queue_qs[:8])

    for case in queue:
        try:
            assessment = case.clinical_assessment
        except ClinicalAssessment.DoesNotExist:
            assessment = None
        case.needs_assess = assessment is None or assessment.status == 'draft'
        case._cached_assessment = assessment
        case.assessment_status = ('pending' if assessment is None
                                  else 'for_assessment' if assessment.status == 'draft'
                                  else 'completed')
        if assessment is not None and assessment.category_confirmed:
            case.who_category = assessment.category_confirmed
        else:
            try:
                case.who_category = case.exposure.category
            except ExposureClassification.DoesNotExist:
                case.who_category = ''

    recent_assessments = my_qs.select_related(
        'patient', 'bite_case', 'assessed_by').order_by('-updated_at')[:8]

    context = {
        'today': today,
        'my_total': my_total,
        'pending_assess': pending_assess,
        'draft_assess': draft_assess,
        'final_today': final_today,
        'who_categories': who_categories,
        'categorized_total': categorized_total,
        'monthly_series': monthly_series,
        'monthly_total': monthly_total,
        'selected_year': selected_year,
        'selected_month': selected_month,
        'selected_month_label': month_names[selected_month - 1] if selected_month else '',
        'year_options': years,
        'queue': queue,
        'recent_assessments': recent_assessments,
    }
    return render(request, 'doctor/dashboard.html', context)



@login_required
@doctor_required
def queue_list(request):
    """My queue only: pending (unclaimed) + my own assessments. Hides other doctors' work."""
    q = request.GET.get('q', '')
    status = request.GET.get('status', '')  # pending, draft, final, all
    category = request.GET.get('category', '')
    # Own-usage scope: unclaimed OR mine
    cases = AnimalBiteCase.objects.filter(
        Q(clinical_assessment__isnull=True) | Q(clinical_assessment__assessed_by=request.user)
    ).select_related('patient')
    if q:
        cases = cases.filter(
            Q(case_number__icontains=q) |
            Q(patient__first_name__icontains=q) |
            Q(patient__last_name__icontains=q) |
            Q(patient__patient_number__icontains=q)
        )
    if category:
        cases = cases.filter(clinical_assessment__category_confirmed=category)
    # status filter
    if status == 'pending':
        cases = cases.filter(clinical_assessment__isnull=True)
    elif status == 'draft':
        cases = cases.filter(clinical_assessment__status='draft')
    elif status == 'final':
        cases = cases.filter(clinical_assessment__status='final')
    # ordering
    cases = cases.order_by('-created_at')
    paginator = Paginator(cases, 20)
    page = paginator.get_page(request.GET.get('page'))

    # annotate needs — safe handling of reverse OneToOne
    for c in page:
        try:
            ca = c.clinical_assessment
        except ClinicalAssessment.DoesNotExist:
            ca = None
        c.has_assess = ca is not None
        c.assess_status = ca.status if ca else 'pending'
        c._cached_assessment = ca

    return render(request, 'doctor/queue.html', {
        'cases': page,
        'q': q,
        'status': status,
        'category': category,
    })


@login_required
@doctor_required
def assessment_list(request):
    """My assessments only."""
    qs = ClinicalAssessment.objects.filter(assessed_by=request.user).select_related('patient', 'bite_case', 'assessed_by').order_by('-updated_at')
    q = request.GET.get('q','')
    if q:
        qs = qs.filter(
            Q(patient__first_name__icontains=q) |
            Q(patient__last_name__icontains=q) |
            Q(patient__patient_number__icontains=q) |
            Q(bite_case__case_number__icontains=q)
        )
    paginator = Paginator(qs, 20)
    page = paginator.get_page(request.GET.get('page'))
    return render(request, 'doctor/assessment_list.html', {'assessments': page, 'q': q})


@login_required
@doctor_required
@require_GET
def assessment_detail(request, pk):
    """Read-only view of one of the doctor's own signed/draft assessments.

    This page never writes: the schedule and prescriptions are shown exactly
    as stored, and inventory stock is untouched.
    """
    assessment = get_object_or_404(
        ClinicalAssessment.objects.select_related(
            'patient__barangay', 'patient__created_by', 'bite_case',
            'assessed_by', 'vaccine_inventory',
        ),
        pk=pk, assessed_by=request.user,
    )
    patient = assessment.patient
    bite_case = assessment.bite_case

    schedule = None
    doses = []
    if bite_case:
        schedule = getattr(bite_case, 'vaccination_schedule', None)
        if schedule:
            doses = list(schedule.doses.all())

    first_dose_date = doses[0].scheduled_date if doses else None
    if first_dose_date is None and doses:
        first_dose_date = doses[0].actual_date
    if first_dose_date is None:
        first_dose_date = assessment.first_dose_date

    # Show the total the doctor prescribed, not the hidden protocol label.
    dose_count = len(doses)
    if not dose_count and assessment.dose_protocol:
        dose_count = len(ClinicalAssessment.protocol_days(assessment.dose_protocol))
    if dose_count:
        dose_schedule_label = f'{dose_count} Dose{"" if dose_count == 1 else "s"}'
    elif assessment.dose_schedule_plan:
        dose_schedule_label = assessment.dose_schedule_plan
    else:
        dose_schedule_label = ''

    return render(request, 'doctor/assessment_detail.html', {
        'assessment': assessment,
        'patient': patient,
        'bite_case': bite_case,
        'latest_vitals': patient.get_latest_vitals(),
        'schedule': schedule,
        'doses': doses,
        'first_dose_date': first_dose_date,
        'dose_schedule_label': dose_schedule_label,
        'prescriptions': assessment.prescriptions.prefetch_related('medicine').order_by('id'),
    })


@login_required
@doctor_required
def assess_case(request, pk):
    """
    Unified Doctor Encounter: verify the case and conduct the clinical assessment.
    Patient demographics and vital signs are read-only here — only administrators
    and clinic staff maintain them through their own interfaces.
    """
    bite_case = get_object_or_404(AnimalBiteCase.objects.select_related('patient__barangay', 'patient__account'), pk=pk)
    patient = bite_case.patient

    # Own-usage guard: if this case is already assessed by another doctor, block takeover
    existing = ClinicalAssessment.objects.select_related('assessed_by').filter(bite_case=bite_case).first()
    if existing and existing.assessed_by_id and existing.assessed_by_id != request.user.id:
        owner = existing.assessed_by.get_full_name() or existing.assessed_by.username if existing.assessed_by else 'another doctor'
        messages.error(request, f'This case is already handled by {owner} — you can only manage your own assessments.')
        return redirect('doctor:queue')

    # Get or prepare assessment (mine or new)
    assessment = existing
    # Do not auto-create on GET; create blank for form initial
    if assessment is None:
        assessment = ClinicalAssessment(patient=patient, bite_case=bite_case, assessed_by=request.user)

    latest_vitals = patient.get_latest_vitals()

    if request.method == 'POST':
        form = ClinicalAssessmentForm(request.POST, instance=assessment, prefix='assess')
        rx_formset = prescription_formset(
            request.POST if 'rx-TOTAL_FORMS' in request.POST else None, assessment)
        form_valid = form.is_valid()
        rx_valid = rx_formset.is_valid() if rx_formset.is_bound else True
        if form_valid and rx_valid:
            obj, schedule, schedule_created = save_encounter(
                request, form, rx_formset, assessment, patient, bite_case)
            if schedule_created:
                first_date = obj.first_dose_date or schedule_base_date(bite_case)
                messages.success(
                    request,
                    '%d dose records created for %s — 1st Dose on %s; the remaining doses are Pending Scheduling.'
                    % (schedule.doses.count(), bite_case.case_number,
                       first_date.strftime('%b %d, %Y')),
                )
            if rx_formset.is_bound:
                rx_formset = prescription_formset(None, obj)
            assessment = obj
        else:
            messages.error(request, 'Assessment form has errors - check required fields.')
    else:
        # Prefill assessment from bite_case if new
        if assessment.pk is None:
            assessment.body_part_verified = bite_case.body_part_affected
            assessment.number_of_wounds_verified = bite_case.number_of_wounds
            assessment.date_time_bite_verified = bite_case.date_time_bite
            assessment.wound_washed_verified = bite_case.wound_washed_immediately
            assessment.animal_type_verified = bite_case.animal_type
            assessment.animal_owned_verified = bite_case.animal_ownership
            assessment.animal_vax_verified = bite_case.animal_vax_status
            # Try to prefill category from existing exposure
            try:
                if hasattr(bite_case, 'exposure') and bite_case.exposure:
                    assessment.category_confirmed = bite_case.exposure.category
            except:
                pass
        form = ClinicalAssessmentForm(instance=assessment, prefix='assess')
        rx_formset = prescription_formset(None, assessment)

    try:
        schedule = bite_case.vaccination_schedule
    except VaccinationSchedule.DoesNotExist:
        schedule = None
    # Vital signs for context
    recent_vitals = list(patient.vital_signs.select_related('taken_by').order_by('-taken_at')[:3])
    # History: my assessments for this patient only (own usage)
    history = ClinicalAssessment.objects.filter(patient=patient, assessed_by=request.user).select_related('bite_case', 'assessed_by').order_by('-encounter_date')[:5]

    return render(request, 'doctor/assess_form.html', {
        'patient': patient,
        'bite_case': bite_case,
        'form': form,
        'assessment': assessment,
        'latest_vitals': latest_vitals,
        'recent_vitals': recent_vitals,
        'schedule': schedule,
        'dose_protocols': dose_protocol_options(),
        'history': history,
        'rx_formset': rx_formset,
        'rx_medicine_meta': prescription_medicine_meta(),
        'is_new': assessment.pk is None,
    })


@login_required
@doctor_required
def assess_patient(request, pk):
    """
    Entry point when doctor wants to assess a patient without specific case id - picks latest case or creates generic encounter.
    """
    patient = get_object_or_404(Patient.objects.select_related('barangay'), pk=pk)
    latest_case = patient.bite_cases.order_by('-created_at').first()
    if latest_case:
        return redirect('doctor:assess_case', pk=latest_case.pk)
    # No case yet - create a general assessment without bite_case (doctor will fill exposure)
    # Own-usage: only my general assessment
    assessment = ClinicalAssessment.objects.filter(patient=patient, bite_case__isnull=True, assessed_by=request.user).order_by('-created_at').first()
    if assessment is None:
        # If another doctor owns a general assessment for this patient, block
        other = ClinicalAssessment.objects.filter(patient=patient, bite_case__isnull=True).exclude(assessed_by=request.user).select_related('assessed_by').first()
        if other and other.assessed_by_id:
            owner = other.assessed_by.get_full_name() or other.assessed_by.username
            messages.error(request, f'This patient is already handled by {owner} — you can only manage your own assessments.')
            return redirect('doctor:queue')
        assessment = ClinicalAssessment(patient=patient, bite_case=None, assessed_by=request.user)

    latest_vitals = patient.get_latest_vitals()

    if request.method == 'POST':
        form = ClinicalAssessmentForm(request.POST, instance=assessment, prefix='assess')
        rx_formset = prescription_formset(
            request.POST if 'rx-TOTAL_FORMS' in request.POST else None, assessment)
        form_valid = form.is_valid()
        rx_valid = rx_formset.is_valid() if rx_formset.is_bound else True
        if form_valid and rx_valid:
            obj, _, _ = save_encounter(
                request, form, rx_formset, assessment, patient, None)
            if rx_formset.is_bound:
                rx_formset = prescription_formset(None, obj)
            assessment = obj
        else:
            messages.error(request, 'Assessment form has errors - check required fields.')
    else:
        form = ClinicalAssessmentForm(instance=assessment, prefix='assess')
        rx_formset = prescription_formset(None, assessment)

    return render(request, 'doctor/assess_form.html', {
        'patient': patient,
        'bite_case': None,
        'form': form,
        'assessment': assessment,
        'latest_vitals': latest_vitals,
        'recent_vitals': list(patient.vital_signs.order_by('-taken_at')[:3]),
        'schedule': None,
        'dose_protocols': dose_protocol_options(),
        'history': ClinicalAssessment.objects.filter(patient=patient, assessed_by=request.user).order_by('-encounter_date')[:5],
        'rx_formset': rx_formset,
        'rx_medicine_meta': prescription_medicine_meta(),
        'is_new': assessment.pk is None,
    })
