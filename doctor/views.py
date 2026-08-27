from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q, Count
from django.core.paginator import Paginator
from django.utils import timezone
from django.db import transaction

from patients.models import Patient
from patients.forms import PatientForm
from bite_cases.models import AnimalBiteCase
from .models import ClinicalAssessment
from .forms import ClinicalAssessmentForm
from audit.models import AuditLog
from vaccination.models import VaccineDose
from django.db.models import Sum

def is_doctor_user(user):
    return user.is_authenticated and user.role in ['doctor', 'health_worker', 'admin']

def doctor_required(view_func):
    from django.contrib.auth.decorators import user_passes_test
    return user_passes_test(lambda u: is_doctor_user(u), login_url='accounts:login')(view_func)


@login_required
@doctor_required
def dashboard(request):
    today = timezone.localdate()
    # Stats
    total_patients = Patient.objects.filter(is_archived=False).count()
    total_cases = AnimalBiteCase.objects.count()
    pending_assess = AnimalBiteCase.objects.filter(clinical_assessment__isnull=True).count()
    draft_assess = ClinicalAssessment.objects.filter(status='draft').count()
    final_today = ClinicalAssessment.objects.filter(updated_at__date=today, status='final').count()
    # Category breakdown
    cat_counts = ClinicalAssessment.objects.values('category_confirmed').annotate(c=Count('id'))
    cat_map = {r['category_confirmed']: r['c'] for r in cat_counts}
    # Queue: recent cases without assessment or draft
    queue_qs = AnimalBiteCase.objects.select_related('patient').prefetch_related('clinical_assessment').order_by('-created_at')[:12]
    # Mark pending if no assessment or draft — handle reverse OneToOne safely
    for c in queue_qs:
        try:
            ca = c.clinical_assessment
        except ClinicalAssessment.DoesNotExist:
            ca = None
        c.needs_assess = ca is None or ca.status == 'draft'
        # also attach for template reuse
        c._cached_assessment = ca

    recent_assessments = ClinicalAssessment.objects.select_related('patient', 'bite_case', 'assessed_by').order_by('-updated_at')[:10]

    # Upcoming doses for doctor awareness
    upcoming_doses = VaccineDose.objects.filter(dose_status='scheduled', scheduled_date__gte=today).count()

    context = {
        'total_patients': total_patients,
        'total_cases': total_cases,
        'pending_assess': pending_assess,
        'draft_assess': draft_assess,
        'final_today': final_today,
        'cat_map': cat_map,
        'queue': queue_qs,
        'recent_assessments': recent_assessments,
        'upcoming_doses': upcoming_doses,
    }
    return render(request, 'doctor/dashboard.html', context)


