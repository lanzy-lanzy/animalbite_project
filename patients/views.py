from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef, Q
from django.http import HttpResponse
from django.urls import reverse
from django.views.decorators.http import require_POST
from datetime import datetime
import io
import re
from urllib.parse import urlparse
from .models import Patient, Barangay, VitalSign
from .forms import PatientForm, VitalSignForm
from .services import (
    PatientDeletionBlocked,
    delete_patient_records,
    deletion_summary_text,
    patient_record_summary,
    protected_record_labels,
)
from audit.models import AuditLog
from utils.redirects import redirect_back

# Case statuses considered "active / being treated" for the doctor Live view.
DOCTOR_ACTIVE_CASE_STATUSES = ('new', 'under_treatment')


def _doctor_owned_or_unclaimed_q(user):
    """Q limiting a Patient queryset to a doctor's own patients plus patients
    that no doctor has claimed yet.

    A patient becomes 'claimed' the moment any doctor creates a ClinicalAssessment
    for them. Until then they are open for every doctor to pick up — this includes
    walk-in patients registered directly by an admin/staff, who usually have no
    bite case yet and were previously invisible to every doctor."""
    from doctor.models import ClinicalAssessment
    own_ids = ClinicalAssessment.objects.filter(assessed_by=user).values_list('patient_id', flat=True)
    claimed_by_others = (
        ClinicalAssessment.objects
        .exclude(assessed_by=user)
        .filter(assessed_by__isnull=False)
        .values_list('patient_id', flat=True)
    )
    return Q(pk__in=own_ids) | ~Q(pk__in=claimed_by_others)


def _doctor_can_access_patient(user, patient):
    """Ownership check: a doctor may open a patient unless another doctor has
    already claimed them. Unclaimed patients — including walk-in records created
    directly by an admin that have no bite case or assessment yet — are open to
    every doctor. Handover is performed by an admin reassigning
    ClinicalAssessment.assessed_by."""
    from doctor.models import ClinicalAssessment
    if ClinicalAssessment.objects.filter(patient=patient, assessed_by=user).exists():
        return True
    claimed_by_others = (
        ClinicalAssessment.objects
        .filter(patient=patient)
        .exclude(assessed_by=user)
        .filter(assessed_by__isnull=False)
        .exists()
    )
    return not claimed_by_others


def generate_patient_number():
    last = Patient.objects.filter(patient_number__startswith='PAT-').order_by('-id').first()
    if last:
        num = int(last.patient_number.split('-')[1]) + 1
    else:
        num = 1
    return f"PAT-{num:05d}"

@login_required
def patient_list(request):
    query = request.GET.get('q', '')
    barangay = request.GET.get('barangay', '')
    patients_qs = Patient.objects.filter(is_archived=False)

    if query:
        from utils.phone import normalize_to_63
        # Match both the raw query and the 63... Semaphore equivalent for stored numbers
        query_63 = normalize_to_63(query) or query
        patients_qs = patients_qs.filter(
            Q(first_name__icontains=query) | Q(middle_name__icontains=query) |
            Q(last_name__icontains=query) | Q(patient_number__icontains=query) |
            Q(contact_number__icontains=query) | Q(contact_number__icontains=query_63) |
            Q(address__icontains=query) | Q(barangay__name__icontains=query)
        )
    if barangay:
        patients_qs = patients_qs.filter(barangay_id=barangay)

    # Access control (RBAC): doctors only see their own patients + unassigned cases.
    is_doctor = getattr(request.user, 'role', '') == 'doctor'
    if is_doctor:
        patients_qs = patients_qs.filter(_doctor_owned_or_unclaimed_q(request.user))

    # Counts for the delete confirmation dialog, plus the retention flags that
    # make permanent deletion illegal (dispensed meds, given doses, signed notes).
    from doctor.models import ClinicalAssessment, Prescription
    from vaccination.models import VaccineDose
    patients_qs = patients_qs.annotate(
        bite_case_count=Count('bite_cases', distinct=True),
        assessment_count=Count('clinical_assessments', distinct=True),
        vaccination_count=Count('bite_cases__vaccination_schedule', distinct=True),
        has_dispensed_prescription=Exists(
            Prescription.objects.filter(patient_id=OuterRef('pk')).filter(
                Q(status='dispensed') | Q(dispensed_at__isnull=False))
        ),
        has_administered_dose=Exists(
            VaccineDose.objects.filter(schedule__bite_case__patient_id=OuterRef('pk')).filter(
                Q(actual_date__isnull=False) | Q(dose_status='completed'))
        ),
        has_signed_assessment=Exists(
            ClinicalAssessment.objects.filter(patient_id=OuterRef('pk')).filter(
                Q(status='final') | Q(is_finalized=True))
        ),
    )

    paginator = Paginator(patients_qs.order_by('-created_at'), 20)
    page = paginator.get_page(request.GET.get('page'))
    barangays = Barangay.objects.filter(is_active=True)

    # "View on Live": attach the patient's current active, claimable case for doctors.
    if is_doctor:
        from bite_cases.models import AnimalBiteCase
        page_ids = [p.pk for p in page]
        # Patients whose current status is active / under treatment (any active case).
        active_patient_ids = set(
            AnimalBiteCase.objects.filter(
                patient_id__in=page_ids,
                case_status__in=DOCTOR_ACTIVE_CASE_STATUSES,
            ).values_list('patient_id', flat=True)
        )
        # Latest active, claimable case per patient (unassigned or already this doctor's).
        live_cases = AnimalBiteCase.objects.filter(
            patient_id__in=page_ids,
            case_status__in=DOCTOR_ACTIVE_CASE_STATUSES,
        ).filter(
            Q(clinical_assessment__isnull=True) | Q(clinical_assessment__assessed_by=request.user)
        ).order_by('patient_id', '-created_at')
        live_map = {}
        for c in live_cases:
            live_map.setdefault(c.patient_id, c.pk)
        for p in page:
            p.has_active_case = p.pk in active_patient_ids
            p.live_case_pk = live_map.get(p.pk)

    return render(request, 'patients/patient_list.html', {
        'patients': page, 'barangays': barangays, 'query': query,
        'selected_barangay': barangay,
    })

@login_required
def patient_create(request):
    if getattr(request.user, 'role', '') == 'patient':
        messages.error(request, 'Not authorized to register patients.')
        return redirect('patients:patient_list')
    if request.method == 'POST':
        form = PatientForm(request.POST, request.FILES)
        if form.is_valid():
            patient = form.save(commit=False)
            patient.patient_number = generate_patient_number()
            patient.created_by = request.user
            patient.save()
            AuditLog.objects.create(user=request.user, action='CREATE_PATIENT', description=f'Created patient {patient.patient_number} - {patient.full_name()}')
            # Auto-create patient login credentials
            temp_password = None
            username = None
            try:
                from utils.credentials import create_patient_user
                user, temp_password, created = create_patient_user(patient, created_by=request.user)
                username = user.username
                if created:
                    # Store in session for one-time display on detail page
                    request.session[f'temp_creds_{patient.pk}'] = {
                        'username': username,
                        'password': temp_password,
                        'created': True,
                    }
                    # Also try to send the portal credentials by SMS.
                    try:
                        from vaccination.sms import send_semaphore_sms
                        from django.conf import settings
                        login_url = request.build_absolute_uri(reverse('accounts:login'))
                        sms_msg = (
                            f"ABTC Dumingag: Hello {patient.first_name}, your patient portal is ready. "
                            f"Username: {username} Temp Password: {temp_password}. "
                            f"Login at {login_url} and change password via Profile > Change Password. -ABTC Dumingag"
                        )
                        # patient.contact_number is stored in international 63... format
                        sms_res = send_semaphore_sms(patient.contact_number, sms_msg)
                        if sms_res.get('success'):
                            messages.info(request, f'SMS with login sent to {patient.contact_display}.')
                        else:
                            # Fallback still show credentials; SMS failure is not critical
                            if not settings.SEMAPHORE_ENABLED:
                                messages.info(request, f'SMS login details would be sent to {patient.contact_number} (SMS is disabled).')
                    except Exception:
                        pass
                    messages.success(request, f'Patient {patient.patient_number} created. Login: {username} / Temp: {temp_password} — patient should change password after first login.')
                else:
                    messages.success(request, f'Patient {patient.patient_number} created successfully (login already exists: {username}).')
            except Exception as e:
                messages.success(request, f'Patient {patient.patient_number} created successfully.')
                messages.warning(request, f'Auto-login creation failed: {e}. Create manually via Users.')
            return redirect('patients:patient_detail', pk=patient.pk)
    else:
        form = PatientForm()
    return render(request, 'patients/patient_form.html', {'form': form, 'title': 'Register New Patient'})

