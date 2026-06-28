from settings_app.models import SystemSetting

def settings_context(request):
    settings_dict = {}
    try:
        for s in SystemSetting.objects.all():
            settings_dict[s.key] = s.value
    except Exception:
        pass
    return {"system_settings": settings_dict}
