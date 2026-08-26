"""
Helper to auto-create vaccination schedule for a bite case.
Used by bite_cases case_create and pre_registrations conversion to ensure
every case has a schedule so SMS reminders can trigger automatically.
"""
from datetime import timedelta
from django.utils import timezone

def ensure_vaccination_schedule(case, created_by=None):
    """
    Ensure a VaccinationSchedule exists for the given AnimalBiteCase.
    If not, create it with default doses based on SystemSetting 'vaccination_days'.
    Returns (schedule, created_bool)
    """
    from .models import VaccinationSchedule, VaccineDose
    from settings_app.models import SystemSetting

    try:
        return case.vaccination_schedule, False
    except VaccinationSchedule.DoesNotExist:
        pass

    schedule = VaccinationSchedule.objects.create(
        bite_case=case,
        created_by=created_by,
    )
    # Determine days
    days = [0, 3, 7, 14, 28]
    try:
        days_str_obj = SystemSetting.objects.filter(key='vaccination_days').first()
        if days_str_obj and days_str_obj.value:
            parsed = [int(d.strip()) for d in days_str_obj.value.split(',') if d.strip().isdigit() or (d.strip().lstrip('-').isdigit())]
            # Allow empty? Keep default if parsing fails
            if parsed:
                # Ensure valid parsing for all entries
                days = [int(d.strip()) for d in days_str_obj.value.split(',')]
    except Exception:
        pass

    base_date = case.date_time_consultation.date() if case.date_time_consultation else timezone.localdate()
    for d in days:
        try:
            di = int(d)
        except ValueError:
            continue
        VaccineDose.objects.create(
            schedule=schedule,
            dose_label=f"Day {di}",
            scheduled_date=base_date + timedelta(days=di),
        )
    return schedule, True