@login_required
def patient_detail(request, pk):
    patient = get_object_or_404(Patient.objects.select_related('account', 'barangay'), pk=pk)
    # Access control (RBAC): doctors may only open their own patients or unassigned cases.
    if getattr(request.user, 'role', '') == 'doctor' and not _doctor_can_access_patient(request.user, patient):
        messages.error(request, 'You do not have access to this patient record — you can only view your own patients or unassigned cases.')
        return redirect('patients:patient_list')
    bite_cases = patient.bite_cases.select_related().prefetch_related('vaccination_schedule__doses').order_by('-created_at') if hasattr(patient, 'bite_cases') else []
    # Patient portal account details and credential management are restricted
    # to the System Administrator. Other roles receive no portal data at all.
    can_manage_portal = getattr(request.user, 'role', '') == 'admin'
    temp_creds = None
    portal_login_url = None
    portal_qr_data_uri = None
    if can_manage_portal:
        # One-time temp credentials from session (set after auto-creation)
        temp_creds = request.session.pop(f'temp_creds_{patient.pk}', None)
        # Also check if there's a recent reset stored generically
        if not temp_creds:
            temp_creds = request.session.pop('temp_creds_last', None)
            if temp_creds and str(temp_creds.get('patient_pk')) != str(patient.pk):
                temp_creds = None
        # Persistent login QR carries only the portal URL — never a password or
        # other reusable secret — so it stays safe to display, download and reprint.
        portal_login_url = request.build_absolute_uri(reverse('accounts:login'))
        try:
            from utils.qr import qr_data_uri
            portal_qr_data_uri = qr_data_uri(portal_login_url)
        except Exception:
            portal_qr_data_uri = None
    else:
        # Never leak administrator-only credential material through another
        # user's session.
        request.session.pop(f'temp_creds_{patient.pk}', None)
        last_creds = request.session.get('temp_creds_last')
        if last_creds and str(last_creds.get('patient_pk')) == str(patient.pk):
            request.session.pop('temp_creds_last', None)
    # Vital signs — latest and recent for admin dynamic editing
    latest_vitals = patient.get_latest_vitals() if hasattr(patient, 'get_latest_vitals') else None
    recent_vitals = list(patient.vital_signs.select_related('taken_by').order_by('-taken_at')[:5]) if hasattr(patient, 'vital_signs') else []
    # Clinical Assessments (Section 3) — latest + history for doctor edit/update on this record
    latest_assessment = None
    clinical_by_case = {}
    assessments = []
    try:
        from doctor.models import ClinicalAssessment
        assessments = list(
            ClinicalAssessment.objects
            .select_related('assessed_by', 'bite_case', 'bite_case__vaccination_schedule', 'vaccine_inventory')
            .prefetch_related('prescriptions__medicine', 'bite_case__vaccination_schedule__doses')
            .filter(patient=patient)
            .order_by('-encounter_date', '-updated_at')[:10]
        )
        if assessments:
            latest_assessment = assessments[0]
        for a in ClinicalAssessment.objects.select_related(
                'assessed_by', 'bite_case', 'bite_case__vaccination_schedule', 'vaccine_inventory').filter(patient=patient):
            if a.bite_case_id:
                clinical_by_case[a.bite_case_id] = a
        # prefer the assessment linked to the latest bite case
        try:
            bite_list = list(bite_cases) if bite_cases else []
            if bite_list:
                newest = bite_list[0]
                if newest.pk in clinical_by_case:
                    latest_assessment = clinical_by_case[newest.pk]
        except Exception:
            pass
    except Exception:
        pass
    patient_qr_data_uri, _ = _patient_qr_data_uri(patient)
    # Primary vaccination schedule for this patient — the most recent bite case
    # that actually has one. Used by the "Vaccination Schedule" shortcut so it
    # opens this patient's schedule detail instead of the global list.
    primary_schedule = None
    for _case in bite_cases:
        _sched = getattr(_case, 'vaccination_schedule', None)
        if _sched:
            primary_schedule = _sched
            break
    # Schedule shown inside the clinical assessment's dose-plan section: prefer
    # the schedule of the assessment's own case, otherwise the primary one.
    display_schedule = None
    if latest_assessment and latest_assessment.bite_case_id:
        display_schedule = getattr(latest_assessment.bite_case, 'vaccination_schedule', None)
    display_schedule = display_schedule or primary_schedule

    # Section D — the doctor's selected dose count, independent of how many dose
    # records the nurse has created so far. The table is padded to that count.
    dose_count = None
    if latest_assessment:
        if latest_assessment.dose_protocol:
            dose_count = len(latest_assessment.protocol_days(latest_assessment.dose_protocol))
        elif latest_assessment.dose_schedule_plan:
            match = re.search(r'(\d+)\s*doses?\b', latest_assessment.dose_schedule_plan, re.I)
            if match:
                dose_count = int(match.group(1))
    saved_doses = list(display_schedule.doses.all()) if display_schedule else []
    if dose_count is None and saved_doses:
        dose_count = len(saved_doses)

    def _ordinal_dose_label(number):
        suffix = 'th' if 10 <= number % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(number % 10, 'th')
        return f'{number}{suffix} Dose'

    clinical_dose_rows = []
    for index in range(1, (dose_count or 0) + 1):
        dose = saved_doses[index - 1] if index - 1 < len(saved_doses) else None
        record_date = dose.clinical_date if dose else None
        if index == 1 and record_date is None and latest_assessment:
            record_date = latest_assessment.first_dose_date
        status = dose.clinical_status if dose else 'Pending Scheduling'
        clinical_dose_rows.append({
            'label': _ordinal_dose_label(index),
            'date': record_date,
            'status': status,
            'status_class': {
                'Completed': 'pd-badge--final',
                'Pending Scheduling': 'pd-badge--draft',
                'Missed': 'pd-badge--off',
                'Cancelled': 'pd-badge--off',
            }.get(status, ''),
        })

    first_dose_date = latest_assessment.first_dose_date if latest_assessment else None
    if first_dose_date is None and saved_doses:
        first_dose_date = saved_doses[0].clinical_date

    return render(request, 'patients/patient_detail.html', {'patient': patient, 'bite_cases': bite_cases, 'temp_creds': temp_creds, 'portal_login_url': portal_login_url, 'portal_qr_data_uri': portal_qr_data_uri, 'can_manage_portal': can_manage_portal, 'patient_qr_data_uri': patient_qr_data_uri, 'latest_vitals': latest_vitals, 'recent_vitals': recent_vitals, 'latest_assessment': latest_assessment, 'assessments': assessments, 'primary_schedule': primary_schedule, 'display_schedule': display_schedule, 'dose_count': dose_count, 'clinical_dose_rows': clinical_dose_rows, 'first_dose_date': first_dose_date})


@login_required
def patient_reset_password(request, pk):
    # Administrator-only account management: patient portal credentials must
    # never be reset through a nurse- or doctor-accessible route.
    if getattr(request.user, 'role', '') != 'admin':
        raise PermissionDenied(
            'Only the System Administrator can manage patient portal access.')
    patient = get_object_or_404(Patient.objects.select_related('account'), pk=pk)
    if request.method == 'POST':
        from utils.credentials import reset_patient_password, create_patient_user
        # If no account, create; else reset
        if patient.account_id:
            user, temp, _ = reset_patient_password(patient, created_by=request.user)
        else:
            user, temp, _ = create_patient_user(patient, created_by=request.user)
        # Store in session for display
        request.session[f'temp_creds_{patient.pk}'] = {
            'username': user.username,
            'password': temp,
            'created': False,
            'reset': True,
        }
        # Try SMS
        try:
            from vaccination.sms import send_semaphore_sms
            login_url = request.build_absolute_uri(reverse('accounts:login'))
            sms_msg = (
                f"ABTC Dumingag: Hello {patient.first_name}, your login was reset. "
                f"Username: {user.username} New Temp Password: {temp}. "
                f"Login at {login_url} and change password. -ABTC Dumingag"
            )
            res = send_semaphore_sms(patient.contact_number, sms_msg)
            if res.get('success'):
                messages.info(request, f'Password reset SMS sent to {patient.contact_display}.')
        except Exception:
            pass
        messages.success(request, f'Password reset for {patient.patient_number}. New temp: {temp} (user: {user.username}) — share securely and ask patient to change via Profile > Change Password.')
        return redirect('patients:patient_detail', pk=patient.pk)
    # GET shows confirm page? For now just redirect with POST required
    messages.info(request, 'Use the Reset button to generate a new temporary password.')
    return redirect('patients:patient_detail', pk=patient.pk)

@login_required
@require_POST
def patient_delete(request, pk):
    """Admin-only permanent deletion of a patient and their eligible records.

    The typed Patient ID is the confirmation step; retention-protected records
    (dispensed prescriptions, administered doses, signed assessments) block the
    operation before anything is touched."""
    if getattr(request.user, 'role', '') != 'admin':
        messages.error(request, 'Only the System Administrator can delete patient records.')
        return redirect('patients:patient_list')

    patient = get_object_or_404(Patient.objects.select_related('account'), pk=pk)
    patient_number = patient.patient_number
    entered = (request.POST.get('confirm_patient_number') or '').strip()
    if entered != patient_number:
        messages.error(
            request,
            f'Deletion cancelled — the Patient ID you typed does not match {patient_number}. '
            'Nothing was deleted.',
        )
        return redirect('patients:patient_list')

    summary = patient_record_summary(patient)
    blockers = protected_record_labels(summary)
    if blockers:
        messages.error(request, _retention_block_message(patient_number, blockers))
        return redirect('patients:patient_list')

    try:
        summary = delete_patient_records(patient, request.user)
    except PatientDeletionBlocked as exc:
        messages.error(request, _retention_block_message(patient_number, exc.labels))
        return redirect('patients:patient_list')

    messages.success(
        request,
        f'Patient {patient_number} and all eligible related records were permanently deleted: '
        f'{deletion_summary_text(summary)}. Inventory and audit history were preserved.',
    )
    return redirect('patients:patient_list')


