"""Tests for admin-only complete patient deletion.

Permanent deletion removes the patient and every eligible record across the
ABTC modules in one transaction. Records protected by the clinic's retention
policy — dispensed prescriptions, administered vaccine doses, signed clinical
assessments — block deletion so the admin can archive instead.
"""
import datetime
from datetime import date

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from bite_cases.models import AnimalBiteCase
from doctor.models import ClinicalAssessment, Prescription
from inventory.models import MedicineInventory, MedicineStockMovement
from pre_registrations.models import PreRegistration
from vaccination.models import SMSLog, VaccinationSchedule, VaccineDose

from .models import Patient, VitalSign

U = get_user_model()


class PatientDeleteTestCase(TestCase):

    def setUp(self):
        self.admin = U.objects.create_user("admin.one", password="pw12345678", role="admin")
        self.doctor = U.objects.create_user("doc.one", password="pw12345678", role="doctor")
        self.nurse = U.objects.create_user("nurse.one", password="pw12345678", role="nurse")
        self.patient = self._make_patient("PAT-D1001", "Laila")
        self.other_patient = self._make_patient("PAT-D1002", "Oscar")
        self.other_case = self._make_case(self.other_patient, "ABC-D1002")
        self.medicine = MedicineInventory.objects.create(
            medicine_name="Amoxicillin",
            generic_name="Amoxicillin",
            strength="500 mg",
            dosage_form="capsule",
            unit="capsule",
            batch_number="MED-D1001",
            expiration_date=date(2027, 12, 31),
            quantity_received=100,
            quantity_available=100,
        )
        self.portal_user = U.objects.create_user(
            "laila.portal", password="pw12345678", role="patient")
        self.patient.account = self.portal_user
        self.patient.save(update_fields=["account"])
        AuditLog.objects.create(
            user=self.admin,
            action="CREATE_PATIENT",
            description="Existing audit trail must survive",
        )

    def _make_patient(self, number, first_name):
        return Patient.objects.create(
            patient_number=number,
            first_name=first_name,
            last_name="Test",
            birthdate=date(1990, 1, 1),
            contact_number="09170000000",
        )

    def _make_case(self, patient, case_number):
        return AnimalBiteCase.objects.create(
            case_number=case_number,
            patient=patient,
            created_by=self.nurse,
            date_time_bite=timezone.now() - datetime.timedelta(days=1),
            place_of_incident="Home",
            incident_barangay="Poblacion",
            animal_type="dog",
            animal_ownership="owned",
            animal_vax_status="unknown",
            animal_condition="under_observation",
            exposure_type="bite",
            body_part_affected="right hand",
            number_of_wounds=1,
        )

    def _make_pre_registration(self, case):
        return PreRegistration.objects.create(
            first_name="Laila",
            last_name="Test",
            birthdate=date(1990, 1, 1),
            sex="female",
            contact_number="09170000001",
            address="Poblacion",
            barangay="Poblacion",
            medical_history="None",
            allergy_info="None",
            previous_rabies_vax="None",
            bite_datetime=timezone.now() - datetime.timedelta(days=1),
            incident_place="Home",
            incident_barangay="Poblacion",
            animal_type="dog",
            animal_ownership="owned",
            animal_vaccination_status="unknown",
            animal_condition="alive",
            exposure_type="bite",
            body_part_affected="hand",
            number_of_wounds=1,
            consent_given=True,
            status=PreRegistration.Status.CONVERTED,
            account=self.portal_user,
            converted_patient=self.patient,
            converted_case=case,
        )

    def build_patient_records(self):
        """Two bite cases with assessments, schedules, doses, and one of each
        patient-linked record. All are eligible for permanent deletion."""
        case_one = self._make_case(self.patient, "ABC-D1001")
        case_two = self._make_case(self.patient, "ABC-D1003")
        assessment_one = ClinicalAssessment.objects.create(
            patient=self.patient, bite_case=case_one, assessed_by=self.doctor,
            chief_complaint="Dog bite", status="draft",
        )
        assessment_two = ClinicalAssessment.objects.create(
            patient=self.patient, bite_case=case_two, assessed_by=self.doctor,
            chief_complaint="Scratch", status="draft",
        )
        schedule_one = VaccinationSchedule.objects.create(
            bite_case=case_one, created_by=self.doctor)
        schedule_two = VaccinationSchedule.objects.create(
            bite_case=case_two, created_by=self.doctor)
        dose_one = VaccineDose.objects.create(
            schedule=schedule_one, dose_label="Day 0",
            scheduled_date=date(2026, 10, 30), protocol_day=0,
        )
        dose_two = VaccineDose.objects.create(
            schedule=schedule_two, dose_label="Day 0",
            scheduled_date=date(2026, 10, 31), protocol_day=0,
        )
        prescription = Prescription.objects.create(
            assessment=assessment_one, patient=self.patient, bite_case=case_one,
            medicine=self.medicine, quantity=6, status="pending",
        )
        vital = VitalSign.objects.create(
            patient=self.patient, taken_at=timezone.now(),
            bp_systolic=120, bp_diastolic=80, taken_by=self.nurse,
        )
        sms = SMSLog.objects.create(
            dose=dose_one, patient=self.patient, bite_case=case_one,
            reminder_type="on_day", recipient="639170000000",
            message="Vaccination reminder", status="sent",
        )
        pre_registration = self._make_pre_registration(case_one)
        movement = MedicineStockMovement.objects.create(
            medicine=self.medicine, movement_type="dispense", quantity=6,
            reference="ABC-D1001", moved_by=self.admin,
            notes="Preserved inventory transaction",
        )
        return {
            "cases": [case_one, case_two],
            "assessments": [assessment_one, assessment_two],
            "schedules": [schedule_one, schedule_two],
            "doses": [dose_one, dose_two],
            "prescription": prescription,
            "vital": vital,
            "sms": sms,
            "pre_registration": pre_registration,
            "movement": movement,
        }

    def _delete(self, user=None, patient=None, confirm=None, follow=True):
        patient = patient or self.patient
        data = {}
        if confirm is not False:
            data["confirm_patient_number"] = (
                patient.patient_number if confirm is None else confirm)
        self.client.force_login(user or self.admin)
        return self.client.post(
            reverse("patients:patient_delete", args=[patient.pk]), data, follow=follow)

    def _archive(self, user=None, patient=None, follow=True):
        patient = patient or self.patient
        self.client.force_login(user or self.admin)
        return self.client.post(
            reverse("patients:patient_archive", args=[patient.pk]), follow=follow)

    def _messages(self, response):
        return [str(m) for m in response.context["messages"]]


