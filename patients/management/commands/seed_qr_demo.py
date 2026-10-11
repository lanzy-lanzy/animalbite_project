"""
Seed fresh demo data for testing QR scanning.

Creates three scannable records so every QR path can be verified end-to-end:
  1. A brand-new patient (with a linked login account)  -> patient verification QR.
  2. A submitted pre-registration                        -> pre-reg QR (staff verify).
  3. A pre-registration already converted to the patient -> same QR, now resolves to
     the live patient record (proves the fresh-data sync path).

Run with:  python manage.py seed_qr_demo
"""
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.urls import reverse
from django.utils import timezone

from patients.models import Barangay, Patient, VitalSign
from patients.views import generate_patient_number
from pre_registrations.models import PreRegistration
from utils.credentials import create_patient_user
from utils.qr import build_patient_payload, build_pre_registration_payload


class Command(BaseCommand):
    help = "Seed a fresh patient + pre-registrations for QR scan testing."

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=1, help="How many patients to seed (default 1).")

    def handle(self, *args, **options):
        count = options["count"]
        barangay, _ = Barangay.objects.get_or_create(name="Dumingag (Demo)")

        made = []
        for _ in range(count):
            patient = self._make_patient(barangay)
            self._make_vitals(patient)
            pre_submitted = self._make_pre_registration(patient, converted=False)
            pre_converted = self._make_pre_registration(patient, converted=True)
            made.append((patient, pre_submitted, pre_converted))

        self.stdout.write(self.style.SUCCESS(f"Seeded {count} patient(s) with demo pre-registrations.\n"))
        self.stdout.write("Scan targets (open the page, then use the in-app 'Scan QR' button):\n")
        for patient, pre_submitted, pre_converted in made:
            patient_detail = reverse("patients:patient_detail", args=[patient.pk])
            staff_detail = reverse("pre_registrations:staff_detail", args=[pre_submitted.pk])
            self.stdout.write(f"\n== {patient.full_name()} ({patient.patient_number}) ==")
            self.stdout.write(f"  Patient QR page   : {patient_detail}  (QR shown top-right)")
            self.stdout.write(f"    payload token   : {build_patient_payload(patient)}")
            self.stdout.write(f"    -> scans to     : {patient_detail}")
            self.stdout.write(f"  Pre-reg (submitted) page: {staff_detail}")
            self.stdout.write(f"    payload token   : {build_pre_registration_payload(pre_submitted.pre_registration_number)}")
            self.stdout.write(f"    -> scans to     : staff verification {staff_detail}")
            self.stdout.write(f"  Pre-reg (CONVERTED) token: {build_pre_registration_payload(pre_converted.pre_registration_number)}")
            self.stdout.write(f"    -> scans to     : LIVE patient record {patient_detail}  (fresh-data sync)")

    # -- helpers -----------------------------------------------------------

    def _make_patient(self, barangay):
        patient = Patient.objects.create(
            patient_number=generate_patient_number(),
            first_name="Rex",
            middle_name="Demo",
            last_name="Del Rosario",
            birthdate=date(1995, 3, 14),
            sex="male",
            civil_status="single",
            contact_number="09171234567",
            address="Purok 1, Dumingag, Zamboanga del Sur",
            barangay=barangay,
            medical_history="Seeded demo record for QR scan testing.",
            created_by=None,
        )
        # Link a login account so the one-time login QR is also available on the detail page.
        create_patient_user(patient)
        return patient

    def _make_vitals(self, patient):
        VitalSign.objects.create(
            patient=patient,
            taken_at=timezone.now() - timedelta(hours=1),
            bp_systolic=120,
            bp_diastolic=80,
            temperature=36.8,
            pulse_rate=78,
            respiratory_rate=18,
            spo2=98,
            weight=68.0,
            height=170.0,
            general_appearance="well",
            notes="Seeded demo vitals.",
        )

    def _make_pre_registration(self, patient, converted):
        base = {
            "first_name": patient.first_name,
            "middle_name": patient.middle_name,
            "last_name": patient.last_name,
            "birthdate": patient.birthdate,
            "sex": patient.sex,
            "contact_number": patient.contact_number,
            "address": patient.address,
            "barangay": "Dumingag (Demo)",
            "bite_datetime": timezone.now() - timedelta(days=1),
            "incident_place": "Backyard, Purok 1",
            "incident_barangay": "Dumingag (Demo)",
            "animal_type": "dog",
            "animal_ownership": "owned",
            "animal_vaccination_status": "unknown",
            "animal_condition": "under_observation",
            "exposure_type": "bite",
            "body_part_affected": "right hand",
            "number_of_wounds": 1,
            "wound_washed": True,
            "consent_given": True,
        }
        if converted:
            record = PreRegistration.objects.create(
                **base,
                status=PreRegistration.Status.CONVERTED,
                converted_patient=patient,
            )
        else:
            record = PreRegistration.objects.create(
                **base,
                status=PreRegistration.Status.SUBMITTED,
            )
        return record