def _retention_block_message(patient_number, blockers):
    return (
        f'Permanent deletion of patient {patient_number} is blocked by the medical '
        f'record-retention policy. Protected records: {", ".join(blockers)}. '
        'Nothing was deleted. Use Archive to remove the patient from active lists '
        'while keeping those records.'
    )


@login_required
@require_POST
def patient_archive(request, pk):
    """Admin-only archive alternative for patients whose records must be kept."""
    if getattr(request.user, 'role', '') != 'admin':
        messages.error(request, 'Only the System Administrator can archive patient records.')
        return redirect('patients:patient_list')

    patient = get_object_or_404(Patient, pk=pk)
    if patient.is_archived:
        messages.info(request, f'Patient {patient.patient_number} is already archived.')
        return redirect('patients:patient_list')

    summary = patient_record_summary(patient)
    blockers = protected_record_labels(summary)
    patient.is_archived = True
    patient.save(update_fields=['is_archived', 'updated_at'])
    AuditLog.objects.create(
        user=request.user,
        action='ARCHIVE_PATIENT',
        target_type='patient',
        target_id=patient.pk,
        description=(
            f'Archived patient {patient.patient_number} by admin {request.user.username}. '
            f'Preserved: {", ".join(blockers) if blockers else "patient clinical records"}.'
        ),
    )
    messages.success(
        request,
        f'Patient {patient.patient_number} was archived and removed from active lists. '
        'Stored clinical records were preserved.',
    )
    return redirect('patients:patient_list')


@login_required
@require_POST
def patient_retrieve(request, pk):
    """Admin-only restore of an archived patient record."""
    if getattr(request.user, 'role', '') != 'admin':
        messages.error(request, 'Only the System Administrator can retrieve patient records.')
        return redirect_back(request, 'audit:audit_list')

    patient = get_object_or_404(Patient, pk=pk)
    if not patient.is_archived:
        messages.info(request, f'Patient {patient.patient_number} is not archived.')
        return redirect_back(request, 'audit:audit_list')

    patient.is_archived = False
    patient.save(update_fields=['is_archived', 'updated_at'])
    AuditLog.objects.create(
        user=request.user,
        action='RETRIEVE_PATIENT',
        target_type='patient',
        target_id=patient.pk,
        description=(
            f'Retrieved patient {patient.patient_number} by admin {request.user.username}. '
            'The record was restored to the active patient list.'
        ),
    )
    messages.success(
        request,
        f'Patient {patient.patient_number} was retrieved and restored to the active patient list.',
    )
    return redirect_back(request, 'audit:audit_list')


@login_required
def patient_edit(request, pk):
    # Only staff/admin can edit patient info (same layout as print sheet)
    # Nurses and doctors are read-only on patient records — corrections to
    # demographics or medical history are made by an authorised administrator.
    if getattr(request.user, 'role', '') in ('patient', 'nurse', 'doctor'):
        messages.error(request, 'Not authorized — patient records are edited by an administrator. Nurses and doctors have read-only access.')
        return redirect('patients:patient_detail', pk=pk)
    patient = get_object_or_404(Patient, pk=pk)
    if request.method == 'POST':
        form = PatientForm(request.POST, request.FILES, instance=patient)
        if form.is_valid():
            form.save()
            AuditLog.objects.create(user=request.user, action='UPDATE_PATIENT', description=f'Updated patient {patient.patient_number}')
            messages.success(request, 'Patient updated successfully.')
            return redirect('patients:patient_detail', pk=patient.pk)
    else:
        form = PatientForm(instance=patient)
    return render(request, 'patients/patient_form.html', {'form': form, 'title': 'Edit Patient'})


@login_required
def patient_print(request, pk):
    """HTML printable sheet — emerald style, with stored vital signs and doctor clinical assessment if available."""
    patient = get_object_or_404(Patient.objects.select_related('barangay', 'account'), pk=pk)
    # Access control (RBAC): doctors may only print their own patients or unassigned cases.
    if getattr(request.user, 'role', '') == 'doctor' and not _doctor_can_access_patient(request.user, patient):
        messages.error(request, 'You do not have access to this patient record.')
        return redirect('patients:patient_list')
    bite_cases = patient.bite_cases.select_related().prefetch_related('vaccination_schedule__doses').order_by('-created_at')[:5] if hasattr(patient, 'bite_cases') else []
    latest_case = bite_cases[0] if bite_cases else None
    # Latest vitals for filling the sheet
    latest_vitals = patient.get_latest_vitals() if hasattr(patient, 'get_latest_vitals') else None
    # Also recent vitals history for reference (last 3)
    recent_vitals = list(patient.vital_signs.order_by('-taken_at')[:3]) if hasattr(patient, 'vital_signs') else []
    # Latest clinical assessment (for Section 3)
    latest_assessment = None
    clinical_by_case = {}
    try:
        from doctor.models import ClinicalAssessment
        # latest for patient
        latest_assessment = (
            ClinicalAssessment.objects
            .select_related('assessed_by', 'bite_case', 'bite_case__vaccination_schedule', 'vaccine_inventory')
            .prefetch_related('prescriptions__medicine', 'bite_case__vaccination_schedule__doses')
            .filter(patient=patient)
            .order_by('-encounter_date', '-updated_at')
            .first()
        )
        # for latest_case specifically, prefer its assessment if exists
        if latest_case and hasattr(latest_case, 'clinical_assessment') and latest_case.clinical_assessment:
            latest_assessment = latest_case.clinical_assessment
        # map case -> assessment for template convenience
        for c in bite_cases:
            if hasattr(c, 'clinical_assessment') and c.clinical_assessment:
                clinical_by_case[c.pk] = c.clinical_assessment
            else:
                # try lookup
                ca = ClinicalAssessment.objects.filter(bite_case=c).first()
                if ca:
                    clinical_by_case[c.pk] = ca
    except Exception:
        pass
    # Schedule belonging to the assessment's own case, for the Dose Schedule Records table.
    display_schedule = None
    if latest_assessment and latest_assessment.bite_case_id:
        display_schedule = getattr(latest_assessment.bite_case, 'vaccination_schedule', None)
    if not display_schedule:
        for _case in bite_cases:
            _sched = getattr(_case, 'vaccination_schedule', None)
            if _sched:
                display_schedule = _sched
                break
    # A prescription can outlive its medicine row (dangling FK). Resolve it here so
    # the sheet still prints every prescription instead of failing outright.
    prescription_rows = []
    if latest_assessment:
        from inventory.models import MedicineInventory
        for rx in latest_assessment.prescriptions.all():
            try:
                medicine = rx.medicine
            except MedicineInventory.DoesNotExist:
                medicine = None
            if medicine:
                prescription_rows.append({
                    'medicine_name': medicine.medicine_name,
                    'strength': medicine.strength,
                    'status': rx.get_status_display(),
                    'quantity': rx.quantity_display,
                })
            else:
                prescription_rows.append({
                    'medicine_name': 'Medicine record unavailable',
                    'strength': '',
                    'status': rx.get_status_display(),
                    'quantity': str(rx.quantity),
                })
    ctx = {
        'patient': patient,
        'bite_cases': bite_cases,
        'latest_case': latest_case,
        'latest_vitals': latest_vitals,
        'recent_vitals': recent_vitals,
        'latest_assessment': latest_assessment,
        'clinical_by_case': clinical_by_case,
        'display_schedule': display_schedule,
        'prescription_rows': prescription_rows,
        'now': datetime.now(),
        'print_mode': True,
    }
    ctx['patient_qr_data_uri'], ctx['patient_qr_payload'] = _patient_qr_data_uri(patient)
    return render(request, 'patients/patient_print.html', ctx)


