from django.shortcuts import render
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db.models import Q
from .models import AuditLog

def is_admin(user):
    return user.is_authenticated and user.role == 'admin'

@login_required
@user_passes_test(is_admin)
def audit_log_list(request):
    query = request.GET.get('q', '')
    logs = AuditLog.objects.all()
    if query:
        logs = logs.filter(Q(user__username__icontains=query) | Q(action__icontains=query) | Q(description__icontains=query))
    paginator = Paginator(logs.order_by('-created_at'), 50)
    page = paginator.get_page(request.GET.get('page'))
    return render(request, 'audit/audit_list.html', {'logs': page, 'query': query})