class PatientDeletePermissionTests(PatientDeleteTestCase):

    def test_only_admin_sees_the_delete_controls(self):
        url = reverse("patients:patient_list")
        delete_url = reverse("patients:patient_delete", args=[self.patient.pk])
        for user, visible in ((self.admin, True), (self.doctor, False), (self.nurse, False)):
            self.client.force_login(user)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            html = response.content.decode()
            self.assertEqual(delete_url in html, visible, user.role)
            self.assertEqual(
                "Delete Patient and All Related Records?" in html, visible, user.role)

    def test_non_admin_cannot_delete(self):
        for user in (self.doctor, self.nurse):
            response = self._delete(user=user)
            self.assertRedirects(response, reverse("patients:patient_list"))
            self.assertTrue(Patient.objects.filter(pk=self.patient.pk).exists())
            self.assertFalse(AuditLog.objects.filter(action="DELETE_PATIENT").exists())

    def test_non_admin_cannot_archive(self):
        for user in (self.doctor, self.nurse):
            self._archive(user=user)
            self.patient.refresh_from_db()
            self.assertFalse(self.patient.is_archived)
            self.assertFalse(AuditLog.objects.filter(action="ARCHIVE_PATIENT").exists())

    def test_get_requests_are_rejected(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(
            reverse("patients:patient_delete", args=[self.patient.pk])).status_code, 405)
        self.assertEqual(self.client.get(
            reverse("patients:patient_archive", args=[self.patient.pk])).status_code, 405)
        self.assertTrue(Patient.objects.filter(pk=self.patient.pk).exists())

    def test_login_required(self):
        for name in ("patient_delete", "patient_archive"):
            response = self.client.post(reverse(f"patients:{name}", args=[self.patient.pk]))
            self.assertEqual(response.status_code, 302)
            self.assertIn("/login/", response["Location"])
        self.assertTrue(Patient.objects.filter(pk=self.patient.pk).exists())