@login_required
def patient_print_pdf(request, pk):
    """Generate print-ready PDF for patient — emerald style, includes blank vital signs and vaccine schedule."""
    patient = get_object_or_404(Patient.objects.select_related('barangay', 'account'), pk=pk)
    # Access control (RBAC): doctors may only export their own patients or unassigned cases.
    if getattr(request.user, 'role', '') == 'doctor' and not _doctor_can_access_patient(request.user, patient):
        messages.error(request, 'You do not have access to this patient record.')
        return redirect('patients:patient_list')
    try:
        bite_cases_qs = patient.bite_cases.select_related().prefetch_related('vaccination_schedule__doses').order_by('-created_at')
        bite_cases = list(bite_cases_qs[:5])
    except Exception:
        bite_cases = list(patient.bite_cases.order_by('-created_at')[:5]) if hasattr(patient, 'bite_cases') else []

    # Latest clinical assessment for PDF Section 3
    latest_assessment = None
    try:
        from doctor.models import ClinicalAssessment
        latest_assessment = (
            ClinicalAssessment.objects
            .select_related('assessed_by', 'bite_case', 'bite_case__vaccination_schedule', 'vaccine_inventory')
            .prefetch_related('prescriptions__medicine', 'bite_case__vaccination_schedule__doses')
            .filter(patient=patient)
            .order_by('-encounter_date', '-updated_at')
            .first()
        )
        if bite_cases and hasattr(bite_cases[0], 'clinical_assessment') and bite_cases[0].clinical_assessment:
            latest_assessment = bite_cases[0].clinical_assessment
    except Exception:
        pass
    # Schedule for the assessment's own case, shown inside the PDF dose records table.
    assessment_schedule = None
    if latest_assessment and latest_assessment.bite_case_id:
        assessment_schedule = getattr(latest_assessment.bite_case, 'vaccination_schedule', None)
    if not assessment_schedule:
        for _case in bite_cases:
            _sched = getattr(_case, 'vaccination_schedule', None)
            if _sched:
                assessment_schedule = _sched
                break

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable, KeepTogether
    from reportlab.lib.colors import HexColor
    from reports.pdf import PAGE_MARGIN, _NumberedCanvas, _rule, _summary_panel, letterhead
    import html as _html

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=PAGE_MARGIN, bottomMargin=18*mm,
        leftMargin=PAGE_MARGIN, rightMargin=PAGE_MARGIN,
        title=f"{patient.patient_number} - {patient.full_name()}",
        author="ABTC System"
    )

    emerald_950 = HexColor("#08291F")
    emerald_800 = HexColor("#08291F")
    emerald_700 = HexColor("#08291F")
    sage_line = HexColor("#D9DED9")
    sage_50 = HexColor("#FAF8F2")
    sage_100 = HexColor("#F3F0E7")
    muted = HexColor("#26352F")
    light_muted = HexColor("#496458")

    styles = getSampleStyleSheet()
    s_h1 = ParagraphStyle('h1', parent=styles['Heading1'], fontSize=13, leading=15, textColor=emerald_950, fontName='Helvetica-Bold', alignment=TA_CENTER, spaceAfter=2)
    s_h1sub = ParagraphStyle('h1sub', parent=styles['Normal'], fontSize=6.5, leading=8, textColor=light_muted, fontName='Helvetica', alignment=TA_CENTER, spaceAfter=4)
    s_section = ParagraphStyle('section', parent=styles['Normal'], fontSize=8.5, leading=10.5, textColor=HexColor("#08291F"), fontName='Helvetica-Bold', alignment=TA_LEFT)
    s_label = ParagraphStyle('label', parent=styles['Normal'], fontSize=6, leading=7, textColor=HexColor("#496458"), fontName='Helvetica-Bold', alignment=TA_LEFT)
    s_value = ParagraphStyle('value', parent=styles['Normal'], fontSize=7.5, leading=9, textColor=emerald_950, fontName='Helvetica-Bold', alignment=TA_LEFT)
    s_value_small = ParagraphStyle('vsmall', parent=s_value, fontSize=7)
    s_td = ParagraphStyle('td', parent=styles['Normal'], fontSize=6.5, leading=8, textColor=muted, fontName='Helvetica', alignment=TA_LEFT)
    s_td_center = ParagraphStyle('tdc', parent=s_td, alignment=TA_CENTER)
    s_footer = ParagraphStyle('foot', parent=styles['Normal'], fontSize=6, leading=8, textColor=HexColor("#8aa589"), fontName='Helvetica', alignment=TA_CENTER)
    s_note = ParagraphStyle('note', parent=styles['Normal'], fontSize=6, leading=7.5, textColor=muted, fontName='Helvetica')

    cell = lambda txt, style=s_td: Paragraph(str(txt) if txt not in (None, '') else "-", style)
    label_cell = lambda txt: Paragraph(f'<font color="#496458"><b>{txt}</b></font>', s_label)

    story = []

    # Official ABTC letterhead
    pid = patient.patient_number
    pname = patient.full_name()
    story.append(letterhead("Patient Information Sheet"))
    story.append(_rule())
    story.append(Paragraph(
        f'Date Printed: {datetime.now().strftime("%B %d, %Y · %I:%M %p")}',
        ParagraphStyle('officialMeta', parent=styles['Normal'], fontSize=7,
                       leading=9, textColor=HexColor("#496458"),
                       fontName='Helvetica')))
    story.append(Paragraph("For clinical use — present this sheet at every visit. Verify patient identity before treatment.", s_h1sub))
    story.append(Spacer(1, 6))

    def section_header(title):
        t = Table([['', Paragraph(f'{title}', s_section)]], colWidths=[1.6*mm, 168.4*mm])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (0,0), emerald_950),
            ('LINEBELOW', (0,0), (-1,-1), 0.4, sage_line),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('LEFTPADDING', (0,0), (0,0), 0),
            ('RIGHTPADDING', (0,0), (0,0), 0),
            ('LEFTPADDING', (1,0), (1,0), 7),
            ('TOPPADDING', (0,0), (-1,-1), 3),
            ('BOTTOMPADDING', (0,0), (-1,-1), 3),
            ('TOPPADDING', (0,0), (0,0), 0),
            ('BOTTOMPADDING', (0,0), (0,0), 0),
        ]))
        return t

    def panel_value(value):
        return value if value not in (None, '') else 'Not Recorded'

    def fmt_dt(value, pattern="%B %d, %Y · %I:%M %p"):
        return value.strftime(pattern) if value else 'Not Recorded'

    barangay_name = str(patient.barangay) if patient.barangay_id else 'Not Recorded'
    emergency_contact = patient.emergency_contact_name or 'Not Recorded'
    if patient.emergency_contact_name and patient.emergency_contact_number:
        emergency_contact = f"{patient.emergency_contact_name} · {patient.emergency_contact_display}"

    # Patient Information
    story.append(section_header("Patient Information"))
    story.append(Spacer(1, 4))
    story.append(_summary_panel([
        ('Patient Full Name', pname),
        ('Patient ID', pid),
        ('Birthdate', fmt_dt(patient.birthdate, "%B %d, %Y")),
        ('Age', str(patient.age)),
        ('Sex', panel_value(patient.get_sex_display())),
        ('Civil Status', panel_value(patient.get_civil_status_display())),
        ('Contact Number', panel_value(patient.contact_display)),
        ('Barangay', barangay_name),
        ('Complete Address', panel_value(patient.address)),
        ('Parent / Guardian', panel_value(patient.parent_guardian)),
        ('Emergency Contact', emergency_contact),
    ]))
    story.append(Spacer(1, 8))

    # Medical Information
    story.append(section_header("Medical Information"))
    story.append(Spacer(1, 4))
    story.append(_summary_panel([
        ('Medical History', panel_value(patient.medical_history)),
        ('Allergies', panel_value(patient.allergy_info)),
        ('Previous Rabies Vaccination', panel_value(patient.previous_rabies_vax)),
        ('Remarks', panel_value(patient.remarks)),
    ]))
    story.append(Spacer(1, 8))

    # Animal Bite Information — latest case only, never mixed with other incidents
    latest_case = bite_cases[0] if bite_cases else None
    story.append(section_header("Animal Bite Information"))
    story.append(Spacer(1, 4))
    if latest_case:
        who_category = 'Not Recorded'
        try:
            exposure_info = latest_case.exposure
        except Exception:
            exposure_info = None
        if exposure_info is not None:
            who_category = exposure_info.get_category_display()
        elif (latest_assessment and latest_assessment.bite_case_id == latest_case.pk
              and latest_assessment.category_confirmed):
            who_category = latest_assessment.category_label or 'Not Recorded'
        story.append(_summary_panel([
            ('Case Number', latest_case.case_number),
            ('Date of Bite', fmt_dt(latest_case.date_time_bite)),
            ('Animal Type', latest_case.get_animal_type_display()),
            ('Exposure Type', latest_case.get_exposure_type_display()),
            ('Bite Location / Body Part', panel_value(latest_case.body_part_affected)),
            ('WHO Bite Category', who_category),
            ('Case Status', latest_case.get_case_status_display()),
            ('Place of Incident', panel_value(latest_case.place_of_incident)),
        ]))
        story.append(Paragraph(
            f"<i>This sheet refers to bite case <b>{_html.escape(latest_case.case_number)}</b>. "
            "Details from other bite incidents are listed separately under Recent Bite Cases.</i>", s_note))
    else:
        story.append(Paragraph(
            "No bite case has been recorded for this patient. This sheet serves as the baseline intake form.",
            s_note))
    story.append(Spacer(1, 8))

    # Vaccination Information — vaccine and RIG are reported separately
    first_dose_date = latest_assessment.first_dose_date if latest_assessment else None
    if not first_dose_date and assessment_schedule:
        first_dose = assessment_schedule.doses.all().order_by('id').first()
        first_dose_date = first_dose.clinical_date if first_dose else None
    if latest_assessment and latest_assessment.vaccine_inventory:
        inventory = latest_assessment.vaccine_inventory
        vaccine_product = ' · '.join(
            part for part in (inventory.vaccine_name, inventory.brand) if part) or 'Not Recorded'
    elif latest_assessment and latest_assessment.vaccine_brand_plan:
        vaccine_product = latest_assessment.vaccine_brand_plan
    else:
        vaccine_product = 'Not Recorded'
    if assessment_schedule:
        dose_total = assessment_schedule.doses.count()
        total_doses = f"{dose_total} Dose" + ('' if dose_total == 1 else 's')
        rig_status = 'Given' if assessment_schedule.rig_given else 'Not Given'
    else:
        total_doses = 'Not Recorded'
        rig_status = 'Not Recorded'
    treatment_status = (
        latest_case.get_case_status_display() if latest_case else
        (latest_assessment.get_status_display() if latest_assessment else 'Not Recorded'))
    story.append(section_header("Vaccination Information"))
    story.append(Spacer(1, 4))
    story.append(_summary_panel([
        ('Recorded Vaccine / Biological Product', vaccine_product),
        ('Rabies Immunoglobulin (RIG)', rig_status),
        ('Total Required Doses', total_doses),
        ('First Dose Date', fmt_dt(first_dose_date, "%B %d, %Y")),
        ('Current Treatment Status', treatment_status),
    ]))
    story.append(Spacer(1, 8))

    # Vaccination Schedule — same DOSE / SCHEDULED DATE / STATUS view as the HTML sheet
    story.append(section_header("Vaccination Schedule"))
    story.append(Spacer(1, 4))
    if assessment_schedule and assessment_schedule.doses.exists():
        schedule_rows = []
        for dose in assessment_schedule.doses.all().order_by('id'):
            schedule_rows.append([
                Paragraph(
                    f'<b>{_html.escape(dose.number_label)}</b><br/>'
                    f'<font color="#496458" size="6">{_html.escape(dose.dose_label)}</font>',
                    ParagraphStyle('schd', parent=s_td, fontSize=6.5, leading=8)),
                Paragraph(
                    dose.scheduled_date.strftime("%B %d, %Y") if dose.scheduled_date
                    else '<font color="#496458">Pending Scheduling</font>',
                    ParagraphStyle('schdate', parent=s_td, fontSize=6.5, leading=8)),
                Paragraph(_html.escape(dose.clinical_status),
                          ParagraphStyle('schstat', parent=s_td, fontSize=6.5, leading=8)),
            ])
        sch_head = [
            Paragraph(f'<b>{h}</b>', ParagraphStyle(
                'sch_th', parent=styles['Normal'], fontSize=6, leading=7.5,
                textColor=emerald_950, fontName='Helvetica-Bold'))
            for h in ['DOSE', 'SCHEDULED DATE', 'STATUS']]
        sch_tbl = Table([sch_head] + schedule_rows,
                        colWidths=[70 * mm, 60 * mm, 40 * mm], repeatRows=1)
        sch_tbl.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), sage_100),
            ('GRID', (0, 0), (-1, -1), 0.4, sage_line),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, sage_50]),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        story.append(sch_tbl)
    else:
        story.append(_summary_panel([
            ('Schedule Status', 'Pending Scheduling'),
            ('Instruction', 'No vaccination schedule has been assigned for this patient.'),
        ]))
    story.append(Paragraph(
        "A dose is marked Completed only when its administration has been recorded. "
        "Scheduled dates do not imply the dose was given.", s_note))
    story.append(Spacer(1, 8))

    # Section 2: Vital signs — dynamic (shows stored if exists)
    latest_vitals = patient.get_latest_vitals() if hasattr(patient, 'get_latest_vitals') else None
    # Header hint — show taken info if exists
    if latest_vitals:
        taken_str = latest_vitals.taken_at.strftime("%m/%d/%Y  %H:%M") if latest_vitals.taken_at else "—"
        taken_by_str = str(latest_vitals.taken_by) if latest_vitals.taken_by else "—"
        vit_hint = f"<i>Last taken: <b>{taken_str}</b> &nbsp;by <b>{taken_by_str}</b> &nbsp;·&nbsp; Fill next on printed copy if needed.</i>"
    else:
        vit_hint = "<i>Staff: fill with ballpen on printed copy. Leave blank if not taken. Date/Time: &nbsp;_____ / _____ / _____ &nbsp;&nbsp; ___:___ &nbsp;&nbsp; Taken by: ______________________</i>"
    story.append(section_header("Vital Signs"))
    story.append(Spacer(1, 2))
    story.append(Paragraph(vit_hint, ParagraphStyle('vitals_hint', parent=styles['Normal'], fontSize=6, leading=8, textColor=HexColor("#496458"), alignment=TA_CENTER, spaceAfter=4, fontName='Helvetica-Oblique')))
    def vit_cell(label, unit, value=None, blank_len=14):
        # If value is provided and not None/empty, show it bold; else show blank underscores
        if value is not None and str(value).strip() not in ("", "—", "None"):
            val_str = str(value)
            # For BP, value already formatted like "120/80"
            return Paragraph(f'<font color="#496458" size="6"><b>{label}</b></font><br/><font color="#08291F" size="9"><b>{val_str}</b></font><font color="#496458" size="6"> {unit}</font><br/><font color="#8aa589" size="5">__________________</font>', ParagraphStyle('vit', parent=styles['Normal'], alignment=TA_CENTER, leading=9))
        blank = "_" * blank_len
        return Paragraph(f'<font color="#496458" size="6"><b>{label}</b></font><br/><font color="#08291F" size="9">{blank}</font><font color="#496458" size="6"> {unit}</font><br/><font color="#8aa589" size="5">__________________</font>', ParagraphStyle('vit', parent=styles['Normal'], alignment=TA_CENTER, leading=9))
    # Prepare values from latest_vitals
    if latest_vitals:
        bp_val = latest_vitals.bp_display if latest_vitals.bp_display else None
        temp_val = f"{latest_vitals.temperature}" if latest_vitals.temperature is not None else None
        pulse_val = f"{latest_vitals.pulse_rate}" if latest_vitals.pulse_rate is not None else None
        rr_val = f"{latest_vitals.respiratory_rate}" if latest_vitals.respiratory_rate is not None else None
        spo2_val = f"{latest_vitals.spo2}" if latest_vitals.spo2 is not None else None
        wt_val = f"{latest_vitals.weight}" if latest_vitals.weight is not None else None
        ht_val = f"{latest_vitals.height}" if latest_vitals.height is not None else None
        bmi_val = f"{latest_vitals.bmi}" if latest_vitals.bmi is not None else None
        pain_val = f"{latest_vitals.pain_score}" if latest_vitals.pain_score is not None else None
        glucose_val = f"{latest_vitals.blood_glucose}" if latest_vitals.blood_glucose is not None else None
        glucose_type = latest_vitals.glucose_type or ""
        appearance = latest_vitals.general_appearance or ""
        notes_val = latest_vitals.notes or ""
        # For radio-like fields, show filled circle if matches
        def circ(chosen, val):
            return "●" if chosen == val else "○"
        glucose_para = f'<font color="#496458" size="6"><b>RANDOM / FASTING</b></font><br/><font size="6">{circ(glucose_type,"random")} Random &nbsp; {circ(glucose_type,"fasting")} Fasting</font>'
        appearance_para = f'<font color="#496458" size="6"><b>GENERAL APPEARANCE</b></font><br/><font size="6">{circ(appearance,"well")} Well &nbsp; {circ(appearance,"ill")} Ill &nbsp; {circ(appearance,"toxic")} Toxic</font>'
    else:
        bp_val = temp_val = pulse_val = rr_val = spo2_val = wt_val = ht_val = bmi_val = pain_val = glucose_val = None
        glucose_para = '<font color="#496458" size="6"><b>RANDOM / FASTING</b></font><br/><font size="6">○ Random &nbsp; ○ Fasting</font>'
        appearance_para = '<font color="#496458" size="6"><b>GENERAL APPEARANCE</b></font><br/><font size="6">○ Well &nbsp; ○ Ill &nbsp; ○ Toxic</font>'
        notes_val = ""
    v_rows = [
        [vit_cell("BLOOD PRESSURE", "mmHg", bp_val, 10), vit_cell("TEMPERATURE", "°C", temp_val, 8), vit_cell("PULSE RATE", "bpm", pulse_val, 8), vit_cell("RESP. RATE", "/min", rr_val, 8)],
        [vit_cell("SpO<sub>2</sub>", "%", spo2_val, 8), vit_cell("WEIGHT", "kg", wt_val, 8), vit_cell("HEIGHT", "cm", ht_val, 8), vit_cell("BMI", "", bmi_val, 8)],
        [vit_cell("PAIN SCORE", "/10", pain_val, 8), vit_cell("BLOOD GLUCOSE", "mg/dL", glucose_val, 8), Paragraph(glucose_para, ParagraphStyle('vit2', parent=styles['Normal'], alignment=TA_CENTER, leading=9)), Paragraph(appearance_para, ParagraphStyle('vit3', parent=styles['Normal'], alignment=TA_CENTER, leading=9))],
    ]
    v_tbl = Table(v_rows, colWidths=[42.5*mm]*4)
    v_tbl.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.5, sage_line),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('LEFTPADDING', (0,0), (-1,-1), 4),
        ('RIGHTPADDING', (0,0), (-1,-1), 4),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('ROWBACKGROUNDS', (0,0), (-1,-1), [colors.white, sage_50, colors.white]),
    ]))
    story.append(v_tbl)
    story.append(Spacer(1, 2))
    if 'notes_val' in locals() and notes_val:
        story.append(Paragraph(f"<i>Additional notes / allergies check — nurse to confirm before vaccination:</i> &nbsp; <b>{notes_val}</b>", s_note))
        story.append(Spacer(1, 4))
    else:
        story.append(Paragraph("<i>Additional notes / allergies check — nurse to confirm before vaccination:</i> &nbsp; _______________________________________________________________________________", s_note))
        story.append(Paragraph("&nbsp; _______________________________________________________________________________", s_note))
    story.append(Spacer(1, 8))

    # Section 3: Clinical Assessment — full A–F sequence

    s_cs_head = ParagraphStyle('cshead', parent=styles['Normal'], fontSize=7.5, leading=9,
                               textColor=emerald_950, fontName='Helvetica-Bold')
    s_cs_label = ParagraphStyle('cslabel', parent=styles['Normal'], fontSize=5.5, leading=7,
                                textColor=HexColor("#496458"), fontName='Helvetica-Bold', spaceAfter=1)
    s_cs_value = ParagraphStyle('csval', parent=styles['Normal'], fontSize=7.5, leading=10,
                                textColor=emerald_950, fontName='Helvetica')
    s_cs_note = ParagraphStyle('csnote', parent=styles['Normal'], fontSize=6.5, leading=8.5,
                               textColor=light_muted, fontName='Helvetica')

    def cs_text(value, fallback='Not recorded'):
        if value in (None, ''):
            return f'<font color="#496458">{fallback}</font>'
        return _html.escape(str(value)).replace('\n', '<br/>')

    def cs_cell(label, value=None, value_html=None):
        val = value_html if value_html is not None else cs_text(value)
        return [
            Paragraph(f'<font color="#496458"><b>{_html.escape(label.upper())}</b></font>', s_cs_label),
            Paragraph(val, s_cs_value),
        ]

    def cs_grid(cells, cols=3, widths=None):
        rows = []
        for start in range(0, len(cells), cols):
            row = cells[start:start + cols]
            while len(row) < cols:
                row.append('')
            rows.append(row)
        table = Table(rows, colWidths=widths or [170 * mm / cols] * cols)
        table.setStyle(TableStyle([
            ('GRID', (0, 0), (-1, -1), 0.4, sage_line),
            ('BACKGROUND', (0, 0), (-1, -1), sage_50),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        return table

    def cs_header(letter, title):
        table = Table([[
            Paragraph(f'<font color="#08291F" size="8"><b>{letter}</b></font> &nbsp; <b>{_html.escape(title.upper())}</b>', s_cs_head)
        ]], colWidths=[170 * mm])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), sage_100),
            ('BOX', (0, 0), (-1, -1), 0.4, sage_line),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        return table

    def cs_add(letter, title, flowables):
        story.append(cs_header(letter, title))
        story.extend(flowables)
        story.append(Spacer(1, 7))

    def cs_records_table(header, rows, widths):
        head = [Paragraph(f'<font color="#08291F"><b>{h}</b></font>',
                          ParagraphStyle('cs_th', parent=styles['Normal'], fontSize=6, leading=7.5,
                                         textColor=emerald_950, fontName='Helvetica-Bold'))
                for h in header]
        data = [head] + rows
        table = Table(data, colWidths=widths, repeatRows=1)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), sage_100),
            ('GRID', (0, 0), (-1, -1), 0.4, sage_line),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, sage_50]),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        return table

    if latest_assessment:
        cat_label = latest_assessment.category_label or "Not recorded"
        cat_desc = latest_assessment.category_description or "No category description recorded."
        story.append(section_header(
            f"Clinical Assessment <font color=\"#496458\" size=\"6\">— "
            f"Dr. {(latest_assessment.assessed_by.get_full_name() if latest_assessment.assessed_by else '—')} · "
            f"{latest_assessment.encounter_date.strftime('%b %d, %Y %I:%M %p') if latest_assessment.encounter_date else ''} · "
            f"{cat_label} · {latest_assessment.get_status_display()}</font>"))
        story.append(Spacer(1, 4))

        # A. Chief Complaint
        cs_add('A', 'Chief Complaint', [
            cs_grid([
                cs_cell('Chief Complaint', latest_assessment.chief_complaint),
                cs_cell('History of Present Illness', latest_assessment.history_present_illness),
            ], cols=1, widths=[170 * mm]),
        ])

        # B. Exposure Narrative (Verified) + verified details + examination
        wash = "Yes" if latest_assessment.wound_washed_verified else ("No" if latest_assessment.wound_washed_verified is False else None)
        b_flows = [
            cs_grid([cs_cell('Narrative as Verified by the Doctor', latest_assessment.exposure_narrative)],
                    cols=1, widths=[170 * mm]),
            Paragraph('<font color="#496458"><b>VERIFIED INCIDENT DETAILS</b></font>', s_cs_label),
        ]
        b_flows.append(cs_grid([
            cs_cell('Verified Animal Type', latest_assessment.animal_type_verified),
            cs_cell('Ownership (Verified)', latest_assessment.get_animal_owned_verified_display),
            cs_cell('Animal Vaccination (Verified)', latest_assessment.get_animal_vax_verified_display),
            cs_cell('Wound Washed Immediately', wash),
            cs_cell('Body Part (Verified)', latest_assessment.body_part_verified),
            cs_cell('Number of Wounds (Verified)', latest_assessment.number_of_wounds_verified),
            cs_cell('Date & Time of Bite (Verified)',
                    latest_assessment.date_time_bite_verified.strftime('%b %d, %Y · %I:%M %p')
                    if latest_assessment.date_time_bite_verified else None),
        ], cols=3))
        b_flows.append(Paragraph('<font color="#496458"><b>EXAMINATION FINDINGS</b></font>', s_cs_label))
        b_flows.append(cs_grid([
            cs_cell('HEENT', latest_assessment.exam_heent),
            cs_cell('Chest / Lungs', latest_assessment.exam_chest_lungs),
            cs_cell('Cardiac', latest_assessment.exam_cardiac),
            cs_cell('Abdomen', latest_assessment.exam_abdomen),
            cs_cell('Skin / Wound Description', latest_assessment.skin_wound_description),
            cs_cell('Systemic Review', latest_assessment.systemic_review),
        ], cols=3))
        cs_add('B', 'Exposure Narrative (Verified)', b_flows)

        # C. Bite Category
        category_html = (
            f'<font size="11"><b>{_html.escape(cat_label)}</b></font><br/>'
            f'<font size="7">{_html.escape(cat_desc)}</font>'
        )
        cs_add('C', 'Bite Category', [
            cs_grid([cs_cell('WHO Bite Category', value_html=category_html)],
                    cols=1, widths=[170 * mm]),
        ])

        # D. Dose Schedule & Vaccine
        dose_plan_html = ''
        if latest_assessment.dose_protocol:
            dose_plan_html = f'<b>{_html.escape(latest_assessment.get_dose_protocol_display())}</b>'
        if latest_assessment.dose_schedule_plan:
            dose_plan_html += ('<br/>' if dose_plan_html else '') + _html.escape(latest_assessment.dose_schedule_plan)
        d_flows = [
            cs_grid([
                cs_cell('Dose Schedule', value_html=dose_plan_html or cs_text(None)),
                cs_cell('Date (1st Dose)',
                        latest_assessment.first_dose_date.strftime('%b %d, %Y')
                        if latest_assessment.first_dose_date else None),
            ], cols=2),
            Paragraph('<font color="#496458"><b>DOSE SCHEDULE RECORDS</b></font>', s_cs_label),
        ]
        if assessment_schedule:
            doses = list(assessment_schedule.doses.all().order_by('id'))
            d_flows.append(Paragraph(
                f'{len(doses)} dose{"s" if len(doses) != 1 else ""} on this schedule.', s_cs_note))
            dose_rows = []
            for dose in doses:
                dose_rows.append([
                    Paragraph(f'<b>{_html.escape(dose.dose_label)}</b>'
                              + (f' · Day {dose.protocol_day}' if dose.protocol_day is not None else ''),
                              ParagraphStyle('cs_dose', parent=s_td, fontSize=6.5, leading=8)),
                    Paragraph(dose.clinical_date.strftime('%b %d, %Y') if dose.clinical_date
                              else '<font color="#496458">Not yet set</font>',
                              ParagraphStyle('cs_date', parent=s_td, fontSize=6.5, leading=8)),
                    Paragraph(_html.escape(dose.clinical_status),
                              ParagraphStyle('cs_stat', parent=s_td, fontSize=6.5, leading=8)),
                ])
            d_flows.append(cs_records_table(
                ['DOSE', 'DATE', 'STATUS'], dose_rows, [55 * mm, 70 * mm, 45 * mm]))
            flags = []
            if assessment_schedule.rig_given:
                flags.append('RIG given')
            if assessment_schedule.tetanus_toxoid_given:
                flags.append('Tetanus toxoid given')
            if assessment_schedule.antibiotics_given:
                flags.append('Antibiotics given')
            if flags:
                d_flows.append(Paragraph(' · '.join(flags), s_cs_note))
            if assessment_schedule.doctor_notes:
                d_flows.append(Paragraph(_html.escape(assessment_schedule.doctor_notes), s_cs_note))
        else:
            d_flows.append(Paragraph('No vaccination schedule has been generated for this case.', s_cs_note))
        cs_add('D', 'Dose Schedule & Vaccine', d_flows)

        # E. Vaccine Brand
        inventory = latest_assessment.vaccine_inventory
        if inventory:
            brand_main = inventory.brand or latest_assessment.vaccine_brand_plan
            brand_sub = inventory.vaccine_name
            brand_lot = latest_assessment.vaccine_lot_number or inventory.batch_number
        else:
            brand_main = latest_assessment.vaccine_brand_plan
            brand_sub = ''
            brand_lot = latest_assessment.vaccine_lot_number
        brand_html = ''
        if brand_main:
            brand_html = f'<font size="9"><b>{_html.escape(brand_main)}</b></font>'
        if brand_sub:
            brand_html += f'<br/>{_html.escape(brand_sub)}'
        if brand_lot:
            brand_html += f'<br/><font color="#496458" size="6">Lot / batch: {_html.escape(brand_lot)}</font>'
        if not brand_html:
            brand_html = cs_text(None)
        cs_add('E', 'Vaccine Brand', [
            cs_grid([cs_cell('Vaccine Brand (from Stock)', value_html=brand_html)],
                    cols=1, widths=[170 * mm]),
            cs_grid([
                cs_cell('Volume per Dose (mL)', latest_assessment.vaccine_dose_ml),
                cs_cell('Quantity (Vials/Doses)', latest_assessment.vaccine_quantity),
                cs_cell('1st Dose Date',
                        latest_assessment.first_dose_date.strftime('%b %d, %Y')
                        if latest_assessment.first_dose_date else None),
            ], cols=3),
        ])

        # F. Treatment & Prescription
        flags_text = (
            f'PEP Indicated: {"Yes" if latest_assessment.pep_indicated else "No"} &nbsp;·&nbsp; '
            f'RIG Indicated: {"Yes" if latest_assessment.rig_indicated else "No"} &nbsp;·&nbsp; '
            f'Tetanus Toxoid Indicated: {"Yes" if latest_assessment.tt_indicated else "No"}'
        )
        f_flows = [Paragraph(flags_text, s_cs_note)]
        f_flows.append(cs_grid([cs_cell('Treatment Plan', latest_assessment.treatment_plan)],
                               cols=1, widths=[170 * mm]))
        f_flows.append(Paragraph('<font color="#496458"><b>PRESCRIPTION</b></font>', s_cs_label))
        prescriptions = list(latest_assessment.prescriptions.all())
        if prescriptions:
            from inventory.models import MedicineInventory
            rx_rows = []
            for rx in prescriptions:
                try:
                    medicine = rx.medicine
                except MedicineInventory.DoesNotExist:
                    medicine = None
                if medicine:
                    med_html = f'<b>{_html.escape(medicine.medicine_name)}</b>'
                    if medicine.strength:
                        med_html += f' · {_html.escape(medicine.strength)}'
                    qty_display = rx.quantity_display
                else:
                    med_html = '<b>Medicine record unavailable</b>'
                    qty_display = str(rx.quantity)
                med_html += (f'<br/><font color="#496458" size="6">'
                             f'{_html.escape(rx.get_status_display())}</font>')
                rx_rows.append([
                    Paragraph(med_html, ParagraphStyle('cs_rx', parent=s_td, fontSize=6.5, leading=8)),
                    Paragraph(_html.escape(qty_display),
                              ParagraphStyle('cs_qty', parent=s_td, fontSize=6.5, leading=8)),
                ])
            f_flows.append(cs_records_table(['MEDICINE', 'QUANTITY'], rx_rows, [110 * mm, 60 * mm]))
            if latest_assessment.prescription:
                f_flows.append(Paragraph(
                    f'Prescription note: {cs_text(latest_assessment.prescription)}', s_cs_note))
        elif latest_assessment.prescription:
            f_flows.append(Paragraph(cs_text(latest_assessment.prescription), s_cs_value))
        else:
            f_flows.append(Paragraph(cs_text(None), s_cs_value))
        f_flows.append(cs_grid([
            cs_cell('Dosage Instructions — Frequency & Duration', latest_assessment.dosage_instructions),
            cs_cell('Follow-up Instructions', latest_assessment.follow_up_instructions),
            cs_cell('Additional Notes / Allergy Re-check', latest_assessment.additional_notes),
        ], cols=1, widths=[170 * mm]))
        f_flows.append(Paragraph(
            f'Assessed by <b>{_html.escape(latest_assessment.assessed_by.get_full_name() if latest_assessment.assessed_by else "—")}</b>'
            + (f' · PRC {_html.escape(latest_assessment.prc_number)}' if latest_assessment.prc_number else '')
            + (f' · {latest_assessment.encounter_date.strftime("%b %d, %Y %I:%M %p")}' if latest_assessment.encounter_date else '')
            + f' · {latest_assessment.get_status_display()}', s_cs_note))
        cs_add('F', 'Treatment & Prescription', f_flows)
    else:
        story.append(section_header("Clinical Assessment <font color=\"#496458\" size=\"6\">— no assessment recorded</font>"))
        story.append(Spacer(1, 4))
        empty_tbl = Table([[
            Paragraph('<b>NO ASSESSMENT YET</b><br/>'
                      '<font color="#496458" size="7">No doctor assessment has been recorded for this case.</font>',
                      ParagraphStyle('cs_empty', parent=s_td, alignment=TA_CENTER, leading=10))
        ]], colWidths=[170 * mm])
        empty_tbl.setStyle(TableStyle([
            ('BOX', (0, 0), (-1, -1), 0.5, sage_line),
            ('BACKGROUND', (0, 0), (-1, -1), sage_50),
            ('TOPPADDING', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ]))
        story.append(empty_tbl)
        story.append(Spacer(1, 8))

    # Section 5: Recent Bite Cases
    story.append(section_header("Recent Bite Cases"))
    story.append(Spacer(1, 4))
    if bite_cases:
        case_headers = ["Case #", "Date", "Animal", "Exposure", "Status"]
        case_data = [[cell(f"<b>{h}</b>", ParagraphStyle('ch', parent=s_td, alignment=TA_CENTER, textColor=emerald_950, fontName='Helvetica-Bold')) for h in case_headers]]
        for c in bite_cases:
            case_data.append([
                cell(c.case_number, s_td_center),
                cell(c.date_time_bite.strftime("%Y-%m-%d") if c.date_time_bite else "-", s_td_center),
                cell(c.get_animal_type_display(), s_td_center),
                cell(c.get_exposure_type_display(), s_td_center),
                cell(c.get_case_status_display(), s_td_center),
            ])
        cw = [30*mm, 28*mm, 28*mm, 32*mm, 52*mm]
        ctbl = Table(case_data, colWidths=cw)
        ctbl.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), sage_100),
            ('TEXTCOLOR', (0,0), (-1,0), emerald_950),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('GRID', (0,0), (-1,-1), 0.4, sage_line),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, sage_50]),
            ('LEFTPADDING', (0,0), (-1,-1), 3),
            ('RIGHTPADDING', (0,0), (-1,-1), 3),
            ('TOPPADDING', (0,0), (-1,-1), 3),
            ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ]))
        story.append(ctbl)
    else:
        story.append(Paragraph("<i>No bite cases recorded yet — this sheet will serve as baseline intake form.</i>", ParagraphStyle('no_case', parent=styles['Normal'], fontSize=7, leading=9, textColor=light_muted, alignment=TA_CENTER, fontName='Helvetica-Oblique', borderPadding=(6,6,6), borderColor=sage_line, borderWidth=0.5, backColor=sage_50)))
    story.append(Spacer(1, 8))

    # Signature footer
    sig_data = [
        [Paragraph("____________________________<br/><font size=\"6\" color=\"#496458\"><b>Patient / Guardian</b><br/>Signature over printed name & date</font>", ParagraphStyle('sig', parent=styles['Normal'], alignment=TA_CENTER, leading=8)),
         Paragraph("____________________________<br/><font size=\"6\" color=\"#496458\"><b>Triage Nurse</b><br/>Signature & date/time</font>", ParagraphStyle('sig2', parent=styles['Normal'], alignment=TA_CENTER, leading=8)),
         Paragraph("____________________________<br/><font size=\"6\" color=\"#496458\"><b>Physician / ABTC Doctor</b><br/>Signature & PRC no.</font>", ParagraphStyle('sig3', parent=styles['Normal'], alignment=TA_CENTER, leading=8)),
        ],
        [Paragraph("Date: ______ / ______ / ______", s_td_center), Paragraph("Time: ______ : ______", s_td_center), Paragraph("Date: ______ / ______ / ______", s_td_center)],
    ]
    sig_tbl = Table(sig_data, colWidths=[56*mm, 56*mm, 58*mm])
    sig_tbl.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING', (0,0), (-1,-1), 6),
        ('RIGHTPADDING', (0,0), (-1,-1), 6),
        ('TOPPADDING', (0,0), (-1,-1), 12),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('LINEBELOW', (0,1), (-1,1), 0.3, sage_line),
    ]))
    story.append(KeepTogether(sig_tbl))
    story.append(Spacer(1, 10))

    doc.build(story, canvasmaker=_NumberedCanvas)
    pdf_bytes = buf.getvalue()
    buf.close()

    resp = HttpResponse(pdf_bytes, content_type='application/pdf')
    inline = request.GET.get('preview') == '1' or request.GET.get('inline') == '1'
    disp = 'inline' if inline else 'attachment'
    resp['Content-Disposition'] = f'{disp}; filename="ABTC_Patient_{pid}_{datetime.now().strftime("%Y%m%d")}.pdf"'
    resp['X-Frame-Options'] = 'SAMEORIGIN'
    return resp


