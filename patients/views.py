from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.urls import reverse
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
                    # Also try to send SMS with credentials via Semaphore (RHUDumingag)
                    try:
                        from vaccination.sms import send_semaphore_sms
                        from django.conf import settings
                        login_url = request.build_absolute_uri(reverse('accounts:login'))
                        sms_msg = (
                            f"ABTC Dumingag: Hello {patient.first_name}, your patient portal is ready. "
                            f"Username: {username} Temp Password: {temp_password}. "
                            f"Login at {login_url} and change password via Profile > Change Password. -RHUDumingag"
                        )
                        # Ensure not starting with TEST
                        sms_res = send_semaphore_sms(patient.contact_number, sms_msg)
                        if sms_res.get('success'):
                            messages.info(request, f'SMS with login sent to {patient.contact_number} via RHUDumingag.')
                        else:
                            # Fallback still show credentials; SMS failure is not critical
                            if not settings.SEMAPHORE_ENABLED:
                                messages.info(request, f'[DRY-RUN] SMS would be sent to {patient.contact_number} (SEMAPHORE_ENABLED=False).')
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
    bite_cases = patient.bite_cases.select_related().prefetch_related('vaccination_schedule__doses').order_by('-created_at') if hasattr(patient, 'bite_cases') else []
    # One-time temp credentials from session (set after auto-creation)
    temp_creds = request.session.pop(f'temp_creds_{patient.pk}', None)
    # Also check if there's a recent reset stored generically
    if not temp_creds:
        temp_creds = request.session.pop('temp_creds_last', None)
        if temp_creds and str(temp_creds.get('patient_pk')) != str(patient.pk):
            temp_creds = None
    return render(request, 'patients/patient_detail.html', {'patient': patient, 'bite_cases': bite_cases, 'temp_creds': temp_creds})


@login_required
def patient_reset_password(request, pk):
    patient = get_object_or_404(Patient.objects.select_related('account'), pk=pk)
    # Only staff allowed (not patient)
    if getattr(request.user, 'role', '') == 'patient':
        messages.error(request, 'Not authorized.')
        return redirect('patients:patient_detail', pk=patient.pk)
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
                f"Login at {login_url} and change password. -RHUDumingag"
            )
            res = send_semaphore_sms(patient.contact_number, sms_msg)
            if res.get('success'):
                messages.info(request, f'Password reset SMS sent to {patient.contact_number}.')
        except Exception:
            pass
        messages.success(request, f'Password reset for {patient.patient_number}. New temp: {temp} (user: {user.username}) — share securely and ask patient to change via Profile > Change Password.')
        return redirect('patients:patient_detail', pk=patient.pk)
    # GET shows confirm page? For now just redirect with POST required
    messages.info(request, 'Use the Reset button to generate a new temporary password.')
    return redirect('patients:patient_detail', pk=patient.pk)

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
