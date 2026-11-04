from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.views.decorators.http import require_POST
from .models import AnimalBiteCase, ExposureClassification, MedicalNote
from .forms import AnimalBiteCaseForm, ExposureClassificationForm, MedicalNoteForm
from audit.models import AuditLog
from utils.redirects import redirect_back

def generate_case_number():
    last = AnimalBiteCase.objects.filter(case_number__startswith='ABC-').order_by('-id').first()
    if last:
        num = int(last.case_number.split('-')[1]) + 1
    else:
        num = 1
    return f"ABC-{num:05d}"


def _bite_case_dependencies(case):
    """Labels for linked records that must survive a case deletion."""
    from doctor.models import ClinicalAssessment, Prescription
    from inventory.models import MedicineStockMovement, VaccineStockMovement
    from vaccination.models import AnimalObservation, VaccinationSchedule, VaccineDose

    labels = []
    if ClinicalAssessment.objects.filter(bite_case=case).exists():
        labels.append('clinical assessment')
    if ExposureClassification.objects.filter(bite_case=case).exists():
        labels.append('exposure classification')
    if case.medical_notes.exists():
        labels.append('medical notes')
    if VaccinationSchedule.objects.filter(bite_case=case).exists():
        labels.append('vaccination schedule')
    if VaccineDose.objects.filter(
            Q(schedule__bite_case=case),
            Q(dose_status='completed') | Q(actual_date__isnull=False)).exists():
        labels.append('administered vaccine doses')
    if Prescription.objects.filter(bite_case=case).exists():
        labels.append('prescriptions')
    if Prescription.objects.filter(bite_case=case, status='dispensed').exists():
        labels.append('dispensing records')
    if case.follow_ups.exists():
        labels.append('follow-up records')
    if case.sms_logs.exists():
        labels.append('SMS reminder logs')
    if AnimalObservation.objects.filter(bite_case=case).exists():
        labels.append('animal observation records')
    if (MedicineStockMovement.objects.filter(reference=case.case_number).exists()
            or VaccineStockMovement.objects.filter(reference=case.case_number).exists()):
        labels.append('inventory transactions')
    return labels

@login_required
def case_list(request):
    query = request.GET.get('q', '')
    status = request.GET.get('status', '')
    animal = request.GET.get('animal', '')
    is_admin = getattr(request.user, 'role', '') == 'admin'
    cases_qs = AnimalBiteCase.objects.select_related('patient')

    if query:
        cases_qs = cases_qs.filter(Q(case_number__icontains=query) | Q(patient__first_name__icontains=query) | Q(patient__last_name__icontains=query))
    if status:
        if status == 'archived' and not is_admin:
            messages.error(request, 'Archived bite cases are available to the System Administrator only.')
            cases_qs = cases_qs.none()
        else:
            cases_qs = cases_qs.filter(case_status=status)
    else:
        # Archived cases are retained for recordkeeping but leave the active list.
        cases_qs = cases_qs.exclude(case_status='archived')
    if animal:
        cases_qs = cases_qs.filter(animal_type=animal)

    paginator = Paginator(cases_qs.order_by('-created_at'), 20)
    page = paginator.get_page(request.GET.get('page'))

    return render(request, 'bite_cases/case_list.html', {
        'cases': page, 'query': query, 'selected_status': status, 'selected_animal': animal,
        'start_index': page.start_index(),
    })

@login_required
def case_create(request):
    if request.method == 'POST':
        form = AnimalBiteCaseForm(request.POST)
        if form.is_valid():
            case = form.save(commit=False)
            case.case_number = generate_case_number()
            case.created_by = request.user
            case.save()
            # NOTE: the vaccination schedule is NOT auto-created. A nurse must
            # trigger it from the case page or the Nurse Portal, so dosing never
            # starts off an unset/stale consultation date.
            AuditLog.objects.create(user=request.user, action='CREATE_CASE', description=f'Created bite case {case.case_number}')
            messages.success(request, f'Case {case.case_number} created. No vaccination schedule yet - a nurse must set it from the case page or Nurse Portal.')
            return redirect('bite_cases:case_detail', pk=case.pk)
    else:
        form = AnimalBiteCaseForm()
    return render(request, 'bite_cases/case_form.html', {'form': form, 'title': 'New Animal Bite Case'})