# ---- Vital Signs — dynamic admin input ----
@login_required
def patient_vital_create(request, pk):
    """Admin dynamically inputs vital signs for a patient — same layout as print grid."""
    if getattr(request.user, 'role', '') == 'patient':
        messages.error(request, 'Not authorized to input vital signs.')
        return redirect('patients:patient_detail', pk=pk)
    patient = get_object_or_404(Patient, pk=pk)
    # Support HTMX or normal POST
    if request.method == 'POST':
        form = VitalSignForm(request.POST)
        if form.is_valid():
            vital = form.save(commit=False)
            vital.patient = patient
            vital.taken_by = request.user
            vital.save()
            AuditLog.objects.create(user=request.user, action='CREATE_VITALS', description=f'Added vitals for {patient.patient_number} ({vital.taken_at:%Y-%m-%d %H:%M})')
            messages.success(request, 'Vital signs saved.')
            nxt = request.POST.get('next') or request.GET.get('next')
            if nxt:
                return redirect(nxt)
            return redirect('patients:patient_detail', pk=patient.pk)
    else:
        form = VitalSignForm()
    # Allow prefill from latest if exists (copy forward)
    latest = patient.get_latest_vitals()
    next_url = request.GET.get('next') or request.POST.get('next') or ''
    context = {
        'form': form,
        'patient': patient,
        'latest_vitals': latest,
        'title': f'Add Vital Signs — {patient.patient_number}',
        'is_create': True,
        'next_url': next_url,
    }
    # If HTMX request, return partial
    if request.headers.get('HX-Request'):
        return render(request, 'patients/vital_form_partial.html', context)
    return render(request, 'patients/vital_form.html', context)


