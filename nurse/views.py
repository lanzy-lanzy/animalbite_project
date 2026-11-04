from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET

from functools import wraps


def is_nurse_user(user):
    """RBAC: only users with role == 'nurse' may use the Nurse Portal."""
    return user.is_authenticated and getattr(user, 'role', '') == 'nurse'


def nurse_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.urls import reverse
            from urllib.parse import quote
            login_url = reverse('accounts:login')
            return redirect(f"{login_url}?next={quote(request.get_full_path())}")
        if getattr(request.user, 'role', '') != 'nurse':
            messages.error(request, 'Not authorized — Nurse Portal is restricted to Nurse role.')
            return redirect('dashboard:index')
        return view_func(request, *args, **kwargs)
    return _wrapped


@login_required
@nurse_required
def dashboard(request):
    """Nurse Portal hub — the 8 nurse use cases in one simple screen."""
    from vaccination.models import SMSLog, VaccineDose
    from inventory.models import VaccineInventory
    from bite_cases.models import AnimalBiteCase

    today = date.today()

    due_today = VaccineDose.objects.filter(
        dose_status='scheduled', scheduled_date=today
    ).select_related('schedule__bite_case__patient').order_by('schedule__bite_case__case_number')[:10]
    due_today_count = VaccineDose.objects.filter(
        dose_status='scheduled', scheduled_date=today
    ).count()
    missed_count = VaccineDose.objects.filter(dose_status='missed').count()
    pending_scheduling_count = VaccineDose.objects.filter(dose_status='pending').count()
    upcoming_count = VaccineDose.objects.filter(
        dose_status='scheduled', scheduled_date__gt=today
    ).count()
    sms_pending = SMSLog.objects.filter(status__in=['pending', 'queued']).count()
    sms_failed = SMSLog.objects.filter(status='failed').count()
    low_stock = VaccineInventory.objects.filter(quantity_available__lte=5).count()

    # Cases with no vaccination schedule yet — the nurse sets these.
    awaiting_schedule = (
        AnimalBiteCase.objects
        .filter(vaccination_schedule__isnull=True)
        .select_related('patient')
        .order_by('-created_at')[:10]
    )
    awaiting_schedule_count = AnimalBiteCase.objects.filter(
        vaccination_schedule__isnull=True
    ).count()

    context = {
        'due_today': due_today,
        'due_today_count': due_today_count,
        'missed_count': missed_count,
        'pending_scheduling_count': pending_scheduling_count,
        'upcoming_count': upcoming_count,
        'sms_pending': sms_pending,
        'sms_failed': sms_failed,
        'low_stock': low_stock,
        'today': today,
        'awaiting_schedule': awaiting_schedule,
        'awaiting_schedule_count': awaiting_schedule_count,
    }
    return render(request, 'nurse/dashboard.html', context)


@login_required
@nurse_required
@require_GET
def doctor_finding_detail(request, case_pk):
    """Nurse Portal: the doctor's saved findings for one bite case, read-only.

    The page is case-scoped so records from another bite case of the same
    patient are never mixed in. It renders exactly what is stored and never
    writes patient, assessment, dose, prescription, or inventory data.
    """
    from bite_cases.models import AnimalBiteCase
    from vaccination.views import build_record_context

    bite_case = get_object_or_404(
        AnimalBiteCase.objects.select_related(
            'patient__barangay', 'patient__created_by', 'created_by',
        ),
        pk=case_pk,
    )
    if bite_case.is_archived:
        messages.error(
            request,
            'This bite case has been archived and is available to the System Administrator only.',
        )
        return redirect('bite_cases:case_list')

    context = build_record_context(bite_case)
    context.update({'nurse_view': True, 'cases': [bite_case]})
    return render(request, 'vaccination/record_detail.html', context)
