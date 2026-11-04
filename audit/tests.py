from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from bite_cases.models import AnimalBiteCase, MedicalNote
from patients.models import Patient
from pre_registrations.models import PreRegistration

from .models import AuditLog

U = get_user_model()


class AuditAccessTests(TestCase):

    def setUp(self):
        self.admin = U.objects.create_user("admin.audit", password="pw12345678", role="admin")
        self.doctor = U.objects.create_user("doc.audit", password="pw12345678", role="doctor")
        self.nurse = U.objects.create_user("nurse.audit", password="pw12345678", role="nurse")
        AuditLog.objects.create(user=self.doctor, action="DOCTOR_ASSESSMENT",
                                description="Doctor signed an assessment")
        AuditLog.objects.create(user=self.admin, action="USER_CREATED",
                                description="Admin created a user")

    def test_doctor_sees_only_their_own_activity(self):
        self.client.force_login(self.doctor)
        response = self.client.get(reverse("audit:audit_list"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["own_only"])
        self.assertContains(response, "Activity Logs")
        self.assertContains(response, "Doctor signed an assessment")
        self.assertNotContains(response, "Admin created a user")

    def test_admin_still_sees_the_whole_trail(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("audit:audit_list"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["own_only"])
        self.assertContains(response, "Doctor signed an assessment")
        self.assertContains(response, "Admin created a user")

    def test_nurse_cannot_open_the_audit_trail(self):
        self.client.force_login(self.nurse)
        response = self.client.get(reverse("audit:audit_list"))
        self.assertEqual(response.status_code, 302)


class AuditRetrieveTests(TestCase):

    def setUp(self):
        self.admin = U.objects.create_user("admin.retrieve", password="pw12345678", role="admin")
        self.doctor = U.objects.create_user("doc.retrieve", password="pw12345678", role="doctor")
        self.patient = Patient.objects.create(
            patient_number="PAT-R1001",
            first_name="Retrieve",
            last_name="Fixture",
            birthdate=date(1990, 1, 1),
            contact_number="09170000000",
        )
        self.case = AnimalBiteCase.objects.create(
            case_number="ABC-R1001",
            patient=self.patient,
            created_by=self.admin,
            date_time_bite=timezone.now(),
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
        MedicalNote.objects.create(
            bite_case=self.case, note="Treatment note", created_by=self.admin)
        self.registration = PreRegistration.objects.create(
            first_name="Retrieve",
            last_name="Fixture",
            birthdate=date(1990, 1, 1),
            sex="female",
            contact_number="09170000001",
            address="Poblacion",
            barangay="Poblacion",
            medical_history="None",
            allergy_info="None",
            previous_rabies_vax="None",
            bite_datetime=timezone.now(),
            incident_place="Home",
            incident_barangay="Poblacion",
            animal_type="dog",
            animal_ownership="owned",
            animal_vaccination_status="unknown",
            animal_condition="under_observation",
            exposure_type="bite",
            body_part_affected="right hand",
            number_of_wounds=1,
            wound_washed=True,
            first_aid_given="Soap and water",
            consent_given=True,
        )

    def _archive_patient(self):
        self.client.force_login(self.admin)
        return self.client.post(
            reverse("patients:patient_archive", args=[self.patient.pk]))

    def _archive_case(self):
        self.client.force_login(self.admin)
        return self.client.post(
            reverse("bite_cases:case_delete", args=[self.case.pk]),
            {"reason": "Retention archive"},
        )

    def _archive_registration(self):
        self.client.force_login(self.admin)
        return self.client.post(
            reverse("pre_registrations:staff_archive", args=[self.registration.pk]))

    def _retrieve(self, url_name, obj, user=None, follow=True):
        self.client.force_login(user or self.admin)
        return self.client.post(reverse(url_name, args=[obj.pk]), follow=follow)

    def _messages(self, response):
        return [str(message) for message in response.context["messages"]]

    def test_admin_sees_retrieve_for_each_archived_record_type(self):
        self._archive_patient()
        self._archive_case()
        self._archive_registration()

        response = self.client.get(reverse("audit:audit_list"))

        self.assertContains(response, reverse("patients:patient_retrieve", args=[self.patient.pk]))
        self.assertContains(response, reverse("bite_cases:case_retrieve", args=[self.case.pk]))
        self.assertContains(
            response, reverse("pre_registrations:staff_retrieve", args=[self.registration.pk]))
        self.assertContains(response, "Retrieve")
        self.assertEqual(
            AuditLog.objects.filter(action="ARCHIVE_PATIENT").get().target_type, "patient")
        self.assertEqual(
            AuditLog.objects.filter(action="ARCHIVE_BITE_CASE").get().target_id, self.case.pk)
        self.assertEqual(
            AuditLog.objects.filter(action="ARCHIVE_PRE_REGISTRATION").get().target_type,
            "pre_registration",
        )

    def test_retrieve_patient_restores_active_record_and_audits(self):
        self._archive_patient()

        response = self._retrieve("patients:patient_retrieve", self.patient)

        self.patient.refresh_from_db()
        self.assertFalse(self.patient.is_archived)
        self.assertTrue(any("was retrieved" in message for message in self._messages(response)))
        retrieve_log = AuditLog.objects.get(action="RETRIEVE_PATIENT")
        self.assertEqual(retrieve_log.target_type, "patient")
        self.assertEqual(retrieve_log.target_id, self.patient.pk)
        listing = self.client.get(reverse("audit:audit_list"))
        self.assertNotContains(
            listing, reverse("patients:patient_retrieve", args=[self.patient.pk]))

    def test_retrieve_case_restores_the_status_it_had_before_archiving(self):
        self.case.case_status = "under_treatment"
        self.case.save(update_fields=["case_status"])
        self._archive_case()
        self.case.refresh_from_db()
        self.assertEqual(self.case.pre_archive_status, "under_treatment")

        response = self._retrieve("bite_cases:case_retrieve", self.case)

        self.case.refresh_from_db()
        self.assertEqual(self.case.case_status, "under_treatment")
        self.assertEqual(self.case.pre_archive_status, "")
        self.assertTrue(any("was retrieved" in message for message in self._messages(response)))
        self.assertTrue(AuditLog.objects.filter(action="RETRIEVE_BITE_CASE").exists())

    def test_retrieve_registration_restores_active_record_and_audits(self):
        self._archive_registration()
        self.registration.refresh_from_db()
        self.assertTrue(self.registration.is_archived)

        response = self._retrieve(
            "pre_registrations:staff_retrieve", self.registration)

        self.registration.refresh_from_db()
        self.assertFalse(self.registration.is_archived)
        self.assertTrue(any("was retrieved" in message for message in self._messages(response)))
        log = AuditLog.objects.get(action="RETRIEVE_PRE_REGISTRATION")
        self.assertEqual(log.target_id, self.registration.pk)

    def test_doctor_cannot_retrieve_archived_records(self):
        self._archive_patient()
        self._retrieve("patients:patient_retrieve", self.patient, user=self.doctor)

        self.patient.refresh_from_db()
        self.assertTrue(self.patient.is_archived)
        self.assertFalse(AuditLog.objects.filter(action="RETRIEVE_PATIENT").exists())

        self.client.force_login(self.doctor)
        listing = self.client.get(reverse("audit:audit_list"))
        self.assertNotContains(
            listing, reverse("patients:patient_retrieve", args=[self.patient.pk]))

    def test_retrieve_requires_post(self):
        self._archive_patient()
        self.client.force_login(self.admin)

        response = self.client.get(
            reverse("patients:patient_retrieve", args=[self.patient.pk]))

        self.assertEqual(response.status_code, 405)
        self.patient.refresh_from_db()
        self.assertTrue(self.patient.is_archived)

    def test_retrieving_a_record_that_is_not_archived_changes_nothing(self):
        response = self._retrieve("patients:patient_retrieve", self.patient)

        self.assertFalse(AuditLog.objects.filter(action="RETRIEVE_PATIENT").exists())
        self.assertTrue(any("is not archived" in message for message in self._messages(response)))
