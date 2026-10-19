from django.conf import settings
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
from utils.qr import qr_png_bytes, build_pre_registration_payload

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
            messages.success(request, "Your account and online registration are ready. You can now track treatment online.")
            return redirect("pre_registrations:public_success", number=record.pre_registration_number)
    else:
        form = PatientSignupPreRegistrationForm(account=account)
    return render(request, "pre_registrations/public_form.html", {"form": form, "existing_account": account})


@login_required
def patient_report_bite(request):
    """For logged-in patients: report another bite directly as a case without refilling personal info."""
    if getattr(request.user, 'role', '') != 'patient':
        messages.info(request, "Please use the public online registration form.")
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
            # NOTE: the vaccination schedule is NOT auto-created. A nurse sets
            # it after assessing the case, so doses are never dated from an
            # unset consultation date.
            AuditLog.objects.create(user=request.user, action='PATIENT_REPORT_BITE', description=f'Patient {patient.patient_number} reported new bite as {case.case_number}')
            messages.success(request, f'Bite reported successfully as {case.case_number}. Staff will review it and a nurse will set your vaccine schedule. Check My Treatment for updates.')
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


def qr_code(request, number):
    """Serve the pre-registration slip QR as a spec-compliant PNG.

    Payload policy (fixes codes that scanned but opened the wrong machine):
    - With a canonical public SITE_URL configured, encode the absolute status
      page URL so any external phone camera opens the live registration
      status directly.
    - Otherwise encode the portable `ABTC-PRE-REG:<number>` token, which the
      in-app scanner resolves against the live database on every scan.
    The current request host is never baked in — slips generated on localhost
    used to embed http://localhost:8000/... and were useless off the machine.
    """
    record = get_object_or_404(PreRegistration, pre_registration_number=number)
    site_url = getattr(settings, "SITE_URL", "")
    if site_url:
        payload = f"{site_url.rstrip('/')}/{reverse('pre_registrations:public_success', args=[record.pre_registration_number]).lstrip('/')}"
    else:
        payload = build_pre_registration_payload(record.pre_registration_number)
    import qrcode.constants
    png = qr_png_bytes(payload, error_correction=qrcode.constants.ERROR_CORRECT_Q)
    return HttpResponse(png, content_type="image/png")


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
                description=f"Updated online registration {updated.pre_registration_number}",
            )
            messages.success(request, "Online registration updated.")
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
        description=f"Marked online registration {record.pre_registration_number} as duplicate",
    )
    messages.warning(request, "Online registration marked as duplicate.")
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
        description=f"Marked online registration {record.pre_registration_number} as no show",
    )
    messages.warning(request, "Online registration marked as no show.")
    return redirect("pre_registrations:staff_detail", pk=record.pk)


@login_required
@user_passes_test(is_authorized_staff)
def verify_console(request, pk):
    """Scan-verification console — the landing page when a pre-registration QR
    is scanned (see patients.scan_resolve). Re-reads the freshest row from the
    database on every visit and puts Approve / Reject / Convert in one view.
    Already-converted records surface the live patient record instead, so the
    staff always act on current data."""
    record = get_object_or_404(
        PreRegistration.objects.select_related("converted_patient", "converted_case", "verified_by"),
        pk=pk,
    )
    duplicates = record.possible_duplicates()[:5]
    is_converted = bool(record.converted_case_id or record.converted_patient_id)
    can_act = not is_converted and record.status != PreRegistration.Status.CANCELLED
    return render(
        request,
        "pre_registrations/staff_verify.html",
        {
            "record": record,
            "duplicates": duplicates,
            "can_act": can_act,
            "is_converted": is_converted,
            "already_verified": record.status == PreRegistration.Status.VERIFIED,
        },
    )


@login_required
@user_passes_test(is_authorized_staff)
def verify_approve(request, pk):
    """POST-only: approve (verify) a pre-registration from the console."""
    record = get_object_or_404(PreRegistration, pk=pk)
    if request.method != "POST":
        return redirect("pre_registrations:verify", pk=record.pk)
    if record.converted_case_id or record.converted_patient_id:
        messages.info(request, "Already converted to an official case — open the patient record for current data.")
        return redirect("pre_registrations:verify", pk=record.pk)
    record.status = PreRegistration.Status.VERIFIED
    record.verified_by = request.user
    record.verified_at = timezone.now()
    record.save(update_fields=["status", "verified_by", "verified_at", "updated_at"])
    AuditLog.objects.create(
        user=request.user,
        action="VERIFY_PRE_REGISTRATION",
        description=f"Verified online registration {record.pre_registration_number} via scan console",
    )
    messages.success(request, "Online registration approved (verified). You can now convert it to an official case.")
    return redirect("pre_registrations:verify", pk=record.pk)


@login_required
@user_passes_test(is_authorized_staff)
def verify_reject(request, pk):
    """POST-only: reject a pre-registration (with an optional reason that is
    appended to remarks and mirrored in the audit trail)."""
    record = get_object_or_404(PreRegistration, pk=pk)
    if request.method != "POST":
        return redirect("pre_registrations:verify", pk=record.pk)
    if record.converted_case_id or record.converted_patient_id:
        messages.error(request, "Converted online registrations cannot be rejected — amend the official record instead.")
        return redirect("pre_registrations:verify", pk=record.pk)
    reason = (request.POST.get("reason") or "").strip()[:300]
    record.status = PreRegistration.Status.CANCELLED
    if reason:
        stamp = timezone.localdate()
        record.remarks = f"{record.remarks}\n[Rejected {stamp}] {reason}".strip()
    record.save(update_fields=["status", "remarks", "updated_at"])
    AuditLog.objects.create(
        user=request.user,
        action="REJECT_PRE_REGISTRATION",
        description=f"Rejected online registration {record.pre_registration_number}" + (f" — {reason}" if reason else ""),
    )
    messages.warning(request, "Online registration rejected." + (f" Reason recorded: {reason}" if reason else ""))
    return redirect("pre_registrations:staff_list")


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
        messages.success(request, f"Online registration converted to {case.case_number} and patient login created: {temp_user} / Temp: {temp_pass} — please ask patient to change password after login (no SMS sent for credentials).")
        # Also store for case_detail display
        request.session['temp_creds_last'] = {'patient_pk': patient.pk, 'username': temp_user, 'password': temp_pass}
    else:
        messages.success(request, f"Online registration converted to official case {case.case_number}.")
    messages.info(request, "Vaccination schedule is not set automatically — a nurse must trigger it from the case page or Nurse Portal.")
    return redirect("bite_cases:case_detail", pk=case.pk)


@login_required
@user_passes_test(is_authorized_staff)
def staff_slip(request, pk):
    record = get_object_or_404(PreRegistration, pk=pk)
    return render(request, "pre_registrations/slip.html", {"record": record, "public": False})