class PatientDeleteBehaviourTests(PatientDeleteTestCase):

    def test_permanent_deletion_removes_patient_and_all_eligible_records(self):
        records = self.build_patient_records()
        patient_id = self.patient.pk
        case_ids = [c.pk for c in records["cases"]]

        response = self._delete()

        self.assertRedirects(response, reverse("patients:patient_list"))
        self.assertFalse(Patient.objects.filter(pk=patient_id).exists())
        self.assertFalse(AnimalBiteCase.objects.filter(id__in=case_ids).exists())
        self.assertFalse(ClinicalAssessment.objects.filter(patient_id=patient_id).exists())
        self.assertFalse(VaccinationSchedule.objects.filter(bite_case_id__in=case_ids).exists())
        self.assertFalse(VaccineDose.objects.filter(schedule__bite_case_id__in=case_ids).exists())
        self.assertFalse(Prescription.objects.filter(patient_id=patient_id).exists())
        self.assertFalse(VitalSign.objects.filter(patient_id=patient_id).exists())
        self.assertFalse(SMSLog.objects.filter(
            Q(patient_id=patient_id) | Q(bite_case_id__in=case_ids)).exists())
        self.assertFalse(PreRegistration.objects.filter(
            Q(converted_patient_id=patient_id) | Q(converted_case_id__in=case_ids)).exists())

        # The portal login and QR lookup are revoked with the patient.
        self.assertFalse(U.objects.filter(pk=self.portal_user.pk).exists())
        scan = self.client.get(
            reverse("patients:scan_resolve"), {"code": "ABTC-PATIENT:PAT-D1001"})
        self.assertEqual(scan.status_code, 200)
        self.assertFalse(scan.context["found"])
        self.assertEqual(self.client.get(
            reverse("patients:patient_detail", args=[patient_id])).status_code, 404)
        search = self.client.get(reverse("patients:patient_list"), {"q": "PAT-D1001"})
        self.assertNotContains(search, reverse("patients:patient_detail", args=[patient_id]))
        self.assertContains(search, "No patients found")

        messages = self._messages(response)
        self.assertTrue(any("permanently deleted" in m for m in messages))
        self.assertTrue(any(
            "Inventory and audit history were preserved" in m for m in messages))

    def test_unrelated_patient_and_inventory_are_untouched(self):
        records = self.build_patient_records()
        self._delete()

        self.assertTrue(Patient.objects.filter(pk=self.other_patient.pk).exists())
        self.assertTrue(AnimalBiteCase.objects.filter(pk=self.other_case.pk).exists())
        self.assertTrue(MedicineInventory.objects.filter(pk=self.medicine.pk).exists())
        self.assertTrue(MedicineStockMovement.objects.filter(
            pk=records["movement"].pk).exists())
        self.assertTrue(AuditLog.objects.filter(action="CREATE_PATIENT").exists())

        log = AuditLog.objects.get(action="DELETE_PATIENT")
        self.assertEqual(log.user, self.admin)
        self.assertIn("PAT-D1001", log.description)
        self.assertIn("2 bite cases", log.description)
        self.assertIn("2 clinical assessments", log.description)
        self.assertIn("2 vaccination schedules", log.description)
        self.assertNotIn("Laila", log.description)

    def test_protected_dispensed_prescription_blocks_deletion(self):
        records = self.build_patient_records()
        records["prescription"].status = "dispensed"
        records["prescription"].dispensed_at = timezone.now()
        records["prescription"].save(update_fields=["status", "dispensed_at", "updated_at"])

        response = self._delete()

        self.assertRedirects(response, reverse("patients:patient_list"))
        self.assertTrue(Patient.objects.filter(pk=self.patient.pk).exists())
        self.assertTrue(AnimalBiteCase.objects.filter(patient=self.patient).exists())
        self.assertTrue(Prescription.objects.filter(patient=self.patient).exists())
        self.assertFalse(AuditLog.objects.filter(action="DELETE_PATIENT").exists())
        messages = self._messages(response)
        self.assertTrue(any("medical record-retention policy" in m for m in messages))
        self.assertTrue(any("dispensed prescription" in m for m in messages))
        self.assertTrue(any("Archive" in m for m in messages))

    def test_protected_administered_dose_blocks_deletion(self):
        records = self.build_patient_records()
        dose = records["doses"][0]
        dose.actual_date = date(2026, 10, 30)
        dose.dose_status = "completed"
        dose.save(update_fields=["actual_date", "dose_status", "updated_at"])

        response = self._delete()

        self.assertTrue(Patient.objects.filter(pk=self.patient.pk).exists())
        self.assertTrue(VaccineDose.objects.filter(pk=dose.pk).exists())
        messages = self._messages(response)
        self.assertTrue(any("administered vaccine dose" in m for m in messages))

    def test_protected_signed_assessment_blocks_deletion(self):
        records = self.build_patient_records()
        assessment = records["assessments"][0]
        assessment.status = "final"
        assessment.is_finalized = True
        assessment.save(update_fields=["status", "is_finalized", "updated_at"])

        response = self._delete()

        self.assertTrue(Patient.objects.filter(pk=self.patient.pk).exists())
        self.assertTrue(ClinicalAssessment.objects.filter(pk=assessment.pk).exists())
        messages = self._messages(response)
        self.assertTrue(any("signed clinical assessment" in m for m in messages))

    def test_archive_keeps_protected_records_and_leaves_active_lists(self):
        records = self.build_patient_records()
        records["prescription"].status = "dispensed"
        records["prescription"].dispensed_at = timezone.now()
        records["prescription"].save(update_fields=["status", "dispensed_at", "updated_at"])

        response = self._archive()

        self.assertRedirects(response, reverse("patients:patient_list"))
        self.patient.refresh_from_db()
        self.assertTrue(self.patient.is_archived)
        self.assertTrue(AnimalBiteCase.objects.filter(patient=self.patient).exists())
        self.assertTrue(Prescription.objects.filter(patient=self.patient).exists())
        self.assertContains(
            self.client.get(reverse("patients:patient_detail", args=[self.patient.pk])),
            self.patient.patient_number)
        self.assertNotContains(
            self.client.get(reverse("patients:patient_list")), self.patient.patient_number)

        log = AuditLog.objects.get(action="ARCHIVE_PATIENT")
        self.assertIn("PAT-D1001", log.description)
        self.assertIn("dispensed prescription", log.description)
        self.assertTrue(any("archived" in m for m in self._messages(response)))

    def test_typed_patient_id_is_required(self):
        for confirm in (False, "", "PAT-WRONG"):
            response = self._delete(confirm=confirm)
            self.assertRedirects(response, reverse("patients:patient_list"))
            self.assertTrue(Patient.objects.filter(pk=self.patient.pk).exists())
            self.assertFalse(AuditLog.objects.filter(action="DELETE_PATIENT").exists())
            self.assertTrue(any("does not match" in m for m in self._messages(response)))

    def test_archived_patient_can_be_deleted_when_eligible(self):
        self.patient.is_archived = True
        self.patient.save(update_fields=["is_archived", "updated_at"])

        response = self._delete()

        self.assertRedirects(response, reverse("patients:patient_list"))
        self.assertFalse(Patient.objects.filter(pk=self.patient.pk).exists())


