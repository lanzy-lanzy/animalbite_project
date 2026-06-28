from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from .models import Patient, Barangay
from .forms import PatientForm
from audit.models import AuditLog

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
    sex = request.GET.get('sex', '')
    patients_qs = Patient.objects.filter(is_archived=False)

    if query:
        patients_qs = patients_qs.filter(
            Q(first_name__icontains=query) | Q(last_name__icontains=query) |
            Q(patient_number__icontains=query) | Q(contact_number__icontains=query)
        )
    if barangay:
        patients_qs = patients_qs.filter(barangay_id=barangay)
    if sex:
        patients_qs = patients_qs.filter(sex=sex)

    paginator = Paginator(patients_qs.order_by('-created_at'), 20)
    page = paginator.get_page(request.GET.get('page'))
    barangays = Barangay.objects.filter(is_active=True)

    return render(request, 'patients/patient_list.html', {
        'patients': page, 'barangays': barangays, 'query': query,
        'selected_barangay': barangay, 'selected_sex': sex,
    })

@login_required
def patient_create(request):
    if request.method == 'POST':
        form = PatientForm(request.POST)
        if form.is_valid():
            patient = form.save(commit=False)
            patient.patient_number = generate_patient_number()
            patient.created_by = request.user
            patient.save()
            AuditLog.objects.create(user=request.user, action='CREATE_PATIENT', description=f'Created patient {patient.patient_number} - {patient.full_name()}')
            messages.success(request, f'Patient {patient.patient_number} created successfully.')
            return redirect('patients:patient_detail', pk=patient.pk)
    else:
        form = PatientForm()
    return render(request, 'patients/patient_form.html', {'form': form, 'title': 'Register New Patient'})

@login_required
def patient_detail(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    bite_cases = patient.bite_cases.all().order_by('-created_at') if hasattr(patient, 'bite_cases') else []
    return render(request, 'patients/patient_detail.html', {'patient': patient, 'bite_cases': bite_cases})

@login_required
def patient_edit(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    if request.method == 'POST':
        form = PatientForm(request.POST, instance=patient)
        if form.is_valid():
            form.save()
            AuditLog.objects.create(user=request.user, action='UPDATE_PATIENT', description=f'Updated patient {patient.patient_number}')
            messages.success(request, 'Patient updated successfully.')
            return redirect('patients:patient_detail', pk=patient.pk)
    else:
        form = PatientForm(instance=patient)
    return render(request, 'patients/patient_form.html', {'form': form, 'title': 'Edit Patient'})
