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
        patient = Patient.objects.create(
            patient_number=generate_patient_number(),
            account=pre_registration.account,
            first_name=pre_registration.first_name,
            middle_name=pre_registration.middle_name,
            last_name=pre_registration.last_name,
            suffix=pre_registration.suffix,
            birthdate=pre_registration.birthdate,
            sex=pre_registration.sex,
            contact_number=pre_registration.contact_number,
            address=pre_registration.address,
            barangay=barangay,
            parent_guardian=pre_registration.guardian_name,
            emergency_contact_name=pre_registration.emergency_contact_name,
            emergency_contact_number=pre_registration.emergency_contact_number,
            remarks=pre_registration.remarks,
            created_by=user,
        )
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
    pre_registration.status = PreRegistration.Status.CONVERTED
    pre_registration.verified_by = user
    pre_registration.verified_at = timezone.now()
    pre_registration.converted_patient = patient
    pre_registration.converted_case = case
    pre_registration.save(update_fields=[
        "status",
        "verified_by",
        "verified_at",
        "converted_patient",
        "converted_case",
        "updated_at",
    ])
    AuditLog.objects.create(
        user=user,
        action="CONVERT_PRE_REGISTRATION",
        description=f"Converted pre-registration {pre_registration.pre_registration_number} to case {case.case_number}",
    )
    return patient, case
