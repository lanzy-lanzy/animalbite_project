from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

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

    today = date.today()

    due_today = VaccineDose.objects.filter(
        dose_status='scheduled', scheduled_date=today
    ).select_related('schedule__bite_case__patient').order_by('schedule__bite_case__case_number')[:10]
    due_today_count = VaccineDose.objects.filter(
        dose_status='scheduled', scheduled_date=today
    ).count()
    missed_count = VaccineDose.objects.filter(dose_status='missed').count()
    upcoming_count = VaccineDose.objects.filter(
        dose_status='scheduled', scheduled_date__gt=today
    ).count()
    sms_pending = SMSLog.objects.filter(status__in=['pending', 'queued']).count()
    sms_failed = SMSLog.objects.filter(status='failed').count()
    low_stock = VaccineInventory.objects.filter(quantity_available__lte=5).count()

    context = {
        'due_today': due_today,
        'due_today_count': due_today_count,
        'missed_count': missed_count,
        'upcoming_count': upcoming_count,
        'sms_pending': sms_pending,
        'sms_failed': sms_failed,
        'low_stock': low_stock,
        'today': today,
    }
    return render(request, 'nurse/dashboard.html', context)