@login_required
def patient_vital_form_print(request, pk):
    """Official blank A4 Vital Signs Form for manual recording — admin only.

    Read-only by design: it renders an empty writable form for the selected
    patient and never creates, pre-fills, or modifies any VitalSign record, so
    printing is safe even before any measurement has been taken.
    """
    if getattr(request.user, 'role', '') != 'admin':
        raise PermissionDenied('Only the System Administrator can print vital signs forms.')
    patient = get_object_or_404(Patient.objects.select_related('barangay'), pk=pk)
    return render(request, 'patients/vital_form_print.html', {'patient': patient})


@login_required
def patient_vital_edit(request, vital_id):
    vital = get_object_or_404(VitalSign.objects.select_related('patient'), pk=vital_id)
    patient = vital.patient
    if getattr(request.user, 'role', '') == 'patient':
        messages.error(request, 'Not authorized.')
        return redirect('patients:patient_detail', pk=patient.pk)
    if request.method == 'POST':
        form = VitalSignForm(request.POST, instance=vital)
        if form.is_valid():
            vital = form.save()
            # keep taken_by as who last edited
            vital.taken_by = request.user
            vital.save(update_fields=['taken_by', 'updated_at'])
            AuditLog.objects.create(user=request.user, action='UPDATE_VITALS', description=f'Updated vitals {vital.pk} for {patient.patient_number}')
            messages.success(request, 'Vital signs updated.')
            nxt = request.POST.get('next') or request.GET.get('next')
            if nxt:
                return redirect(nxt)
            return redirect('patients:patient_detail', pk=patient.pk)
    else:
        form = VitalSignForm(instance=vital)
    next_url = request.GET.get('next') or request.POST.get('next') or ''
    context = {'form': form, 'patient': patient, 'vital': vital, 'title': f'Edit Vital Signs — {patient.patient_number}', 'is_create': False, 'next_url': next_url}
    if request.headers.get('HX-Request'):
        return render(request, 'patients/vital_form_partial.html', context)
    return render(request, 'patients/vital_form.html', context)