@login_required
def case_detail(request, pk):
    case = get_object_or_404(AnimalBiteCase.objects.select_related('patient__account'), pk=pk)
    if case.is_archived and getattr(request.user, 'role', '') != 'admin':
        messages.error(request, 'This bite case has been archived and is available to the System Administrator only.')
        return redirect('bite_cases:case_list')
    # Patient portal credentials are administrator-only. Other roles must not
    # receive account details or one-time credentials through this page.
    can_manage_portal = getattr(request.user, 'role', '') == 'admin'
    temp_creds = None
    if can_manage_portal:
        # Check for one-time temp credentials from pre-reg conversion (stored via patient pk)
        key = f'temp_creds_{case.patient_id}'
        if key in request.session:
            temp_creds = request.session.pop(key)
        else:
            # Fallback generic last (for redirects)
            last = request.session.get('temp_creds_last')
            if last and str(last.get('patient_pk')) == str(case.patient_id):
                temp_creds = request.session.pop('temp_creds_last')
    else:
        request.session.pop(f'temp_creds_{case.patient_id}', None)
        last = request.session.get('temp_creds_last')
        if last and str(last.get('patient_pk')) == str(case.patient_id):
            request.session.pop('temp_creds_last', None)
    # Clinical Assessment (Section 3) for this case — doctor inputs/edits on this record form
    clinical_assessment = None
    try:
        from doctor.models import ClinicalAssessment
        clinical_assessment = ClinicalAssessment.objects.select_related('assessed_by', 'patient').filter(bite_case=case).first()
        if clinical_assessment is None:
            # fallback via reverse relation (safe)
            try:
                ca = case.clinical_assessment
                clinical_assessment = ca
            except Exception:
                pass
    except Exception:
        pass
    return render(request, 'bite_cases/case_detail.html', {'case': case, 'temp_creds': temp_creds, 'can_manage_portal': can_manage_portal, 'clinical_assessment': clinical_assessment})

@login_required
def case_edit(request, pk):
    # Nurses are read-only on bite cases (View Doctor Findings use case)
    if getattr(request.user, 'role', '') in ('patient', 'nurse'):
        messages.error(request, 'Not authorized — nurses have read-only access to bite cases.')
        return redirect('bite_cases:case_detail', pk=pk)
    case = get_object_or_404(AnimalBiteCase, pk=pk)
    if case.is_archived:
        messages.error(request, 'Archived bite cases cannot be edited.')
        if getattr(request.user, 'role', '') == 'admin':
            return redirect('bite_cases:case_detail', pk=pk)
        return redirect('bite_cases:case_list')
    if case.is_locked:
        messages.error(request, 'This case is locked and cannot be edited.')
        return redirect('bite_cases:case_detail', pk=pk)
    if request.method == 'POST':
        form = AnimalBiteCaseForm(request.POST, instance=case)
        if form.is_valid():
            form.save()
            AuditLog.objects.create(user=request.user, action='UPDATE_CASE', description=f'Updated case {case.case_number}')
            messages.success(request, 'Case updated successfully.')
            return redirect('bite_cases:case_detail', pk=case.pk)
    else:
        form = AnimalBiteCaseForm(instance=case)
    return render(request, 'bite_cases/case_form.html', {'form': form, 'title': 'Edit Case'})

@login_required
def classify_exposure(request, pk):
    # Exposure classification is a clinical (doctor) decision — nurses view only
    if getattr(request.user, 'role', '') in ('patient', 'nurse'):
        messages.error(request, 'Not authorized — exposure classification is restricted to doctors.')
        return redirect('bite_cases:case_detail', pk=pk)
    case = get_object_or_404(AnimalBiteCase, pk=pk)
    if case.is_archived:
        messages.error(request, 'Archived bite cases cannot be modified.')
        return redirect('bite_cases:case_detail', pk=pk)
    try:
        exposure = case.exposure
    except ExposureClassification.DoesNotExist:
        exposure = None

    if request.method == 'POST':
        form = ExposureClassificationForm(request.POST, instance=exposure)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.bite_case = case
            obj.classified_by = request.user
            obj.save()
            AuditLog.objects.create(user=request.user, action='CLASSIFY_EXPOSURE', description=f'Classified exposure for {case.case_number} as {obj.get_category_display()}')
            messages.success(request, 'Exposure classification saved.')
            return redirect('bite_cases:case_detail', pk=case.pk)
    else:
        form = ExposureClassificationForm(instance=exposure)
    return render(request, 'bite_cases/exposure_form.html', {'form': form, 'case': case})

