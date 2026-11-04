"""Permanent patient deletion with a medical-record retention safeguard.

Deleting a patient removes every record the clinic owns for that patient in one
transaction. Records the clinic must retain — dispensed medicines, administered
vaccine doses, and signed clinical assessments — block the operation entirely
instead of being silently discarded; the admin can archive the patient instead.

Inventory lots, stock movements, and audit logs are never deleted here.
"""

from django.db import transaction
from django.db.models import Q

from audit.models import AuditLog


class PatientDeletionBlocked(Exception):
    """Raised when protected records make permanent deletion unlawful."""

    def __init__(self, labels, summary):
        self.labels = labels
        self.summary = summary
        super().__init__('; '.join(labels))


def count_label(count, singular, plural=None):
    plural = plural or f'{singular}s'
    return f'{count} {singular if count == 1 else plural}'


def _related_pre_registrations(patient, case_ids=None):
    """Online registrations owned by this patient.

    A registration is linked through the converted patient, the converted bite
    case, or the patient portal account. It is never matched by name.
    """
    from pre_registrations.models import PreRegistration

    if case_ids is None:
        case_ids = list(patient.bite_cases.values_list('id', flat=True))
    query = Q(converted_patient=patient)
    if case_ids:
        query |= Q(converted_case_id__in=case_ids)
    if patient.account_id:
        query |= Q(account_id=patient.account_id)
    return PreRegistration.objects.filter(query)


def patient_record_summary(patient):
    """Counts used by the confirmation modal, retention check, and audit log."""
    from doctor.models import ClinicalAssessment, Prescription
    from vaccination.models import VaccinationSchedule, VaccineDose

    dispensed_prescriptions = Prescription.objects.filter(patient=patient).filter(
        Q(status='dispensed') | Q(dispensed_at__isnull=False)
    )
    administered_doses = VaccineDose.objects.filter(
        schedule__bite_case__patient=patient
    ).filter(
        Q(actual_date__isnull=False) | Q(dose_status='completed')
    )
    signed_assessments = ClinicalAssessment.objects.filter(patient=patient).filter(
        Q(status='final') | Q(is_finalized=True)
    )
    return {
        'bite_cases': patient.bite_cases.count(),
        'assessments': ClinicalAssessment.objects.filter(patient=patient).count(),
        'vaccination_records': VaccinationSchedule.objects.filter(
            bite_case__patient=patient).count(),
        'doses': VaccineDose.objects.filter(schedule__bite_case__patient=patient).count(),
        'prescriptions': Prescription.objects.filter(patient=patient).count(),
        'vitals': patient.vital_signs.count(),
        'sms_logs': patient.sms_logs.count(),
        'online_registrations': _related_pre_registrations(patient).count(),
        'dispensed_prescriptions': dispensed_prescriptions.count(),
        'administered_doses': administered_doses.count(),
        'signed_assessments': signed_assessments.count(),
    }


def protected_record_labels(summary):
    """Human-readable protected records; empty means permanent deletion is allowed."""
    labels = []
    if summary['signed_assessments']:
        labels.append(count_label(summary['signed_assessments'], 'signed clinical assessment'))
    if summary['dispensed_prescriptions']:
        labels.append(count_label(summary['dispensed_prescriptions'], 'dispensed prescription'))
    if summary['administered_doses']:
        labels.append(count_label(summary['administered_doses'], 'administered vaccine dose'))
    return labels


def deletion_summary_text(summary):
    return ', '.join([
        count_label(summary['bite_cases'], 'bite case'),
        count_label(summary['assessments'], 'clinical assessment'),
        count_label(summary['vaccination_records'], 'vaccination schedule'),
        count_label(summary['doses'], 'dose record'),
        count_label(summary['prescriptions'], 'prescription'),
        count_label(summary['vitals'], 'vital-sign record'),
        count_label(summary['sms_logs'], 'SMS reminder'),
        count_label(summary['online_registrations'], 'online registration'),
    ])


@transaction.atomic
def delete_patient_records(patient, deleted_by):
    """Delete the patient and all eligible linked records atomically.

    Raises PatientDeletionBlocked before touching anything when protected
    records exist. Deleting the Patient row cascades through bite cases,
    assessments, schedules/doses, prescriptions, follow-ups, observations,
    medical notes, SMS logs, and vital signs; online registrations and the
    portal account are removed explicitly.
    """
    patient = type(patient).objects.select_for_update().get(pk=patient.pk)
    summary = patient_record_summary(patient)
    blockers = protected_record_labels(summary)
    if blockers:
        raise PatientDeletionBlocked(blockers, summary)

    patient_number = patient.patient_number
    account = patient.account
    image_name = patient.profile_image.name if patient.profile_image else ''
    image_storage = patient._meta.get_field('profile_image').storage
    case_ids = list(patient.bite_cases.values_list('id', flat=True))

    pre_registrations = _related_pre_registrations(patient, case_ids)
    pre_registrations.delete()

    patient.delete()

    # The portal login is patient-owned; remove it so the QR login and any
    # remaining session stop resolving.
    if account is not None and account.role == 'patient':
        account.delete()

    AuditLog.objects.create(
        user=deleted_by,
        action='DELETE_PATIENT',
        description=(
            f'Permanently deleted patient {patient_number} by admin '
            f'{deleted_by.username}. Removed: {deletion_summary_text(summary)}. '
            'Inventory transaction history and the audit trail were preserved.'
        ),
    )

    if image_name:
        transaction.on_commit(lambda: image_storage.delete(image_name))

    return summary
