from settings_app.models import SystemSetting

def settings_context(request):
    settings_dict = {}
    try:
        for s in SystemSetting.objects.all():
            settings_dict[s.key] = s.value
    except Exception:
        pass
    return {"system_settings": settings_dict}


def doctor_notifications(request):
    """Real counts behind the Doctor Portal bell: unclaimed cases and my drafts."""
    user = getattr(request, 'user', None)
    if not (user and user.is_authenticated and getattr(user, 'role', '') == 'doctor'):
        return {}
    from bite_cases.models import AnimalBiteCase
    from doctor.models import ClinicalAssessment

    queue_count = AnimalBiteCase.objects.filter(clinical_assessment__isnull=True).count()
    draft_count = ClinicalAssessment.objects.filter(assessed_by=user, status='draft').count()
    return {
        'bell_queue': queue_count,
        'bell_drafts': draft_count,
        'bell_total': queue_count + draft_count,
    }


def inventory_notifications(request):
    """Inventory alerts behind the Nurse Portal bell (vaccines + medicines)."""
    user = getattr(request, 'user', None)
    if not (user and user.is_authenticated and getattr(user, 'role', '') == 'nurse'):
        return {}
    from inventory.notifications import get_inventory_alerts

    alerts = get_inventory_alerts()
    return {
        'inventory_alerts': alerts,
        'inventory_alert_total': len(alerts),
    }