@login_required
def add_medical_note(request, pk):
    case = get_object_or_404(AnimalBiteCase, pk=pk)
    if case.is_archived:
        messages.error(request, 'Archived bite cases cannot be modified.')
        return redirect('bite_cases:case_detail', pk=pk)
    if request.method == 'POST':
        form = MedicalNoteForm(request.POST)
        if form.is_valid():
            note = form.save(commit=False)
            note.bite_case = case
            note.created_by = request.user
            note.save()
            messages.success(request, 'Medical note added.')
    return redirect('bite_cases:case_detail', pk=pk)


@login_required
@require_POST
def case_delete(request, pk):
    """Admin-only deletion. Cases with clinical or treatment history are archived
    so assessments, schedules, doses, prescriptions and stock history survive.
    Only cases with no linked records may be removed permanently."""
    if getattr(request.user, 'role', '') != 'admin':
        messages.error(request, 'Only the System Administrator can delete bite case records.')
        return redirect('bite_cases:case_list')

    case = get_object_or_404(AnimalBiteCase.objects.select_related('patient'), pk=pk)
    if case.is_archived:
        messages.info(request, f'Case {case.case_number} is already archived.')
        return redirect('bite_cases:case_list')

    case_number = case.case_number
    patient_name = case.patient.full_name()
    patient_number = case.patient.patient_number
    dependencies = _bite_case_dependencies(case)
    reason = (request.POST.get('reason') or '').strip() or (
        'Admin removal request from the Animal Bite Cases list')

    if dependencies:
        case.pre_archive_status = case.case_status
        case.case_status = 'archived'
        case.save(update_fields=['case_status', 'pre_archive_status', 'updated_at'])
        AuditLog.objects.create(
            user=request.user,
            action='ARCHIVE_BITE_CASE',
            target_type='bite_case',
            target_id=case.pk,
            description=(
                f'Archived bite case {case_number} for patient {patient_name} '
                f'({patient_number}) by admin {request.user.username}. '
                f'Reason: {reason}. Preserved: {", ".join(dependencies)}.'
            ),
        )
        messages.success(
            request,
            f'Case {case_number} ({patient_name}) was archived and removed from the active list. '
            'Linked clinical and treatment records were preserved.',
        )
    else:
        case.delete()
        AuditLog.objects.create(
            user=request.user,
            action='DELETE_BITE_CASE',
            description=(
                f'Permanently deleted bite case {case_number} for patient {patient_name} '
                f'({patient_number}) by admin {request.user.username}. '
                f'Reason: {reason}. No linked clinical or treatment records were found.'
            ),
        )
        messages.success(request, f'Case {case_number} ({patient_name}) was permanently deleted.')
    return redirect('bite_cases:case_list')


@login_required
@require_POST
def case_retrieve(request, pk):
    """Admin-only restore of an archived bite case with its prior status."""
    if getattr(request.user, 'role', '') != 'admin':
        messages.error(request, 'Only the System Administrator can retrieve bite case records.')
        return redirect_back(request, 'audit:audit_list')

    case = get_object_or_404(AnimalBiteCase.objects.select_related('patient'), pk=pk)
    if not case.is_archived:
        messages.info(request, f'Case {case.case_number} is not archived.')
        return redirect_back(request, 'audit:audit_list')

    valid_statuses = {value for value, _ in AnimalBiteCase.CASE_STATUS} - {'archived'}
    restored_status = case.pre_archive_status
    if restored_status not in valid_statuses:
        from doctor.models import ClinicalAssessment
        has_clinical_record = ClinicalAssessment.objects.filter(bite_case=case).exists()
        restored_status = 'under_treatment' if has_clinical_record else 'new'

    case.case_status = restored_status
    case.pre_archive_status = ''
    case.save(update_fields=['case_status', 'pre_archive_status', 'updated_at'])
    AuditLog.objects.create(
        user=request.user,
        action='RETRIEVE_BITE_CASE',
        target_type='bite_case',
        target_id=case.pk,
        description=(
            f'Retrieved bite case {case.case_number} for patient {case.patient.full_name()} '
            f'({case.patient.patient_number}) by admin {request.user.username}. '
            f'Restored to {case.get_case_status_display()}.'
        ),
    )
    messages.success(
        request,
        f'Case {case.case_number} was retrieved and restored to '
        f'{case.get_case_status_display()}.',
    )
    return redirect_back(request, 'audit:audit_list')