@login_required
def patient_vital_delete(request, vital_id):
    vital = get_object_or_404(VitalSign, pk=vital_id)
    patient = vital.patient
    if getattr(request.user, 'role', '') == 'patient':
        messages.error(request, 'Not authorized.')
        return redirect('patients:patient_detail', pk=patient.pk)
    if request.method == 'POST':
        patient_pk = patient.pk
        vital.delete()
        AuditLog.objects.create(user=request.user, action='DELETE_VITALS', description=f'Deleted vitals for {patient.patient_number}')
        messages.success(request, 'Vital signs deleted.')
        return redirect('patients:patient_detail', pk=patient_pk)
        return render(request, 'patients/vital_confirm_delete.html', {'vital': vital, 'patient': patient})


# ---------------------------------------------------------------------------
# QR verification / fast-lookup
# ---------------------------------------------------------------------------

def _patient_qr_payload(patient):
    """Portable identifier embedded in the patient's QR code. Encoding only the
    patient number (not a host URL) keeps the QR valid across localhost, LAN,
    and production — the backend resolves it to the live record on every scan,
    so scanned details are always fresh."""
    from utils.qr import build_patient_payload
    return build_patient_payload(patient)


def _patient_qr_data_uri(patient):
    """Return (data_uri, payload) for a patient's verification QR, or (None, payload)."""
    payload = _patient_qr_payload(patient)
    try:
        from utils.qr import qr_data_uri
        return qr_data_uri(payload), payload
    except Exception:
        return None, payload