@login_required
@doctor_required
def queue_list(request):
    q = request.GET.get('q', '')
    status = request.GET.get('status', '')  # pending, draft, final, all
    category = request.GET.get('category', '')
    cases = AnimalBiteCase.objects.select_related('patient').all()
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
    qs = ClinicalAssessment.objects.select_related('patient', 'bite_case', 'assessed_by').order_by('-updated_at')
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
def assess_case(request, pk):
    """
    Unified Doctor Encounter: edit Patient info + conduct clinical assessment for a specific bite case.
    This is the extended patient information sheet where doctor inputs findings.
    """
    bite_case = get_object_or_404(AnimalBiteCase.objects.select_related('patient__barangay', 'patient__account'), pk=pk)
    patient = bite_case.patient

    # Get or prepare assessment
    assessment = getattr(bite_case, 'clinical_assessment', None)
    # Do not auto-create on GET; create blank for form initial
    if assessment is None:
        assessment = ClinicalAssessment(patient=patient, bite_case=bite_case, assessed_by=request.user)

    # Prepare forms with prefixes to avoid collision
    if request.method == 'POST':
        patient_form = PatientForm(request.POST, instance=patient, prefix='patient')
        form = ClinicalAssessmentForm(request.POST, instance=assessment, prefix='assess')
        # Validate both
        patient_valid = patient_form.is_valid()
        assess_valid = form.is_valid()
        if patient_valid and assess_valid:
            with transaction.atomic():
                patient_saved = patient_form.save()
                obj = form.save(commit=False)
                obj.patient = patient_saved
                obj.bite_case = bite_case
                obj.assessed_by = request.user
                # handle finalized status
                if obj.status == 'final':
                    obj.is_finalized = True
                obj.save()
                # Sync exposure classification category if doctor confirmed category
                if obj.category_confirmed:
                    from bite_cases.models import ExposureClassification
                    exp, _ = ExposureClassification.objects.get_or_create(bite_case=bite_case, defaults={'category': obj.category_confirmed, 'classified_by': request.user})
                    # map assessment category to exposure format (already same keys)
                    if exp.category != obj.category_confirmed:
                        exp.category = obj.category_confirmed
                        exp.classified_by = request.user
                        exp.save()
                    # Also update treatment plan fields if provided
                    if obj.treatment_plan:
                        exp.treatment_plan = obj.treatment_plan
                        exp.save()
                # Audit
                AuditLog.objects.create(user=request.user, action='DOCTOR_ASSESSMENT', description=f'Doctor assessment for {patient.patient_number} case {bite_case.case_number} - {obj.get_category_confirmed_display() if obj.category_confirmed else "draft"}')
                messages.success(request, f'Clinical assessment saved for {patient.full_name()} ({bite_case.case_number}). {"Finalized & signed." if obj.is_finalized else "Draft saved."}')
                # Update case status if finalized
                if obj.is_finalized and bite_case.case_status == 'new':
                    bite_case.case_status = 'under_treatment'
                    bite_case.save(update_fields=['case_status'])
                return redirect('doctor:assess_case', pk=bite_case.pk)
        else:
            if not patient_valid:
                messages.error(request, 'Patient information has errors - please correct.')
            if not assess_valid:
                messages.error(request, 'Assessment form has errors - check required fields.')
    else:
        patient_form = PatientForm(instance=patient, prefix='patient')
        # Prefill assessment from bite_case if new
        if assessment.pk is None:
            # Prefill verified fields from bite_case
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

    # Vital signs for context
    latest_vitals = patient.get_latest_vitals()
    recent_vitals = list(patient.vital_signs.select_related('taken_by').order_by('-taken_at')[:3])
    # History of assessments for this patient
    history = ClinicalAssessment.objects.filter(patient=patient).select_related('bite_case','assessed_by').order_by('-encounter_date')[:5]

    return render(request, 'doctor/assess_form.html', {
        'patient': patient,
        'bite_case': bite_case,
        'patient_form': patient_form,
        'form': form,
        'assessment': assessment,
        'latest_vitals': latest_vitals,
        'recent_vitals': recent_vitals,
        'history': history,
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
    assessment = ClinicalAssessment.objects.filter(patient=patient, bite_case__isnull=True).order_by('-created_at').first()
    if assessment is None:
        assessment = ClinicalAssessment(patient=patient, bite_case=None, assessed_by=request.user)

    if request.method == 'POST':
        patient_form = PatientForm(request.POST, instance=patient, prefix='patient')
        form = ClinicalAssessmentForm(request.POST, instance=assessment, prefix='assess')
        if patient_form.is_valid() and form.is_valid():
            with transaction.atomic():
                p = patient_form.save()
                obj = form.save(commit=False)
                obj.patient = p
                obj.bite_case = None
                obj.assessed_by = request.user
                if obj.status == 'final':
                    obj.is_finalized = True
                obj.save()
                AuditLog.objects.create(user=request.user, action='DOCTOR_ASSESSMENT', description=f'Doctor general assessment for {p.patient_number}')
                messages.success(request, f'Clinical assessment saved for {p.full_name()}.')
                return redirect('doctor:assess_patient', pk=p.pk)
    else:
        patient_form = PatientForm(instance=patient, prefix='patient')
        form = ClinicalAssessmentForm(instance=assessment, prefix='assess')

    latest_vitals = patient.get_latest_vitals()
    return render(request, 'doctor/assess_form.html', {
        'patient': patient,
        'bite_case': None,
        'patient_form': patient_form,
        'form': form,
        'assessment': assessment,
        'latest_vitals': latest_vitals,
        'recent_vitals': list(patient.vital_signs.order_by('-taken_at')[:3]),
        'history': ClinicalAssessment.objects.filter(patient=patient).order_by('-encounter_date')[:5],
        'is_new': assessment.pk is None,
    })
