from io import BytesIO

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog

from .forms import PatientSignupPreRegistrationForm, StaffPreRegistrationForm, StatusLookupForm
from .models import PreRegistration
from .services import convert_to_official_case


def is_authorized_staff(user):
    return user.is_authenticated and user.role in {"admin", "encoder", "health_worker", "doctor", "nurse"}


def public_create(request):
    # If logged-in patient on GET, redirect to direct case report (no need to refill personal info)
    if request.method == 'GET' and request.user.is_authenticated and getattr(request.user, 'role', '') == 'patient':
        try:
            if hasattr(request.user, 'patient_profile') and request.user.patient_profile:
                return redirect('pre_registrations:patient_report')
        except Exception:
            pass
    account = request.user if request.user.is_authenticated and request.user.role == "patient" else None
    if request.method == "POST":
        form = PatientSignupPreRegistrationForm(request.POST, account=account)
        if form.is_valid():
            record = form.save()
            if not request.user.is_authenticated:
                login(request, form.account, backend="django.contrib.auth.backends.ModelBackend")
            messages.success(request, "Your account and pre-registration are ready. You can now track treatment online.")
            return redirect("pre_registrations:public_success", number=record.pre_registration_number)
    else:
        form = PatientSignupPreRegistrationForm(account=account)
    return render(request, "pre_registrations/public_form.html", {"form": form, "existing_account": account})


@login_required
def patient_report_bite(request):
    """For logged-in patients: report another bite directly as a case without refilling personal info."""
    if getattr(request.user, 'role', '') != 'patient':
        messages.info(request, "Please use the public pre-registration form.")
        return redirect('pre_registrations:public_create')
    try:
        patient = request.user.patient_profile
    except Exception:
        messages.error(request, "No patient profile found. Your account is not linked to a patient record. Please contact the health office.")
        return redirect('accounts:patient_portal')
    if not patient:
        messages.error(request, "No patient profile found.")
        return redirect('accounts:patient_portal')

    from bite_cases.forms import PatientBiteReportForm
    from bite_cases.models import AnimalBiteCase
    from patients.models import Patient as PatientModel

    # Helper to generate case number
    def _gen_case():
        last = AnimalBiteCase.objects.filter(case_number__startswith='ABC-').order_by('-id').first()
        return f"ABC-{int(last.case_number.split('-')[1]) + 1:05d}" if last else "ABC-00001"

    if request.method == 'POST':
        form = PatientBiteReportForm(request.POST)
        if form.is_valid():
            case = form.save(commit=False)
            case.patient = patient
            case.case_number = _gen_case()
            case.created_by = request.user
            # Also set patient contact for SMS fallback? Already linked
            case.save()
            # Auto-create schedule for SMS pipeline
            try:
                from vaccination.schedule_utils import ensure_vaccination_schedule
                schedule, created = ensure_vaccination_schedule(case, created_by=request.user)
                if created:
                    AuditLog.objects.create(user=request.user, action='AUTO_CREATE_SCHEDULE', description=f'Auto-created schedule for patient-reported case {case.case_number}')
            except Exception:
                pass
            AuditLog.objects.create(user=request.user, action='PATIENT_REPORT_BITE', description=f'Patient {patient.patient_number} reported new bite as {case.case_number}')
            messages.success(request, f'Bite reported successfully as {case.case_number}. Staff will review and your vaccine schedule is already prepared. Check My Treatment for doses.')
            return redirect('accounts:patient_portal')
    else:
        form = PatientBiteReportForm()
    return render(request, 'pre_registrations/patient_report.html', {'form': form, 'patient': patient})


def public_success(request, number):
    record = get_object_or_404(PreRegistration, pre_registration_number=number)
    return render(request, "pre_registrations/public_success.html", {"record": record})


def public_slip(request, number):
    record = get_object_or_404(PreRegistration, pre_registration_number=number)
    return render(request, "pre_registrations/slip.html", {"record": record, "public": True})


def public_status(request):
    form = StatusLookupForm(request.POST or request.GET or None)
    record = None
    missing = False
    if form.is_bound and form.is_valid():
        number = form.cleaned_data["pre_registration_number"].strip().upper()
        record = PreRegistration.objects.filter(pre_registration_number=number).first()
        missing = record is None
    return render(
        request,
        "pre_registrations/public_status.html",
        {"form": form, "record": record, "missing": missing},
    )


def qr_code_svg(request, number):
    record = get_object_or_404(PreRegistration, pre_registration_number=number)
    payload = request.build_absolute_uri(record.qr_code or reverse("pre_registrations:public_success", args=[number]))
    try:
        import qrcode
        import qrcode.image.svg

        image = qrcode.make(payload, image_factory=qrcode.image.svg.SvgPathImage)
        stream = BytesIO()
        image.save(stream)
        return HttpResponse(stream.getvalue(), content_type="image/svg+xml")
    except Exception:
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="220" height="220" viewBox="0 0 220 220">'
            '<rect width="220" height="220" fill="white"/>'
            '<rect x="20" y="20" width="180" height="180" fill="none" stroke="black" stroke-width="8"/>'
            f'<text x="110" y="112" font-size="12" text-anchor="middle" fill="black">{record.pre_registration_number}</text>'
            "</svg>"
        )
        return HttpResponse(svg, content_type="image/svg+xml")