# Recognised identifier patterns inside a scanned string.
_PATIENT_NUM_RE = re.compile(r'PAT-[0-9A-Za-z-]+', re.IGNORECASE)
_PREREG_NUM_RE = re.compile(r'AB-\d{8}-\d{3,}', re.IGNORECASE)
_PATIENT_TOKEN_RE = re.compile(r'ABTC-PATIENT:\s*([0-9A-Za-z-]+)', re.IGNORECASE)
_PRE_REG_TOKEN_RE = re.compile(r'ABTC-PRE-REG:\s*([0-9A-Za-z-]+)', re.IGNORECASE)
# Internal app area roots a scanned QR is allowed to redirect to (same-origin only).
_SAFE_INTERNAL_ROOTS = ('/patients', '/vaccination', '/bite_cases', '/doctor', '/reports', '/pre-register')


def _resolve_pre_registration(number):
    """Map a pre-registration number to the freshest record: once converted,
    scanning leads to the official patient record (which always carries the
    current data); before conversion it opens the online registration detail
    page, where staff can review and convert it to an official case."""
    try:
        from pre_registrations.models import PreRegistration
        pr = (PreRegistration.objects
              .filter(pre_registration_number__iexact=number)
              .select_related('converted_patient').first())
        if not pr:
            return None
        if pr.converted_patient_id:
            return reverse('patients:patient_detail', args=[pr.converted_patient_id])
        return reverse('pre_registrations:staff_detail', args=[pr.pk])
    except Exception:
        return None


def _resolve_scan_target(request, code, _depth=0):
    """Map a scanned string to an internal redirect path, or None if unresolved."""
    if not code:
        return None
    code = code.strip()
    host = request.get_host().split(':')[0]
    parsed = urlparse(code) if '://' in code else None
    same_origin = bool(parsed and parsed.netloc and parsed.netloc.split(':')[0] == host)
    # 0) One of our own scan-resolver URLs — resolve the embedded ?code= once
    #    instead of redirecting to ourselves in a loop.
    if same_origin and parsed.path == reverse('patients:scan_resolve'):
        from urllib.parse import parse_qs
        embedded = (parse_qs(parsed.query).get('code') or [''])[0].strip()
        if embedded and _depth < 3:
            return _resolve_scan_target(request, embedded, _depth + 1)
        return None
    # 1) Explicit app tokens (host-independent, survive printing).
    m = _PATIENT_TOKEN_RE.search(code)
    if m:
        patient = Patient.objects.filter(patient_number__iexact=m.group(1)).first()
        if patient:
            return reverse('patients:patient_detail', args=[patient.pk])
    m = _PRE_REG_TOKEN_RE.search(code)
    if m:
        target = _resolve_pre_registration(m.group(1))
        if target:
            return target
    # 2) A bare patient number (PAT-#####) anywhere in the string.
    m = _PATIENT_NUM_RE.search(code)
    if m:
        patient = Patient.objects.filter(patient_number__iexact=m.group(0)).first()
        if patient:
            return reverse('patients:patient_detail', args=[patient.pk])
    # 3) A pre-registration number (AB-YYYYMMDD-NNNN) anywhere — including inside
    #    an absolute status URL printed before the record was converted.
    m = _PREREG_NUM_RE.search(code)
    if m:
        target = _resolve_pre_registration(m.group(0))
        if target:
            return target
    # 4) Any other full URL pointing at this same host — only follow it when it
    #    lands in a recognised app area.
    if same_origin:
        path = parsed.path or '/'
        if path == '/':
            return '/'
        for root in _SAFE_INTERNAL_ROOTS:
            if path == root or path.startswith(root + '/'):
                return path + (('?' + parsed.query) if parsed.query else '')
        return None  # same-origin but unrecognised area (e.g. /accounts, /admin)
    return None


@login_required
def scan_resolve(request):
    """Resolve a scanned QR value (via ?code=...) to the matching record.

    The camera scanner simply GETs `/patients/scan/?code=<raw>`; this view figures
    out what the code means and redirects to the verified, live record (patient
    detail or pre-registration), or shows a friendly result page when it can't
    be matched."""
    code = (request.GET.get('code') or '').strip()
    target = _resolve_scan_target(request, code)
    if target:
        try:
            AuditLog.objects.create(
                user=request.user,
                action='QR_SCAN_RESOLVED',
                description=f'QR scan "{code[:80]}" resolved to {target}',
            )
        except Exception:
            pass
        return redirect(target)
    return render(request, 'patients/scan_result.html', {'code': code, 'found': False})

