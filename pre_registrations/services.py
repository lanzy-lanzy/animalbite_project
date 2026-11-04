from django.db import transaction
from django.utils import timezone

from audit.models import AuditLog
from bite_cases.models import AnimalBiteCase
from patients.models import Barangay, Patient

from .models import PreRegistration


def generate_patient_number():
    last = Patient.objects.filter(patient_number__startswith="PAT-").order_by("-id").first()
    sequence = int(last.patient_number.split("-")[1]) + 1 if last else 1
    return f"PAT-{sequence:05d}"


def generate_case_number():
    last = AnimalBiteCase.objects.filter(case_number__startswith="ABC-").order_by("-id").first()
    sequence = int(last.case_number.split("-")[1]) + 1 if last else 1
    return f"ABC-{sequence:05d}"


@transaction.atomic
def convert_to_official_case(pre_registration, user):
    if pre_registration.converted_patient_id and pre_registration.converted_case_id:
        return pre_registration.converted_patient, pre_registration.converted_case

    barangay, _ = Barangay.objects.get_or_create(name=pre_registration.barangay, defaults={"is_active": True})
    patient = None
    if pre_registration.account_id:
        patient = Patient.objects.filter(account=pre_registration.account).first()
    if patient is None:
        # Use guardian contact as fallback for emergency if emergency not provided
        emergency_name = pre_registration.emergency_contact_name or pre_registration.guardian_name
        emergency_number = pre_registration.emergency_contact_number or pre_registration.guardian_contact_number
        patient = Patient.objects.create(
            patient_number=generate_patient_number(),
            account=pre_registration.account,
            first_name=pre_registration.first_name,
            middle_name=pre_registration.middle_name,
            last_name=pre_registration.last_name,
            suffix=pre_registration.suffix,
            birthdate=pre_registration.birthdate,
            sex=pre_registration.sex,
            civil_status=pre_registration.civil_status or "single",
            contact_number=pre_registration.contact_number,
            address=pre_registration.address,
            barangay=barangay,
            parent_guardian=pre_registration.guardian_name,
            emergency_contact_name=emergency_name,
            emergency_contact_number=emergency_number,
            medical_history=pre_registration.medical_history,
            allergy_info=pre_registration.allergy_info,
            previous_rabies_vax=pre_registration.previous_rabies_vax,
            remarks=pre_registration.remarks,
            created_by=user,
        )
        # Auto-create patient login if no account linked (e.g., staff-entered pre-reg)
        # No SMS for credentials here: public pre-registrations already provide their own
        # username/password during signup, so SMS is redundant. Staff can share temp
        # creds verbally via the one-time display on the detail page.
        if patient.account_id is None:
            try:
                from utils.credentials import create_patient_user
                new_user, temp_pwd, _ = create_patient_user(patient, created_by=user)
                # Attach for view to display one-time
                patient._temp_username = new_user.username
                patient._temp_password = temp_pwd
            except Exception:
                pass
    case = AnimalBiteCase.objects.create(
        case_number=generate_case_number(),
        patient=patient,
        date_time_bite=pre_registration.bite_datetime,
        place_of_incident=pre_registration.incident_place,
        incident_barangay=pre_registration.incident_barangay,
        animal_type=pre_registration.animal_type,
        animal_ownership=pre_registration.animal_ownership,
        animal_vax_status=pre_registration.animal_vaccination_status,
        animal_condition=pre_registration.animal_condition,
        exposure_type=pre_registration.exposure_type,
        body_part_affected=pre_registration.body_part_affected,
        number_of_wounds=pre_registration.number_of_wounds,
        wound_washed_immediately=pre_registration.wound_washed,
        first_aid_given=pre_registration.first_aid_given,
        initial_consultation_notes=pre_registration.remarks,
        created_by=user,
    )
    # NOTE: the vaccination schedule is intentionally NOT created here.
    # A nurse triggers it once the case has been assessed.
    # Link account if we just auto-created one for patient
    if pre_registration.account_id is None and patient.account_id:
        pre_registration.account = patient.account
    pre_registration.status = PreRegistration.Status.CONVERTED
    pre_registration.verified_by = user
    pre_registration.verified_at = timezone.now()
    pre_registration.converted_patient = patient
    pre_registration.converted_case = case
    # Include account in update if we set it
    update_fields = [
        "status",
        "verified_by",
        "verified_at",
        "converted_patient",
        "converted_case",
        "updated_at",
    ]
    if pre_registration.account_id and "account" not in update_fields:
        # Need to include account if it was just set
        update_fields = ["account"] + update_fields
        pre_registration.save(update_fields=update_fields)
    else:
        pre_registration.save(update_fields=update_fields)
    AuditLog.objects.create(
        user=user,
        action="CONVERT_PRE_REGISTRATION",
        description=f"Converted online registration {pre_registration.pre_registration_number} to case {case.case_number}",
    )
    return patient, case


PENDING_STATUSES = (
    PreRegistration.Status.SUBMITTED,
    PreRegistration.Status.FOR_VERIFICATION,
)


class RegistrationDeletionBlocked(Exception):
    def __init__(self, labels):
        self.labels = labels
        super().__init__("; ".join(labels))


def registration_retention_blockers(record):
    """Why this registration must be retained instead of permanently deleted.

    Only unconverted pending submissions may be erased. Anything approved,
    linked to an official patient/case, or already finalised is an intake
    record the clinic keeps, so it can only be archived.
    """
    labels = []
    if record.converted_patient_id:
        labels.append("linked patient profile")
    if record.converted_case_id:
        labels.append("official bite case")
    if record.status == PreRegistration.Status.VERIFIED:
        labels.append("approved registration awaiting conversion")
    elif record.status not in PENDING_STATUSES:
        labels.append(f"{record.get_status_display().lower()} registration")
    return labels


@transaction.atomic
def delete_pre_registration(record, deleted_by):
    record = PreRegistration.objects.select_for_update().get(pk=record.pk)
    blockers = registration_retention_blockers(record)
    if blockers:
        raise RegistrationDeletionBlocked(blockers)
    number = record.pre_registration_number
    record.delete()
    AuditLog.objects.create(
        user=deleted_by,
        action="DELETE_PRE_REGISTRATION",
        description=(
            f"Permanently deleted online registration {number} by admin "
            f"{deleted_by.username}. Linked patient and clinical records were preserved."
        ),
    )
    return number


@transaction.atomic
def archive_pre_registration(record, archived_by):
    record = PreRegistration.objects.select_for_update().get(pk=record.pk)
    already_archived = record.is_archived
    if not already_archived:
        record.is_archived = True
        record.save(update_fields=["is_archived", "updated_at"])
        AuditLog.objects.create(
            user=archived_by,
            action="ARCHIVE_PRE_REGISTRATION",
            target_type="pre_registration",
            target_id=record.pk,
            description=(
                f"Archived online registration {record.pre_registration_number} by admin "
                f"{archived_by.username}. The record was retained and removed from active lists."
            ),
        )
    return already_archived


@transaction.atomic
def retrieve_pre_registration(record, retrieved_by):
    record = PreRegistration.objects.select_for_update().get(pk=record.pk)
    was_archived = record.is_archived
    if was_archived:
        record.is_archived = False
        record.save(update_fields=["is_archived", "updated_at"])
        AuditLog.objects.create(
            user=retrieved_by,
            action="RETRIEVE_PRE_REGISTRATION",
            target_type="pre_registration",
            target_id=record.pk,
            description=(
                f"Retrieved online registration {record.pre_registration_number} by admin "
                f"{retrieved_by.username}. The record was restored to the active list."
            ),
        )
    return was_archived
