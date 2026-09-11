from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from .models import AnimalBiteCase, ExposureClassification, MedicalNote
from .forms import AnimalBiteCaseForm, ExposureClassificationForm, MedicalNoteForm
from audit.models import AuditLog

def generate_case_number():
    last = AnimalBiteCase.objects.filter(case_number__startswith='ABC-').order_by('-id').first()
    if last:
        num = int(last.case_number.split('-')[1]) + 1
    else:
        num = 1
    return f"ABC-{num:05d}"

@login_required
def case_list(request):
    query = request.GET.get('q', '')
    status = request.GET.get('status', '')
    animal = request.GET.get('animal', '')
    cases_qs = AnimalBiteCase.objects.all()

    if query:
        cases_qs = cases_qs.filter(Q(case_number__icontains=query) | Q(patient__first_name__icontains=query) | Q(patient__last_name__icontains=query))
    if status:
        cases_qs = cases_qs.filter(case_status=status)
    if animal:
        cases_qs = cases_qs.filter(animal_type=animal)

    paginator = Paginator(cases_qs.order_by('-created_at'), 20)
    page = paginator.get_page(request.GET.get('page'))

    return render(request, 'bite_cases/case_list.html', {
        'cases': page, 'query': query, 'selected_status': status, 'selected_animal': animal,
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
            # Auto-create vaccination schedule so SMS reminders work end-to-end
            try:
                from vaccination.schedule_utils import ensure_vaccination_schedule
                schedule, created = ensure_vaccination_schedule(case, created_by=request.user)
                if created:
                    AuditLog.objects.create(user=request.user, action='AUTO_CREATE_SCHEDULE', description=f'Auto-created vaccination schedule for {case.case_number}')
            except Exception:
                pass
            AuditLog.objects.create(user=request.user, action='CREATE_CASE', description=f'Created bite case {case.case_number}')
            messages.success(request, f'Case {case.case_number} created successfully. Vaccination schedule auto-created — SMS reminders will trigger via RHUDumingag.')
            return redirect('bite_cases:case_detail', pk=case.pk)
    else:
        form = AnimalBiteCaseForm()
    return render(request, 'bite_cases/case_form.html', {'form': form, 'title': 'New Animal Bite Case'})

@login_required
def case_detail(request, pk):
    case = get_object_or_404(AnimalBiteCase.objects.select_related('patient__account'), pk=pk)
    # Check for one-time temp credentials from pre-reg conversion (stored via patient pk)
    temp_creds = None
    # Try patient-specific key
    key = f'temp_creds_{case.patient_id}'
    if key in request.session:
        temp_creds = request.session.pop(key)
    else:
        # Fallback generic last (for redirects)
        last = request.session.get('temp_creds_last')
        if last and str(last.get('patient_pk')) == str(case.patient_id):
            temp_creds = request.session.pop('temp_creds_last')
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
    return render(request, 'bite_cases/case_detail.html', {'case': case, 'temp_creds': temp_creds, 'clinical_assessment': clinical_assessment})

@login_required
def case_edit(request, pk):
    case = get_object_or_404(AnimalBiteCase, pk=pk)
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
    case = get_object_or_404(AnimalBiteCase, pk=pk)
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
    if request.method == 'POST':
        form = MedicalNoteForm(request.POST)
        if form.is_valid():
            note = form.save(commit=False)
            note.bite_case = case
            note.created_by = request.user
            note.save()
            messages.success(request, 'Medical note added.')
    return redirect('bite_cases:case_detail', pk=pk)