@login_required
@user_passes_test(is_authorized_staff)
def staff_list(request):
    query = request.GET.get("q", "")
    status = request.GET.get("status", "")
    barangay = request.GET.get("barangay", "")
    submitted_date = request.GET.get("date", "")
    records = PreRegistration.objects.all()
    if query:
        records = records.filter(
            Q(pre_registration_number__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(contact_number__icontains=query)
        )
    if status:
        records = records.filter(status=status)
    if barangay:
        records = records.filter(barangay__icontains=barangay)
    if submitted_date:
        records = records.filter(submitted_at__date=submitted_date)

    paginator = Paginator(records, 20)
    page = paginator.get_page(request.GET.get("page"))
    return render(
        request,
        "pre_registrations/staff_list.html",
        {
            "records": page,
            "statuses": PreRegistration.Status.choices,
            "query": query,
            "selected_status": status,
            "selected_barangay": barangay,
            "selected_date": submitted_date,
        },
    )


@login_required
@user_passes_test(is_authorized_staff)
def staff_detail(request, pk):
    record = get_object_or_404(PreRegistration, pk=pk)
    return render(
        request,
        "pre_registrations/staff_detail.html",
        {"record": record, "duplicates": record.possible_duplicates()},
    )


@login_required
@user_passes_test(is_authorized_staff)
def staff_edit(request, pk):
    record = get_object_or_404(PreRegistration, pk=pk)
    if request.method == "POST":
        form = StaffPreRegistrationForm(request.POST, instance=record)
        if form.is_valid():
            updated = form.save()
            AuditLog.objects.create(
                user=request.user,
                action="UPDATE_PRE_REGISTRATION",
                description=f"Updated pre-registration {updated.pre_registration_number}",
            )
            messages.success(request, "Pre-registration updated.")
            return redirect("pre_registrations:staff_detail", pk=record.pk)
    else:
        form = StaffPreRegistrationForm(instance=record)
    return render(request, "pre_registrations/staff_form.html", {"form": form, "record": record})


@login_required
@user_passes_test(is_authorized_staff)
def mark_duplicate(request, pk):
    record = get_object_or_404(PreRegistration, pk=pk)
    record.status = PreRegistration.Status.DUPLICATE
    record.save(update_fields=["status", "updated_at"])
    AuditLog.objects.create(
        user=request.user,
        action="MARK_PRE_REGISTRATION_DUPLICATE",
        description=f"Marked pre-registration {record.pre_registration_number} as duplicate",
    )
    messages.warning(request, "Pre-registration marked as duplicate.")
    return redirect("pre_registrations:staff_detail", pk=record.pk)


@login_required
@user_passes_test(is_authorized_staff)
def mark_no_show(request, pk):
    record = get_object_or_404(PreRegistration, pk=pk)
    record.status = PreRegistration.Status.NO_SHOW
    record.save(update_fields=["status", "updated_at"])
    AuditLog.objects.create(
        user=request.user,
        action="MARK_PRE_REGISTRATION_NO_SHOW",
        description=f"Marked pre-registration {record.pre_registration_number} as no show",
    )
    messages.warning(request, "Pre-registration marked as no show.")
    return redirect("pre_registrations:staff_detail", pk=record.pk)


@login_required
@user_passes_test(is_authorized_staff)
def convert(request, pk):
    record = get_object_or_404(PreRegistration, pk=pk)
    duplicates = record.possible_duplicates()
    if duplicates.exists() and request.POST.get("confirm_duplicates") != "1":
        return render(
            request,
            "pre_registrations/convert_confirm.html",
            {"record": record, "duplicates": duplicates},
        )

    patient, case = convert_to_official_case(record, request.user)
    # If auto-created login (patient had no account), surface temp credentials
    # No SMS is sent for pre-registration credentials — patient already provided
    # username/password during public signup; staff shares temp creds verbally if needed.
    temp_user = getattr(patient, "_temp_username", None)
    temp_pass = getattr(patient, "_temp_password", None)
    if temp_user and temp_pass:
        request.session[f'temp_creds_{patient.pk}'] = {
            'username': temp_user,
            'password': temp_pass,
            'created': True,
        }
        messages.success(request, f"Pre-registration converted to {case.case_number} and patient login created: {temp_user} / Temp: {temp_pass} — please ask patient to change password after login (no SMS sent for credentials).")
        # Also store for case_detail display
        request.session['temp_creds_last'] = {'patient_pk': patient.pk, 'username': temp_user, 'password': temp_pass}
    else:
        messages.success(request, f"Pre-registration converted to official case {case.case_number}.")
    return redirect("bite_cases:case_detail", pk=case.pk)


@login_required
@user_passes_test(is_authorized_staff)
def staff_slip(request, pk):
    record = get_object_or_404(PreRegistration, pk=pk)
    return render(request, "pre_registrations/slip.html", {"record": record, "public": False})
