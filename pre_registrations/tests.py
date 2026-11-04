from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from bite_cases.models import AnimalBiteCase
from doctor.models import ClinicalAssessment
from patients.models import Patient
from vaccination.models import VaccinationSchedule, VaccineDose

from .models import PreRegistration
from .services import convert_to_official_case


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

    def test_initials_use_the_first_and_last_name(self):
        record = PreRegistration(
            first_name="Jasper Ian",
            middle_name="D",
            last_name="Babatingan",
        )

        self.assertEqual(record.initials(), "JB")

    def test_formatted_address_collapses_a_repeated_phrase(self):
        record = PreRegistration(
            address="San Pedro, Dumingag, Zamboanga del Sur Zamboanga del Sur",
        )

        self.assertEqual(
            record.formatted_address(),
            "San Pedro, Dumingag, Zamboanga del Sur",
        )
        self.assertEqual(
            record.address,
            "San Pedro, Dumingag, Zamboanga del Sur Zamboanga del Sur",
        )

    def test_formatted_address_collapses_duplicate_comma_segments(self):
        record = PreRegistration(
            address="Poblacion, Poblacion, Dumingag, Zamboanga del Sur",
        )

        self.assertEqual(
            record.formatted_address(), "Poblacion, Dumingag, Zamboanga del Sur"
        )

    def test_formatted_address_returns_blank_for_a_blank_address(self):
        self.assertEqual(PreRegistration(address="  ").formatted_address(), "")

    def test_guardian_and_emergency_contact_displays_join_related_values(self):
        record = PreRegistration(
            guardian_name="Maria Bautista",
            guardian_contact_number="09176660001",
            emergency_contact_name="Nora Garcia",
            emergency_contact_number="",
        )

        self.assertEqual(
            record.guardian_display(), "Maria Bautista · 09176660001"
        )
        self.assertEqual(record.emergency_contact_display(), "Nora Garcia")


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
            "medical_history": "None",
            "allergy_info": "Penicillin",
            "previous_rabies_vax": "None",
            "bite_date": "2026-06-29",
            "bite_time": "08:30",
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

    def test_public_submission_saves_civil_status(self):
        payload = self.valid_payload()
        payload["civil_status"] = "married"

        response = self.client.post(reverse("pre_registrations:public_create"), payload)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(PreRegistration.objects.get().civil_status, "married")

    def test_public_form_requires_medical_history_allergies_and_previous_rabies_vax(self):
        payload = self.valid_payload()
        for field in ("medical_history", "allergy_info", "previous_rabies_vax"):
            payload.pop(field)

        response = self.client.post(reverse("pre_registrations:public_create"), payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This field is required.", count=3)
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
        local_bite = timezone.localtime(record.bite_datetime)
        self.assertEqual(local_bite.strftime("%Y-%m-%d %H:%M"), "2026-06-29 08:30")

        success = self.client.get(response.url)
        self.assertContains(success, "Download QR Code")

        download = self.client.get(
            reverse("pre_registrations:qr_code", args=[record.pre_registration_number]),
            {"download": "1"},
        )
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download["Content-Type"], "image/png")
        self.assertIn("attachment;", download["Content-Disposition"])

    def test_existing_email_does_not_create_partial_registration(self):
        User = get_user_model()
        User.objects.create_user(username="existing", email="lina@example.com", password="SafePatient!2026")

        response = self.client.post(reverse("pre_registrations:public_create"), self.valid_payload())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "An account with this email already exists")
        self.assertEqual(PreRegistration.objects.count(), 0)
        self.assertEqual(User.objects.count(), 1)

    def test_duplicate_name_or_contact_is_rejected(self):
        PreRegistration.objects.create(
            first_name="Lina",
            last_name="Garcia",
            birthdate=date(1995, 6, 15),
            sex="female",
            contact_number="09175551234",
            address="123 Rizal Street",
            barangay="Poblacion",
            bite_datetime=timezone.now(),
            incident_place="Public market",
            incident_barangay="Poblacion",
            animal_type="dog",
            animal_ownership="stray",
            animal_vaccination_status="unknown",
            animal_condition="missing",
            exposure_type="bite",
            body_part_affected="Right calf",
            number_of_wounds=1,
            consent_given=True,
        )

        same_name = self.valid_payload()
        same_name["contact_number"] = "09179998888"
        response = self.client.post(reverse("pre_registrations:public_create"), same_name)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "same name or contact number")
        self.assertEqual(PreRegistration.objects.count(), 1)

        same_contact = self.valid_payload()
        same_contact["first_name"] = "Another"
        same_contact["last_name"] = "Person"
        response = self.client.post(reverse("pre_registrations:public_create"), same_contact)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "same name or contact number")
        self.assertEqual(PreRegistration.objects.count(), 1)
        self.assertEqual(get_user_model().objects.filter(role="patient").count(), 0)

    def test_cancelled_registration_does_not_block_a_new_submission(self):
        cancelled = PreRegistration.objects.create(
            first_name="Lina",
            last_name="Garcia",
            birthdate=date(1995, 6, 15),
            sex="female",
            contact_number="09175551234",
            address="123 Rizal Street",
            barangay="Poblacion",
            bite_datetime=timezone.now(),
            incident_place="Public market",
            incident_barangay="Poblacion",
            animal_type="dog",
            animal_ownership="stray",
            animal_vaccination_status="unknown",
            animal_condition="missing",
            exposure_type="bite",
            body_part_affected="Right calf",
            number_of_wounds=1,
            consent_given=True,
            status=PreRegistration.Status.CANCELLED,
        )

        response = self.client.post(reverse("pre_registrations:public_create"), self.valid_payload())

        self.assertEqual(response.status_code, 302)
        self.assertEqual(PreRegistration.objects.count(), 2)
        self.assertTrue(PreRegistration.objects.exclude(pk=cancelled.pk).exists())

    def test_rejected_registration_does_not_block_a_new_submission(self):
        PreRegistration.objects.create(
            first_name="Lina",
            last_name="Garcia",
            birthdate=date(1995, 6, 15),
            sex="female",
            contact_number="09175551234",
            address="123 Rizal Street",
            barangay="Poblacion",
            bite_datetime=timezone.now(),
            incident_place="Public market",
            incident_barangay="Poblacion",
            animal_type="dog",
            animal_ownership="stray",
            animal_vaccination_status="unknown",
            animal_condition="missing",
            exposure_type="bite",
            body_part_affected="Right calf",
            number_of_wounds=1,
            consent_given=True,
            status=PreRegistration.Status.REJECTED,
            rejection_reason="Incomplete information.",
        )

        response = self.client.post(reverse("pre_registrations:public_create"), self.valid_payload())

        self.assertEqual(response.status_code, 302)
        self.assertEqual(PreRegistration.objects.count(), 2)

    def test_signed_in_patient_can_add_another_preregistration_to_same_account(self):
        self.client.post(reverse("pre_registrations:public_create"), self.valid_payload())
        account = get_user_model().objects.get(username="lina.garcia")
        second_payload = self.valid_payload()
        for field in ("username", "email", "password1", "password2"):
            second_payload.pop(field)
        second_payload["bite_date"] = "2026-07-30"
        second_payload["bite_time"] = "09:00"

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
        self.assertContains(missing, "No online registration was found")


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
            medical_history="Asthma",
            allergy_info="None known",
            previous_rabies_vax="None",
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

    def test_staff_pages_offer_conversion_without_verify_console_or_duplicate_action(self):
        self.client.login(username="encoder", password="pass12345")

        detail = self.client.get(reverse("pre_registrations:staff_detail", args=[self.record.pk]))
        self.assertNotContains(detail, "Verify Console")
        self.assertNotContains(detail, "Mark Duplicate")
        self.assertNotContains(detail, "Mark No Show")
        self.assertContains(detail, "Convert to Official Case")

        listing = self.client.get(reverse("pre_registrations:staff_list"))
        self.assertNotContains(listing, "Verify Console")
        self.assertNotContains(listing, "Mark Duplicate")
        self.assertContains(listing, "Convert to Case")

    def test_staff_detail_displays_every_submitted_field_in_section_order(self):
        PreRegistration.objects.create(
            first_name="Oscar",
            last_name="Registrant",
            birthdate=date(1990, 1, 1),
            sex="male",
            contact_number="09170000009",
            address="San Roque",
            barangay="San Roque",
            medical_history="Hypertension",
            allergy_info="None",
            previous_rabies_vax="None",
            bite_datetime=timezone.now(),
            incident_place="Farm",
            incident_barangay="San Roque",
            animal_type="cat",
            animal_ownership="owned",
            animal_vaccination_status="vaccinated",
            animal_condition="alive",
            exposure_type="scratch",
            body_part_affected="Right arm",
            number_of_wounds=2,
            consent_given=True,
        )
        self.client.login(username="encoder", password="pass12345")

        detail = self.client.get(
            reverse("pre_registrations:staff_detail", args=[self.record.pk])
        )

        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, '<div class="pr-avatar">RB</div>', html=True)
        self.assertContains(detail, "Rico Bautista")
        self.assertContains(detail, self.record.pre_registration_number)
        self.assertContains(detail, "Submitted")

        html = detail.content.decode()
        self.assertLess(
            html.index("Patient Information"),
            html.index("Medical History"),
        )
        self.assertLess(
            html.index("Medical History"),
            html.index("Bite Incident Details"),
        )

        for label, value in (
            ("Full Name", "Rico Bautista"),
            ("Birthdate", "July 20, 2012"),
            ("Sex", "Male"),
            ("Contact Number", "09176660000"),
            ("Complete Address", "Zone 1"),
            ("Barangay", "Poblacion"),
            ("Parent / Guardian", "Maria Bautista · 09176660001"),
            ("Emergency Contact", "Maria Bautista · 09176660001"),
            ("Medical History", "Asthma"),
            ("Allergies", "None known"),
            ("Previous Rabies Vaccination", "None"),
            ("Place of Incident", "Basketball court"),
            ("Animal Type", "Dog"),
            ("Ownership", "Stray"),
            ("Animal Condition", "Missing"),
            ("Animal Vaccination Status", "Unknown"),
            ("Exposure Type", "Bite"),
            ("Body Part / Wounds", "Left ankle"),
            ("Wound Washed Immediately", "Yes"),
            ("First Aid Given", "Soap and water"),
        ):
            with self.subTest(label=label):
                self.assertContains(
                    detail,
                    f'<p class="pr-label">{label}</p>'
                    f'<p class="pr-value">{value}</p>',
                    html=True,
                )

        self.assertNotRegex(html, r"PAT-\d{5}")
        self.assertNotContains(detail, "Oscar Registrant")

    def test_staff_detail_shows_not_provided_for_blanks_and_keeps_explicit_none(self):
        self.client.login(username="encoder", password="pass12345")

        detail = self.client.get(
            reverse("pre_registrations:staff_detail", args=[self.record.pk])
        )

        self.assertContains(
            detail,
            '<p class="pr-label">Civil Status</p>'
            '<p class="pr-value">Not provided</p>',
            html=True,
        )
        self.assertContains(
            detail,
            '<p class="pr-label">Remarks</p>'
            '<p class="pr-value">Not provided</p>',
            html=True,
        )
        self.assertContains(
            detail,
            '<p class="pr-label">Allergies</p>'
            '<p class="pr-value">None known</p>',
            html=True,
        )
        self.assertNotContains(detail, "pr-tile--alert")

    def test_detail_hides_registration_meta_cards_from_patient_information(self):
        self.client.login(username="encoder", password="pass12345")

        detail = self.client.get(
            reverse("pre_registrations:staff_detail", args=[self.record.pk])
        )

        self.assertNotContains(detail, "Registration Date and Time")
        self.assertNotContains(detail, "Registration Status")
        self.assertNotContains(detail, "Consent to Data Use")
        self.assertContains(detail, "Emergency Contact")
        self.assertContains(detail, self.record.get_status_display())

        self.record.refresh_from_db()
        self.assertIsNotNone(self.record.submitted_at)
        self.assertEqual(self.record.status, PreRegistration.Status.SUBMITTED)
        self.assertTrue(self.record.consent_given)

        slip = self.client.get(
            reverse("pre_registrations:staff_slip", args=[self.record.pk])
        )
        for label in (
            "Registration Date and Time",
            "Registration Status",
            "Consent to Data Use",
        ):
            with self.subTest(label=label):
                self.assertContains(slip, label)

    def test_staff_detail_flags_a_real_allergy(self):
        self.record.allergy_info = "Penicillin"
        self.record.save(update_fields=["allergy_info", "updated_at"])
        self.client.login(username="encoder", password="pass12345")

        detail = self.client.get(
            reverse("pre_registrations:staff_detail", args=[self.record.pk])
        )

        self.assertContains(
            detail,
            '<div class="pr-tile pr-tile--wide pr-tile--alert">'
            '<p class="pr-label">Allergies</p>'
            '<p class="pr-value">Penicillin</p></div>',
            html=True,
        )

    def test_staff_detail_formats_duplicate_address_without_touching_the_stored_value(self):
        raw_address = "San Pedro, Dumingag, Zamboanga del Sur Zamboanga del Sur"
        self.record.address = raw_address
        self.record.save(update_fields=["address", "updated_at"])
        self.client.login(username="encoder", password="pass12345")

        detail = self.client.get(
            reverse("pre_registrations:staff_detail", args=[self.record.pk])
        )

        self.assertContains(
            detail, "San Pedro, Dumingag, Zamboanga del Sur", count=1
        )
        self.assertNotContains(detail, raw_address)
        self.record.refresh_from_db()
        self.assertEqual(self.record.address, raw_address)

    def test_staff_detail_actions_and_qr_are_read_only(self):
        self.client.login(username="encoder", password="pass12345")
        qr_url = reverse(
            "pre_registrations:qr_code", args=[self.record.pre_registration_number]
        )

        detail = self.client.get(
            reverse("pre_registrations:staff_detail", args=[self.record.pk])
        )

        self.assertContains(detail, "Back")
        self.assertContains(detail, "Edit")
        self.assertContains(detail, "Print Slip")
        self.assertContains(detail, "Convert to Official Case")
        self.assertContains(detail, "Reject Registration")
        self.assertContains(detail, qr_url)
        self.assertContains(detail, "Scan to verify")
        self.assertNotContains(detail, "View Patient Record")

        self.record.refresh_from_db()
        self.assertEqual(self.record.status, PreRegistration.Status.SUBMITTED)
        self.assertEqual(Patient.objects.count(), 0)
        self.assertEqual(AnimalBiteCase.objects.count(), 0)
        self.assertFalse(
            AuditLog.objects.filter(action="CONVERT_PRE_REGISTRATION").exists()
        )

    def test_detail_moves_convert_and_print_below_bite_incident_section(self):
        self.client.login(username="encoder", password="pass12345")

        detail = self.client.get(
            reverse("pre_registrations:staff_detail", args=[self.record.pk])
        )
        html = detail.content.decode()

        top_start = html.index('<div class="pr-actions no-print">')
        top = html[top_start:html.index("</div>", top_start)]
        self.assertIn("Back", top)
        self.assertIn("Edit", top)
        for removed in ("Print Slip", "Convert to Official Case", "Reject Registration"):
            self.assertNotIn(removed, top)

        bottom_start = html.index('<div class="pr-bottom-actions no-print">')
        bottom = html[bottom_start:html.index("</div>", bottom_start)]
        self.assertLess(html.index("Bite Incident Details"), bottom_start)
        self.assertLess(bottom_start, html.index('id="reject-registration"'))
        self.assertLess(
            bottom.index("Convert to Official Case"),
            bottom.index("Print Slip"),
        )
        self.assertIn(
            reverse("pre_registrations:convert", args=[self.record.pk]), bottom
        )
        self.assertIn(
            reverse("pre_registrations:staff_slip", args=[self.record.pk]), bottom
        )
        self.assertIn("csrfmiddlewaretoken", bottom)
        self.assertIn("pr-print-btn", bottom)
        self.assertIn("pr-btn-icon", bottom)
        self.assertEqual(html.count('class="btn-danger"'), 1)

    def test_converted_detail_keeps_top_to_back_and_edit(self):
        patient, _case = convert_to_official_case(self.record, self.staff)
        self.record.refresh_from_db()
        self.client.login(username="encoder", password="pass12345")

        detail = self.client.get(
            reverse("pre_registrations:staff_detail", args=[self.record.pk])
        )
        html = detail.content.decode()
        top_start = html.index('<div class="pr-actions no-print">')
        top = html[top_start:html.index("</div>", top_start)]
        bottom_start = html.index('<div class="pr-bottom-actions no-print">')
        bottom = html[bottom_start:html.index("</div>", bottom_start)]

        self.assertIn("Back", top)
        self.assertIn("Edit", top)
        self.assertNotIn("View Patient Record", top)
        self.assertIn("Print Slip", bottom)
        self.assertIn("View Patient Record", bottom)
        self.assertNotIn("Convert to Official Case", bottom)
        self.assertNotIn("Reject Registration", bottom)
        self.assertLess(bottom.index("Print Slip"), bottom.index("View Patient Record"))
        self.assertIn(
            reverse("patients:patient_detail", args=[patient.pk]), bottom
        )

    def test_staff_print_slip_contains_all_sections_and_fields(self):
        self.record.remarks = "Patient is afraid of needles."
        self.record.civil_status = "single"
        self.record.save(update_fields=["remarks", "civil_status", "updated_at"])
        self.client.login(username="encoder", password="pass12345")

        slip = self.client.get(
            reverse("pre_registrations:staff_slip", args=[self.record.pk])
        )

        self.assertEqual(slip.status_code, 200)
        for heading in ("Patient Information", "Medical History", "Bite Incident Details"):
            self.assertContains(slip, heading)
        for value in (
            self.record.pre_registration_number,
            "Rico Bautista",
            "Single",
            "Asthma",
            "None known",
            "None",
            "Patient is afraid of needles.",
            "Basketball court",
            "Dog",
            "Stray",
            "Missing",
            "Unknown",
            "Bite",
            "Left ankle",
            "Soap and water",
        ):
            with self.subTest(value=value):
                self.assertContains(slip, value)

    def test_public_print_slip_does_not_expose_medical_history(self):
        self.record.remarks = "Sensitive note"
        self.record.save(update_fields=["remarks", "updated_at"])

        slip = self.client.get(
            reverse(
                "pre_registrations:public_slip",
                args=[self.record.pre_registration_number],
            )
        )

        self.assertEqual(slip.status_code, 200)
        self.assertContains(slip, "Registration Slip")
        self.assertContains(slip, "Rico Bautista")
        for private_value in (
            "Medical History",
            "Asthma",
            "None known",
            "Previous Rabies Vaccination",
            "Sensitive note",
            "Soap and water",
        ):
            with self.subTest(private_value=private_value):
                self.assertNotContains(slip, private_value)

    def test_conversion_copies_the_submitted_civil_status(self):
        self.record.civil_status = "married"
        self.record.save(update_fields=["civil_status", "updated_at"])

        patient, _case = convert_to_official_case(self.record, self.staff)

        self.assertEqual(patient.civil_status, "married")

    def test_staff_can_convert_to_official_patient_and_case(self):
        self.client.login(username="encoder", password="pass12345")

        response = self.client.post(reverse("pre_registrations:convert", args=[self.record.pk]))

        self.record.refresh_from_db()
        self.assertRedirects(response, reverse("bite_cases:case_detail", args=[self.record.converted_case_id]))
        self.assertEqual(self.record.status, PreRegistration.Status.CONVERTED)
        self.assertEqual(Patient.objects.count(), 1)
        self.assertEqual(AnimalBiteCase.objects.count(), 1)
        self.assertEqual(self.record.converted_patient.full_name(), "Rico Bautista")
        self.assertEqual(self.record.converted_patient.allergy_info, "None known")
        self.assertEqual(self.record.converted_patient.previous_rabies_vax, "None")
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
        try:
            schedule = self.record.converted_case.vaccination_schedule
        except VaccinationSchedule.DoesNotExist:
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

    def test_staff_can_reject_with_reason_and_patient_sees_it(self):
        self.client.login(username="encoder", password="pass12345")

        detail = self.client.get(
            reverse("pre_registrations:staff_detail", args=[self.record.pk])
        )
        self.assertContains(detail, "Reject Registration")

        response = self.client.post(
            reverse("pre_registrations:staff_reject", args=[self.record.pk]),
            {"reason": "Duplicate entry from another barangay."},
        )

        self.assertRedirects(
            response,
            reverse("pre_registrations:staff_detail", args=[self.record.pk]),
        )
        self.record.refresh_from_db()
        self.assertEqual(self.record.status, PreRegistration.Status.REJECTED)
        self.assertEqual(
            self.record.rejection_reason, "Duplicate entry from another barangay."
        )
        self.assertEqual(self.record.rejected_by, self.staff)
        self.assertIsNotNone(self.record.rejected_at)

        status = self.client.post(
            reverse("pre_registrations:public_status"),
            {"pre_registration_number": self.record.pre_registration_number},
        )
        self.assertContains(status, "This registration was rejected")
        self.assertContains(status, "Duplicate entry from another barangay.")

    def test_reject_requires_a_reason(self):
        self.client.login(username="encoder", password="pass12345")

        response = self.client.post(
            reverse("pre_registrations:staff_reject", args=[self.record.pk]),
            {"reason": "   "},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A reason is required.")
        self.record.refresh_from_db()
        self.assertEqual(self.record.status, PreRegistration.Status.SUBMITTED)
        self.assertEqual(self.record.rejection_reason, "")

    def test_reject_page_requires_post(self):
        self.client.login(username="encoder", password="pass12345")

        response = self.client.get(
            reverse("pre_registrations:staff_reject", args=[self.record.pk])
        )

        self.assertRedirects(
            response,
            reverse("pre_registrations:staff_detail", args=[self.record.pk]),
        )
        self.record.refresh_from_db()
        self.assertEqual(self.record.status, PreRegistration.Status.SUBMITTED)

    def test_rejected_registration_cannot_be_converted(self):
        self.record.status = PreRegistration.Status.REJECTED
        self.record.rejection_reason = "Not a real patient."
        self.record.save(update_fields=["status", "rejection_reason"])
        self.client.login(username="encoder", password="pass12345")

        response = self.client.post(
            reverse("pre_registrations:convert", args=[self.record.pk])
        )

        self.assertRedirects(
            response,
            reverse("pre_registrations:staff_detail", args=[self.record.pk]),
        )
        self.record.refresh_from_db()
        self.assertEqual(self.record.status, PreRegistration.Status.REJECTED)
        self.assertEqual(Patient.objects.count(), 0)
        self.assertEqual(AnimalBiteCase.objects.count(), 0)

    def test_staff_edit_can_reject_with_rejection_reason(self):
        self.client.login(username="encoder", password="pass12345")
        payload = {
            "first_name": "Rico",
            "middle_name": "",
            "last_name": "Bautista",
            "suffix": "",
            "birthdate": "2012-07-20",
            "sex": "male",
            "contact_number": "09176660000",
            "address": "Zone 1",
            "barangay": "Poblacion",
            "guardian_name": "Maria Bautista",
            "guardian_contact_number": "09176660001",
            "emergency_contact_name": "Maria Bautista",
            "emergency_contact_number": "09176660001",
            "medical_history": "Asthma",
            "allergy_info": "None known",
            "previous_rabies_vax": "None",
            "bite_date": "2026-10-30",
            "bite_time": "10:00",
            "incident_place": "Basketball court",
            "incident_barangay": "Poblacion",
            "animal_type": "dog",
            "animal_ownership": "stray",
            "animal_vaccination_status": "unknown",
            "animal_condition": "missing",
            "exposure_type": "bite",
            "body_part_affected": "Left ankle",
            "number_of_wounds": "1",
            "first_aid_given": "Soap and water",
            "remarks": "",
            "status": PreRegistration.Status.REJECTED,
            "rejection_reason": "",
        }

        response = self.client.post(
            reverse("pre_registrations:staff_edit", args=[self.record.pk]), payload
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Provide a reason for rejecting this registration",
        )
        self.record.refresh_from_db()
        self.assertEqual(self.record.status, PreRegistration.Status.SUBMITTED)

        payload["rejection_reason"] = "Wrong barangay selected."
        response = self.client.post(
            reverse("pre_registrations:staff_edit", args=[self.record.pk]), payload
        )

        self.assertRedirects(
            response,
            reverse("pre_registrations:staff_detail", args=[self.record.pk]),
        )
        self.record.refresh_from_db()
        self.assertEqual(self.record.status, PreRegistration.Status.REJECTED)
        self.assertEqual(self.record.rejection_reason, "Wrong barangay selected.")
        self.assertEqual(self.record.rejected_by, self.staff)
        self.assertIsNotNone(self.record.rejected_at)


class OnlineRegistrationDeleteTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user("admin.reg", password="pass12345", role="admin")
        self.encoder = User.objects.create_user("encoder.reg", password="pass12345", role="encoder")
        self.doctor = User.objects.create_user("doctor.reg", password="pass12345", role="doctor")
        self.nurse = User.objects.create_user("nurse.reg", password="pass12345", role="nurse")
        self.patient_user = User.objects.create_user("patient.reg", password="pass12345", role="patient")
        self.record = self._make_registration(
            "Laila", "09175550001", status=PreRegistration.Status.SUBMITTED
        )
        self.other = self._make_registration(
            "Oscar", "09175550002", status=PreRegistration.Status.FOR_VERIFICATION
        )

    def _make_registration(self, first_name, contact_number, status=PreRegistration.Status.SUBMITTED):
        return PreRegistration.objects.create(
            first_name=first_name,
            last_name="Registrant",
            birthdate=date(1992, 2, 2),
            sex="female",
            contact_number=contact_number,
            address="Poblacion",
            barangay="Poblacion",
            medical_history="None",
            allergy_info="None",
            previous_rabies_vax="None",
            bite_datetime=timezone.now(),
            incident_place="Market",
            incident_barangay="Poblacion",
            animal_type="dog",
            animal_ownership="owned",
            animal_vaccination_status="unknown",
            animal_condition="alive",
            exposure_type="bite",
            body_part_affected="Left arm",
            number_of_wounds=1,
            consent_given=True,
            status=status,
        )

    def _delete(self, user=None, record=None):
        self.client.force_login(user or self.admin)
        return self.client.post(
            reverse("pre_registrations:staff_delete", args=[(record or self.record).pk]),
            follow=True,
        )

    def _archive(self, user=None, record=None):
        self.client.force_login(user or self.admin)
        return self.client.post(
            reverse("pre_registrations:staff_archive", args=[(record or self.record).pk]),
            follow=True,
        )

    def _messages(self, response):
        return [str(message) for message in response.context["messages"]]

    def test_only_admin_sees_delete_button_and_modal(self):
        delete_url = reverse("pre_registrations:staff_delete", args=[self.record.pk])
        for user, visible in (
            (self.admin, True),
            (self.encoder, False),
            (self.doctor, False),
            (self.nurse, False),
        ):
            self.client.force_login(user)
            response = self.client.get(reverse("pre_registrations:staff_list"))
            self.assertEqual(response.status_code, 200)
            html = response.content.decode()
            self.assertEqual(delete_url in html, visible, user.role)
            self.assertEqual("Delete Online Registration?" in html, visible, user.role)

        self.client.force_login(self.patient_user)
        self.assertEqual(
            self.client.get(reverse("pre_registrations:staff_list")).status_code, 302
        )

    def test_non_admin_cannot_delete_or_archive(self):
        for user in (self.encoder, self.doctor, self.nurse):
            delete_response = self._delete(user=user)
            self.assertRedirects(delete_response, reverse("pre_registrations:staff_list"))
            self.assertTrue(PreRegistration.objects.filter(pk=self.record.pk).exists())
            self.assertFalse(
                AuditLog.objects.filter(action="DELETE_PRE_REGISTRATION").exists()
            )

            archive_response = self._archive(user=user)
            self.assertRedirects(archive_response, reverse("pre_registrations:staff_list"))
            self.record.refresh_from_db()
            self.assertFalse(self.record.is_archived)
            self.assertFalse(
                AuditLog.objects.filter(action="ARCHIVE_PRE_REGISTRATION").exists()
            )

    def test_get_is_rejected(self):
        self.client.force_login(self.admin)
        for url in (
            reverse("pre_registrations:staff_delete", args=[self.record.pk]),
            reverse("pre_registrations:staff_archive", args=[self.record.pk]),
        ):
            self.assertEqual(self.client.get(url).status_code, 405)
        self.assertTrue(PreRegistration.objects.filter(pk=self.record.pk).exists())

    def test_login_required(self):
        response = self.client.post(
            reverse("pre_registrations:staff_delete", args=[self.record.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])
        self.assertTrue(PreRegistration.objects.filter(pk=self.record.pk).exists())

    def test_pending_registration_is_permanently_deleted(self):
        account = get_user_model().objects.create_user(
            "laila.reg.portal", password="pass12345", role="patient"
        )
        self.record.account = account
        self.record.save(update_fields=["account", "updated_at"])

        response = self._delete()

        self.assertRedirects(response, reverse("pre_registrations:staff_list"))
        self.assertFalse(PreRegistration.objects.filter(pk=self.record.pk).exists())
        self.assertTrue(PreRegistration.objects.filter(pk=self.other.pk).exists())
        self.assertTrue(get_user_model().objects.filter(pk=account.pk).exists())
        self.assertEqual(
            self.client.get(
                reverse("pre_registrations:staff_detail", args=[self.record.pk])
            ).status_code,
            404,
        )
        log = AuditLog.objects.get(action="DELETE_PRE_REGISTRATION")
        self.assertEqual(log.user, self.admin)
        self.assertIn(self.record.pre_registration_number, log.description)
        self.assertTrue(
            any("permanently deleted" in message for message in self._messages(response))
        )

    def test_converted_registration_is_blocked_and_preserves_patient_records(self):
        patient, case = convert_to_official_case(self.record, self.encoder)
        self.record.refresh_from_db()
        schedule = VaccinationSchedule.objects.create(bite_case=case, created_by=self.nurse)
        VaccineDose.objects.create(schedule=schedule, dose_label="Day 0", dose_status="scheduled")
        assessment = ClinicalAssessment.objects.create(
            patient=patient, bite_case=case, assessed_by=self.doctor, status="draft"
        )

        response = self._delete()

        self.assertRedirects(response, reverse("pre_registrations:staff_list"))
        self.record.refresh_from_db()
        self.assertFalse(self.record.is_archived)
        self.assertTrue(Patient.objects.filter(pk=patient.pk).exists())
        self.assertTrue(AnimalBiteCase.objects.filter(pk=case.pk).exists())
        self.assertTrue(ClinicalAssessment.objects.filter(pk=assessment.pk).exists())
        self.assertTrue(VaccinationSchedule.objects.filter(pk=schedule.pk).exists())
        self.assertFalse(
            AuditLog.objects.filter(action="DELETE_PRE_REGISTRATION").exists()
        )
        messages = self._messages(response)
        self.assertTrue(any("blocked by the medical record-retention policy" in m for m in messages))
        self.assertTrue(any("Archive it instead" in m for m in messages))

    def test_verified_registration_is_blocked(self):
        PreRegistration.objects.filter(pk=self.record.pk).update(
            status=PreRegistration.Status.VERIFIED
        )

        response = self._delete()

        self.assertRedirects(response, reverse("pre_registrations:staff_list"))
        self.assertTrue(PreRegistration.objects.filter(pk=self.record.pk).exists())
        self.assertTrue(
            any("approved registration" in m for m in self._messages(response))
        )

    def test_rejected_registration_is_archived_instead(self):
        PreRegistration.objects.filter(pk=self.record.pk).update(
            status=PreRegistration.Status.REJECTED
        )

        delete_response = self._delete()
        self.assertTrue(PreRegistration.objects.filter(pk=self.record.pk).exists())
        self.assertTrue(
            any("rejected registration" in m for m in self._messages(delete_response))
        )

        archive_response = self._archive()
        self.record.refresh_from_db()
        self.assertTrue(self.record.is_archived)
        self.assertTrue(PreRegistration.objects.filter(pk=self.record.pk).exists())
        self.assertTrue(
            any("was archived" in m for m in self._messages(archive_response))
        )
        listing = self.client.get(reverse("pre_registrations:staff_list"))
        self.assertNotContains(listing, self.record.pre_registration_number)
        self.assertContains(listing, self.other.pre_registration_number)
        log = AuditLog.objects.get(action="ARCHIVE_PRE_REGISTRATION")
        self.assertEqual(log.user, self.admin)

    def test_archive_keeps_converted_registration_and_linked_records(self):
        patient, case = convert_to_official_case(self.record, self.encoder)
        self.record.refresh_from_db()

        response = self._archive()

        self.assertRedirects(response, reverse("pre_registrations:staff_list"))
        self.record.refresh_from_db()
        self.assertTrue(self.record.is_archived)
        self.assertEqual(self.record.converted_patient_id, patient.pk)
        self.assertTrue(Patient.objects.filter(pk=patient.pk).exists())
        self.assertTrue(AnimalBiteCase.objects.filter(pk=case.pk).exists())
        listing = self.client.get(reverse("pre_registrations:staff_list"))
        self.assertNotContains(listing, self.record.pre_registration_number)

    def test_modal_copy_and_fields_are_rendered(self):
        self.client.force_login(self.admin)
        html = self.client.get(reverse("pre_registrations:staff_list")).content.decode()
        self.assertIn("Delete Online Registration?", html)
        self.assertIn(
            "Are you sure you want to delete this online registration record? "
            "This action cannot be undone if permanently deleted.",
            html,
        )
        self.assertIn("Registration Number", html)
        self.assertIn("Patient Name", html)
        self.assertIn("Registration Date", html)
        self.assertIn("Registration Status", html)
        self.assertIn("Cancel", html)
        self.assertIn("Confirm Delete", html)
        self.assertIn("csrfmiddlewaretoken", html)