class PatientDeleteModalTests(PatientDeleteTestCase):

    def _patient_row(self, response):
        return next(
            p for p in response.context["patients"].object_list
            if p.pk == self.patient.pk
        )

    def test_modal_copy_controls_and_counts(self):
        self.build_patient_records()
        self.client.force_login(self.admin)
        response = self.client.get(reverse("patients:patient_list"))
        html = response.content.decode()

        self.assertIn("Delete Patient and All Related Records?", html)
        self.assertIn(
            "Are you sure you want to permanently delete this patient and all "
            "eligible associated records? This action cannot be undone.",
            html,
        )
        self.assertIn("Patient name", html)
        self.assertIn("Patient ID", html)
        self.assertIn('id="deleteBiteCaseCount"', html)
        self.assertIn('id="deleteAssessmentCount"', html)
        self.assertIn('id="deleteVaccinationCount"', html)
        self.assertIn('name="confirm_patient_number"', html)
        self.assertIn("Cancel", html)
        self.assertIn("Permanently Delete", html)
        self.assertIn("csrfmiddlewaretoken", html)

        row = self._patient_row(response)
        self.assertEqual(row.bite_case_count, 2)
        self.assertEqual(row.assessment_count, 2)
        self.assertEqual(row.vaccination_count, 2)
        self.assertFalse(
            row.has_dispensed_prescription
            or row.has_administered_dose
            or row.has_signed_assessment
        )

    def test_modal_flags_retention_protected_records(self):
        records = self.build_patient_records()
        records["prescription"].status = "dispensed"
        records["prescription"].dispensed_at = timezone.now()
        records["prescription"].save(update_fields=["status", "dispensed_at", "updated_at"])
        dose = records["doses"][0]
        dose.actual_date = date(2026, 10, 30)
        dose.dose_status = "completed"
        dose.save(update_fields=["actual_date", "dose_status", "updated_at"])
        assessment = records["assessments"][0]
        assessment.status = "final"
        assessment.is_finalized = True
        assessment.save(update_fields=["status", "is_finalized", "updated_at"])

        self.client.force_login(self.admin)
        response = self.client.get(reverse("patients:patient_list"))
        row = self._patient_row(response)
        self.assertTrue(row.has_dispensed_prescription)
        self.assertTrue(row.has_administered_dose)
        self.assertTrue(row.has_signed_assessment)
        self.assertIn(
            "Permanent deletion is not permitted for this patient",
            response.content.decode(),
        )
