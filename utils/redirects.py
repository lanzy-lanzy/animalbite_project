from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme


def redirect_back(request, fallback):
    """Return to the posted `next` page when it is safe, else the fallback URL name."""
    target = request.POST.get('next') or ''
    if not url_has_allowed_host_and_scheme(
        target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        target = fallback
    return redirect(target)
