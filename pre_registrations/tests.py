from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from bite_cases.models import AnimalBiteCase
from patients.models import Patient
from vaccination.models import VaccinationSchedule, VaccineDose

from .models import PreRegistration


class PreRegistrationModelTests(TestCase):
    def test_generates_daily_sequence_number_on_create(self):
        today = timezone.localdate()

        first = PreRegistration.objects.create(
            first_name="Ana",
            last_name="Santos",
            birthdate=date(2000, 1, 2),
            sex="female",
            contact_number="09170000001",
            address="Poblacion",
            barangay="Poblacion",
            bite_datetime=timezone.now(),
            incident_place="Market",
            incident_barangay="Poblacion",
            animal_type="dog",
            animal_ownership="stray",
            animal_vaccination_status="unknown",
            animal_condition="missing",
            exposure_type="bite",
            body_part_affected="Left leg",
            number_of_wounds=1,
            consent_given=True,
        )
        second = PreRegistration.objects.create(
            first_name="Ben",
            last_name="Reyes",
            birthdate=date(1998, 4, 3),
            sex="male",
            contact_number="09170000002",
            address="San Roque",
            barangay="San Roque",
            bite_datetime=timezone.now(),
            incident_place="Road",
            incident_barangay="San Roque",
            animal_type="cat",
            animal_ownership="unknown",
            animal_vaccination_status="unknown",
            animal_condition="alive",
            exposure_type="scratch",
            body_part_affected="Right hand",
            number_of_wounds=2,
            consent_given=True,
        )

        self.assertEqual(first.pre_registration_number, f"AB-{today:%Y%m%d}-0001")
        self.assertEqual(second.pre_registration_number, f"AB-{today:%Y%m%d}-0002")
        self.assertTrue(first.qr_code)

    def test_possible_duplicates_match_name_birthdate_contact_and_bite_date(self):
        bite_time = timezone.now()
        original = PreRegistration.objects.create(
            first_name="Cara",
            last_name="Dela Cruz",
            birthdate=date(2010, 5, 4),
            sex="female",
            contact_number="09171112222",
            address="Centro",
            barangay="Centro",
            bite_datetime=bite_time,
            incident_place="School",
            incident_barangay="Centro",
            animal_type="dog",
            animal_ownership="owned",
            animal_vaccination_status="vaccinated",
            animal_condition="alive",
            exposure_type="bite",
            body_part_affected="Arm",
            number_of_wounds=1,
            consent_given=True,
        )
        duplicate = PreRegistration.objects.create(
            first_name="Cara",
            last_name="Dela Cruz",
            birthdate=date(2010, 5, 4),
            sex="female",
            contact_number="09171112222",
            address="Centro",
            barangay="Centro",
            bite_datetime=bite_time,
            incident_place="School gate",
            incident_barangay="Centro",
            animal_type="dog",
            animal_ownership="owned",
            animal_vaccination_status="vaccinated",
            animal_condition="alive",
            exposure_type="bite",
            body_part_affected="Arm",
            number_of_wounds=1,
            consent_given=True,
        )

        self.assertQuerySetEqual(
            duplicate.possible_duplicates(),
            [original],
            transform=lambda record: record,
        )


