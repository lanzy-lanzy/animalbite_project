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
    return user.is_authenticated and user.role in {"admin", "encoder", "health_worker", "doctor"}


def public_create(request):
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

    _, case = convert_to_official_case(record, request.user)
    messages.success(request, f"Pre-registration converted to official case {case.case_number}.")
    return redirect("bite_cases:case_detail", pk=case.pk)


@login_required
@user_passes_test(is_authorized_staff)
def staff_slip(request, pk):
    record = get_object_or_404(PreRegistration, pk=pk)
    return render(request, "pre_registrations/slip.html", {"record": record, "public": False})
