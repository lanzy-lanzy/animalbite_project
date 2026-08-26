from django.shortcuts import redirect
from django.urls import reverse


class PatientPortalAccessMiddleware:
    """Keep patient accounts inside patient-facing routes."""

    ALLOWED_PREFIXES = (
        "/patient-portal/",
        "/pre-register/",
        "/profile/",
        "/logout/",
        "/static/",
        "/media/",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if (
            user.is_authenticated
            and user.role == "patient"
            and not request.path.startswith(self.ALLOWED_PREFIXES)
        ):
            return redirect(reverse("accounts:patient_portal"))
        return self.get_response(request)
