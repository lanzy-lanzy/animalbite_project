"""
Vaccination schedule helpers.

Schedules are deliberately NOT created automatically when a bite case is
created or converted. A nurse (or admin) triggers them explicitly via
vaccination.views.schedule_trigger once the case has been assessed, so dose
dates never start from an unset consultation date.

These helpers are the single source of truth for the PEP day offsets and for
materialising a schedule + its doses.
"""
from datetime import timedelta

from django.utils import timezone

# Fallback PEP offsets used when no 'vaccination_days' SystemSetting is set.
DEFAULT_DOSE_DAYS = [0, 3, 7, 14, 28]


def get_dose_days():
    """Dose day offsets from SystemSetting 'vaccination_days', else the PEP default."""
    from settings_app.models import SystemSetting

    days = list(DEFAULT_DOSE_DAYS)
    row = SystemSetting.objects.filter(key='vaccination_days').first()
    if row and row.value:
        try:
            parsed = [int(d.strip()) for d in str(row.value).split(',') if d.strip() != '']
            if parsed:
                days = parsed
        except (ValueError, TypeError):
            pass
    return days


def schedule_base_date(case):
    """Doses are dated from the consultation date, falling back to today."""
    if case.date_time_consultation:
        return case.date_time_consultation.date()
    return timezone.localdate()


def ensure_vaccination_schedule(case, created_by=None):
    """
    Create the VaccinationSchedule + dose rows for a case if it has none yet.
    Returns (schedule, created_bool). Safe to call more than once — it is a
    no-op once a schedule exists.
    """
    from .models import VaccinationSchedule, VaccineDose

    try:
        return case.vaccination_schedule, False
    except VaccinationSchedule.DoesNotExist:
        pass

    schedule = VaccinationSchedule.objects.create(
        bite_case=case,
        created_by=created_by,
    )
    base_date = schedule_base_date(case)
    for d in get_dose_days():
        try:
            di = int(d)
        except (TypeError, ValueError):
            continue
        VaccineDose.objects.create(
            schedule=schedule,
            dose_label='Day %s' % di,
            scheduled_date=base_date + timedelta(days=di),
        )
    return schedule, True
