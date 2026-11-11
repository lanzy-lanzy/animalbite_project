"""
Vaccination schedule helpers.

Schedules are not created when a bite case is created or converted — dose dates
must never start from an unset consultation date. They are materialised either
by a nurse/admin explicitly (vaccination.views.schedule_trigger) or when a
doctor signs a PEP assessment (ClinicalAssessment.save), which uses the
protocol and Day 0 recorded on that assessment.

These helpers are the single source of truth for the PEP day offsets and for
materialising a schedule + its doses.
"""
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
        # Stored UTC; take the clinic-local calendar day so early-morning
        # consultations are not dated a day earlier.
        value = case.date_time_consultation
        if timezone.is_naive(value):
            value = timezone.make_aware(value, timezone.get_current_timezone())
        return timezone.localtime(value).date()
    return timezone.localdate()


def _offset(days, position):
    if position <= len(days):
        return days[position - 1]
    return None


def _dose_label(position, offset):
    return 'Day %s' % offset if offset is not None else 'Dose %d' % position


def _dose_count(value, days):
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = 0
    if count < 1:
        count = len(days)
    return max(1, min(count, 5))


def prescribed_minimum_date(dose):
    """Return a dose's earliest date only when a finalized doctor chose a regimen."""
    from datetime import timedelta

    from doctor.models import ClinicalAssessment

    assessment = ClinicalAssessment.objects.filter(
        bite_case_id=dose.schedule.bite_case_id,
        is_finalized=True,
    ).first()
    if not assessment or not assessment.dose_protocol:
        return None

    dose_ids = list(dose.schedule.doses.order_by('id').values_list('id', flat=True))
    try:
        position = dose_ids.index(dose.pk) + 1
    except ValueError:
        return None
    if position <= 1:
        return None

    protocol_days = ClinicalAssessment.protocol_days(assessment.dose_protocol)
    if position > len(protocol_days):
        return None
    first_dose = dose.schedule.doses.order_by('id').first()
    if not first_dose or not first_dose.scheduled_date:
        return None

    return {
        'date': first_dose.scheduled_date + timedelta(days=protocol_days[position - 1]),
        'day': protocol_days[position - 1],
        'protocol': assessment.get_dose_protocol_display(),
    }


def materialise_doses(schedule, days=None, base_date=None, dose_count=None):
    """Create the dose rows for a schedule that has none.

    Only the 1st dose is dated (Day 0) and marked Scheduled; every other dose
    starts as Pending Scheduling — the nurse assigns those within the protocol.
    """
    from .models import VaccineDose

    if base_date is None:
        base_date = schedule_base_date(schedule.bite_case)
    if days is None:
        days = get_dose_days()
    days = [int(d) for d in days]
    count = _dose_count(dose_count, days)
    doses = []
    for position in range(1, count + 1):
        offset = _offset(days, position)
        doses.append(VaccineDose.objects.create(
            schedule=schedule,
            dose_label=_dose_label(position, offset),
            protocol_day=offset,
            scheduled_date=base_date if position == 1 else None,
            dose_status='scheduled' if position == 1 else 'pending',
        ))
    return doses


def ensure_vaccination_schedule(case, created_by=None, days=None, base_date=None, dose_count=None):
    """
    Create the VaccinationSchedule + dose rows for a case if it has none yet.
    Returns (schedule, created_bool). Safe to call more than once — it is a
    no-op once a schedule exists.

    The doctor's dose count decides how many rows exist.
    """
    from .models import VaccinationSchedule

    try:
        return case.vaccination_schedule, False
    except VaccinationSchedule.DoesNotExist:
        pass

    schedule = VaccinationSchedule.objects.create(
        bite_case=case,
        created_by=created_by,
    )
    materialise_doses(schedule, days=days, base_date=base_date, dose_count=dose_count)
    return schedule, True


def sync_schedule_doses(schedule, dose_count, first_dose_date=None, days=None):
    """Reconcile an existing schedule with the doctor's selected dose count.

    New positions become Pending Scheduling rows, never pre-dated. The 1st
    dose keeps the doctor's confirmed date and Completed/cancelled history is
    never rewritten. Dropping the count only removes untouched pending rows.
    """
    from .models import VaccineDose

    if schedule is None:
        return []
    if days is None:
        days = get_dose_days()
    days = [int(d) for d in days]
    count = _dose_count(dose_count, days)
    doses = list(schedule.doses.order_by('id'))
    touched = []

    for position in range(1, count + 1):
        offset = _offset(days, position)
        if position <= len(doses):
            dose = doses[position - 1]
            changed = []
            if dose.protocol_day is None and offset is not None:
                dose.protocol_day = offset
                changed.append('protocol_day')
            if position == 1 and first_dose_date and dose.dose_status not in ('completed', 'cancelled'):
                if dose.scheduled_date != first_dose_date:
                    dose.scheduled_date = first_dose_date
                    changed.append('scheduled_date')
                if dose.dose_status == 'pending':
                    dose.dose_status = 'scheduled'
                    changed.append('dose_status')
            if changed:
                dose.save(update_fields=changed + ['updated_at'])
            touched.append(dose)
        else:
            touched.append(VaccineDose.objects.create(
                schedule=schedule,
                dose_label=_dose_label(position, offset),
                protocol_day=offset,
                scheduled_date=first_dose_date if position == 1 else None,
                dose_status='scheduled' if position == 1 and first_dose_date else 'pending',
            ))

    for dose in doses[count:]:
        if dose.dose_status == 'pending' and dose.scheduled_date is None:
            dose.delete()
    return touched
