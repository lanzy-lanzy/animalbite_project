from django.shortcuts import render
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db.models import Q
from django.urls import reverse

from bite_cases.models import AnimalBiteCase
from patients.models import Patient
from pre_registrations.models import PreRegistration

from .models import AuditLog

RETRIEVE_ROUTES = {
    'patient': 'patients:patient_retrieve',
    'bite_case': 'bite_cases:case_retrieve',
    'pre_registration': 'pre_registrations:staff_retrieve',
}


def is_admin(user):
    return user.is_authenticated and user.role == 'admin'

def can_view_audit(user):
    """Admins see the whole trail; doctors get their own activity only."""
    return user.is_authenticated and user.role in ('admin', 'doctor')


def _attach_retrieve_urls(page):
    """Give archive entries a Retrieve action while their record stays archived."""
    logs = list(page.object_list)
    ids = {target_type: set() for target_type in RETRIEVE_ROUTES}
    for log in logs:
        if log.target_type in ids and log.target_id:
            ids[log.target_type].add(log.target_id)

    archived = {
        'patient': set(Patient.objects.filter(
            pk__in=ids['patient'], is_archived=True).values_list('pk', flat=True)),
        'bite_case': set(AnimalBiteCase.objects.filter(
            pk__in=ids['bite_case'], case_status='archived').values_list('pk', flat=True)),
        'pre_registration': set(PreRegistration.objects.filter(
            pk__in=ids['pre_registration'], is_archived=True).values_list('pk', flat=True)),
    }

    for log in logs:
        log.retrieve_url = ''
        if log.target_id in archived.get(log.target_type, ()):
            log.retrieve_url = reverse(
                RETRIEVE_ROUTES[log.target_type], args=[log.target_id])
    page.object_list = logs


@login_required
@user_passes_test(can_view_audit)
def audit_log_list(request):
    query = request.GET.get('q', '')
    own_only = request.user.role != 'admin'
    logs = AuditLog.objects.all()
    if own_only:
        logs = logs.filter(user=request.user)
    if query:
        logs = logs.filter(Q(user__username__icontains=query) | Q(action__icontains=query) | Q(description__icontains=query))
    paginator = Paginator(logs.order_by('-created_at'), 50)
    page = paginator.get_page(request.GET.get('page'))
    if not own_only:
        _attach_retrieve_urls(page)
    return render(request, 'audit/audit_list.html', {
        'logs': page,
        'query': query,
        'own_only': own_only,
    })