class PublicPreRegistrationTests(TestCase):
    def valid_payload(self):
        return {
            "username": "lina.garcia",
            "email": "lina@example.com",
            "password1": "SafePatient!2026",
            "password2": "SafePatient!2026",
            "first_name": "Lina",
            "middle_name": "M",
            "last_name": "Garcia",
            "suffix": "",
            "birthdate": "1995-06-15",
            "sex": "female",
            "contact_number": "09175551234",
            "address": "123 Rizal Street",
            "barangay": "Poblacion",
            "guardian_name": "",
            "guardian_contact_number": "",
            "emergency_contact_name": "Nora Garcia",
            "emergency_contact_number": "09175554321",
            "bite_datetime": "2026-06-29T08:30",
            "incident_place": "Public market",
            "incident_barangay": "Poblacion",
            "animal_type": "dog",
            "animal_ownership": "stray",
            "animal_vaccination_status": "unknown",
            "animal_condition": "missing",
            "exposure_type": "bite",
            "body_part_affected": "Right calf",
            "number_of_wounds": "1",
            "wound_washed": "on",
            "first_aid_given": "Washed with soap and water",
            "remarks": "",
            "consent_given": "on",
        }

    def test_public_form_requires_consent(self):
        payload = self.valid_payload()
        payload.pop("consent_given")

        response = self.client.post(reverse("pre_registrations:public_create"), payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You must confirm consent before submitting")
        self.assertEqual(PreRegistration.objects.count(), 0)

    def test_public_submission_creates_record_and_shows_success_slip_link(self):
        response = self.client.post(reverse("pre_registrations:public_create"), self.valid_payload())

        record = PreRegistration.objects.get()
        self.assertRedirects(
            response,
            reverse("pre_registrations:public_success", args=[record.pre_registration_number]),
        )
        self.assertEqual(record.status, PreRegistration.Status.SUBMITTED)
        self.assertEqual(record.account.username, "lina.garcia")
        self.assertEqual(record.account.role, "patient")
        self.assertTrue(record.account.check_password("SafePatient!2026"))
        self.assertEqual(int(self.client.session['_auth_user_id']), record.account_id)
        self.assertEqual(record.age, 31)
        self.assertTrue(record.qr_code)

    def test_existing_email_does_not_create_partial_registration(self):
        User = get_user_model()
        User.objects.create_user(username="existing", email="lina@example.com", password="SafePatient!2026")

        response = self.client.post(reverse("pre_registrations:public_create"), self.valid_payload())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "An account with this email already exists")
        self.assertEqual(PreRegistration.objects.count(), 0)
        self.assertEqual(User.objects.count(), 1)

    def test_signed_in_patient_can_add_another_preregistration_to_same_account(self):
        self.client.post(reverse("pre_registrations:public_create"), self.valid_payload())
        account = get_user_model().objects.get(username="lina.garcia")
        second_payload = self.valid_payload()
        for field in ("username", "email", "password1", "password2"):
            second_payload.pop(field)
        second_payload["bite_datetime"] = "2026-07-30T09:00"

        response = self.client.post(reverse("pre_registrations:public_create"), second_payload)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(PreRegistration.objects.filter(account=account).count(), 2)
        self.assertEqual(get_user_model().objects.filter(role="patient").count(), 1)

    def test_status_lookup_only_returns_matching_pre_registration_number(self):
        record = PreRegistration.objects.create(
            first_name="Mila",
            last_name="Torres",
            birthdate=date(1989, 2, 1),
            sex="female",
            contact_number="09170000003",
            address="Poblacion",
            barangay="Poblacion",
            bite_datetime=timezone.now(),
            incident_place="Home",
            incident_barangay="Poblacion",
            animal_type="dog",
            animal_ownership="owned",
            animal_vaccination_status="vaccinated",
            animal_condition="alive",
            exposure_type="bite",
            body_part_affected="Hand",
            number_of_wounds=1,
            consent_given=True,
        )

        found = self.client.post(
            reverse("pre_registrations:public_status"),
            {"pre_registration_number": record.pre_registration_number},
        )
        missing = self.client.post(
            reverse("pre_registrations:public_status"),
            {"pre_registration_number": "AB-20260629-9999"},
        )

        self.assertContains(found, record.pre_registration_number)
        self.assertContains(found, "Submitted")
        self.assertNotContains(missing, record.full_name())
        self.assertContains(missing, "No pre-registration was found")


class StaffPreRegistrationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.staff = User.objects.create_user(
            username="encoder",
            password="pass12345",
            role="encoder",
        )
        self.record = PreRegistration.objects.create(
            first_name="Rico",
            last_name="Bautista",
            birthdate=date(2012, 7, 20),
            sex="male",
            contact_number="09176660000",
            address="Zone 1",
            barangay="Poblacion",
            guardian_name="Maria Bautista",
            guardian_contact_number="09176660001",
            emergency_contact_name="Maria Bautista",
            emergency_contact_number="09176660001",
            bite_datetime=timezone.now(),
            incident_place="Basketball court",
            incident_barangay="Poblacion",
            animal_type="dog",
            animal_ownership="stray",
            animal_vaccination_status="unknown",
            animal_condition="missing",
            exposure_type="bite",
            body_part_affected="Left ankle",
            number_of_wounds=1,
            wound_washed=True,
            first_aid_given="Soap and water",
            consent_given=True,
        )

    def test_staff_list_requires_login(self):
        response = self.client.get(reverse("pre_registrations:staff_list"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_staff_can_filter_by_number_name_contact_barangay_and_status(self):
        self.client.login(username="encoder", password="pass12345")

        response = self.client.get(
            reverse("pre_registrations:staff_list"),
            {
                "q": self.record.pre_registration_number,
                "barangay": "Poblacion",
                "status": PreRegistration.Status.SUBMITTED,
            },
        )

        self.assertContains(response, self.record.pre_registration_number)
        self.assertContains(response, "Rico Bautista")

    def test_staff_can_mark_duplicate_and_no_show(self):
        self.client.login(username="encoder", password="pass12345")

        duplicate_response = self.client.post(
            reverse("pre_registrations:mark_duplicate", args=[self.record.pk])
        )
        self.record.refresh_from_db()
        self.assertRedirects(
            duplicate_response,
            reverse("pre_registrations:staff_detail", args=[self.record.pk]),
        )
        self.assertEqual(self.record.status, PreRegistration.Status.DUPLICATE)

        no_show_response = self.client.post(
            reverse("pre_registrations:mark_no_show", args=[self.record.pk])
        )
        self.record.refresh_from_db()
        self.assertRedirects(
            no_show_response,
            reverse("pre_registrations:staff_detail", args=[self.record.pk]),
        )
        self.assertEqual(self.record.status, PreRegistration.Status.NO_SHOW)

    def test_staff_can_convert_to_official_patient_and_case(self):
        self.client.login(username="encoder", password="pass12345")

        response = self.client.post(reverse("pre_registrations:convert", args=[self.record.pk]))

        self.record.refresh_from_db()
        self.assertRedirects(response, reverse("bite_cases:case_detail", args=[self.record.converted_case_id]))
        self.assertEqual(self.record.status, PreRegistration.Status.CONVERTED)
        self.assertEqual(Patient.objects.count(), 1)
        self.assertEqual(AnimalBiteCase.objects.count(), 1)
        self.assertEqual(self.record.converted_patient.full_name(), "Rico Bautista")
        self.assertEqual(self.record.converted_case.body_part_affected, "Left ankle")

    def test_conversion_links_patient_account_and_portal_shows_case_and_dose(self):
        User = get_user_model()
        patient_account = User.objects.create_user(
            username="rico.patient",
            email="rico@example.com",
            password="SafePatient!2026",
            role="patient",
        )
        self.record.account = patient_account
        self.record.save(update_fields=["account"])
        self.client.login(username="encoder", password="pass12345")
        self.client.post(reverse("pre_registrations:convert", args=[self.record.pk]))
        self.record.refresh_from_db()
        schedule = VaccinationSchedule.objects.create(
            bite_case=self.record.converted_case,
            created_by=self.staff,
        )
        VaccineDose.objects.create(
            schedule=schedule,
            dose_label="Day 3",
            scheduled_date=date(2026, 8, 17),
        )

        self.client.logout()
        self.client.login(username="rico.patient", password="SafePatient!2026")
        response = self.client.get(reverse("accounts:patient_portal"))

        self.assertEqual(self.record.converted_patient.account, patient_account)
        self.assertContains(response, self.record.converted_case.case_number)
        self.assertContains(response, "Day 3")
        self.assertContains(response, "Aug 17, 2026")

    def test_patient_account_is_redirected_away_from_staff_pages(self):
        patient_account = get_user_model().objects.create_user(
            username="private.patient",
            password="SafePatient!2026",
            role="patient",
        )
        self.client.force_login(patient_account)

        response = self.client.get(reverse("pre_registrations:staff_list"))

        self.assertRedirects(response, reverse("accounts:patient_portal"))
