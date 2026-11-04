import datetime
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from bite_cases.models import AnimalBiteCase
from doctor.models import ClinicalAssessment, Prescription
from inventory.models import MedicineInventory, VaccineInventory
from vaccination.models import VaccinationSchedule, VaccineDose

from .models import Barangay, Patient, VitalSign

U = get_user_model()


class PatientDetailRecordTests(TestCase):
    """The admin patient record must surface every saved field, split the fields
    the clinic reads separately, and never blur data between bite cases."""

    def setUp(self):
        self.admin = U.objects.create_user("admin.record", password="pw12345678", role="admin")
        self.doctor = U.objects.create_user("doc.record", password="pw12345678", role="doctor")
        self.nurse = U.objects.create_user("nurse.record", password="pw12345678", role="nurse")
        self.barangay = Barangay.objects.create(name="Poblacion")
        self.patient = Patient.objects.create(
            patient_number="PAT-D2001",
            first_name="Laila",
            middle_name="C",
            last_name="Silva",
            birthdate=date(1990, 5, 4),
            sex="female",
            civil_status="single",
            contact_number="09171234567",
            address="Zone 4, Poblacion",
            barangay=self.barangay,
            parent_guardian="Maria Silva",
            emergency_contact_name="Maria Silva",
            emergency_contact_number="09179876543",
            medical_history="Asthma",
            allergy_info="Penicillin",
            previous_rabies_vax="None",
            remarks="Follow-up after 7 days",
        )
        self.portal = U.objects.create_user(
            "laila.record", password="pw12345678", role="patient", email="laila@example.com")
        self.patient.account = self.portal
        self.patient.save(update_fields=["account"])

        self.bite_datetime = timezone.make_aware(datetime.datetime(2026, 10, 1, 9, 30))
        self.case = AnimalBiteCase.objects.create(
            case_number="ABC-D2001",
            patient=self.patient,
            created_by=self.nurse,
            date_time_bite=self.bite_datetime,
            place_of_incident="Barangay Hall grounds",
            incident_barangay="Poblacion",
            animal_type="dog",
            animal_ownership="owned",
            animal_vax_status="unknown",
            animal_condition="under_observation",
            exposure_type="bite",
            body_part_affected="right forearm",
            number_of_wounds=2,
            wound_description="Two puncture wounds",
            wound_washed_immediately=True,
            first_aid_given="Wound cleaned with soap and water",
            initial_consultation_notes="Referred to ABTC immediately",
        )
        self.medicine = MedicineInventory.objects.create(
            medicine_name="Amoxicillin",
            generic_name="Amoxicillin",
            strength="500 mg",
            dosage_form="capsule",
            unit="tablet",
            batch_number="MED-D2001",
            expiration_date=date(2027, 12, 31),
            quantity_received=100,
            quantity_available=100,
        )
        self.assessment = ClinicalAssessment.objects.create(
            patient=self.patient,
            bite_case=self.case,
            assessed_by=self.doctor,
            chief_complaint="Dog bite on the right forearm",
            history_present_illness="Bitten while walking home",
            exposure_narrative="Unprovoked dog bite, skin broken",
            animal_type_verified="dog",
            animal_owned_verified="owned",
            animal_vax_verified="unknown",
            wound_washed_verified=True,
            body_part_verified="right forearm",
            number_of_wounds_verified=2,
            date_time_bite_verified=self.bite_datetime,
            exam_heent="Normal",
            exam_chest_lungs="Clear",
            exam_cardiac="Normal",
            exam_abdomen="Soft",
            skin_wound_description="Two puncture wounds",
            systemic_review="No systemic symptoms",
            category_confirmed="category_ii",
            pep_indicated=True,
            rig_indicated=False,
            tt_indicated=True,
            vaccine_brand_plan="Verorab",
            dose_schedule_plan="Days 0, 3, 7, 14, 28",
            dose_protocol="essen",
            first_dose_date=date(2026, 10, 2),
            vaccine_lot_number="LOT-42",
            vaccine_dose_ml="0.5",
            vaccine_quantity=5,
            treatment_plan="Complete 5-dose PEP",
            prescription="Amoxicillin 500 mg",
            dosage_instructions="500 mg every 8 hours for 7 days",
            follow_up_instructions="Return on Day 3",
            additional_notes="Recheck allergy before each dose",
        )
        self.schedule = VaccinationSchedule.objects.create(
            bite_case=self.case,
            tetanus_toxoid_given=True,
            antibiotics_given=True,
        )
        VaccineDose.objects.create(
            schedule=self.schedule, dose_label="Day 0", protocol_day=0,
            scheduled_date=date(2026, 10, 2), actual_date=date(2026, 10, 2),
            vaccine_brand="Verorab", batch_number="LOT-42", dose_status="completed",
        )
        VaccineDose.objects.create(
            schedule=self.schedule, dose_label="Day 3", protocol_day=3, dose_status="pending")
        Prescription.objects.create(
            assessment=self.assessment, patient=self.patient, bite_case=self.case,
            medicine=self.medicine, quantity=21,
        )
        VitalSign.objects.create(
            patient=self.patient,
            taken_at=timezone.make_aware(datetime.datetime(2026, 10, 1, 10, 15)),
            bp_systolic=120, bp_diastolic=80, temperature=Decimal("36.8"),
            pulse_rate=82, respiratory_rate=18, spo2=98,
            weight=Decimal("58.50"), height=Decimal("160.00"), bmi=Decimal("22.85"),
            pain_score=3, blood_glucose=Decimal("95.0"), glucose_type="random",
            general_appearance="well", notes="No allergy reaction observed",
            taken_by=self.nurse,
        )

    def _get(self, user=None):
        self.client.force_login(user or self.admin)
        return self.client.get(reverse("patients:patient_detail", args=[self.patient.pk]))

    def _assert_sections_in_order(self, content):
        anchors = [
            'id="patient-information"',
            'id="medical-history"',
            'id="bite-incidents"',
            'id="vitals"',
            'id="clinical-assessment"',
            'id="portal-access"',
        ]
        positions = [content.index(anchor) for anchor in anchors]
        self.assertEqual(positions, sorted(positions))

    def test_patient_information_keeps_every_field_separate(self):
        response = self._get()
        content = response.content.decode()
        self._assert_sections_in_order(content)

        expected = [
            self.patient.patient_number,
            self.patient.full_name(),
            "May 4, 1990",
            f"{self.patient.age} years old",
            "Female",
            "Single",
            self.patient.address,
            self.patient.contact_display,
            "Poblacion",
            self.patient.parent_guardian,
            self.patient.emergency_contact_name,
            self.patient.emergency_contact_display,
            "Asthma",
            "Penicillin",
            "None",
            self.patient.remarks,
        ]
        for value in expected:
            self.assertIn(value, content)
        self.assertIn("Birthdate", content)
        self.assertIn(">Age<", content)
        self.assertIn(">Sex<", content)
        self.assertIn("Civil Status", content)
        self.assertNotIn("Age / Sex", content)

    def test_bite_incident_fields_are_not_combined(self):
        response = self._get()
        content = response.content.decode()
        for label in [
            "Date of Incident", "Time of Incident", "Place of Incident",
            "Animal Type", "Ownership", "Animal Condition",
            "Animal Vaccination Status", "Exposure Type", "Number of Wounds",
            "Date of Consultation", "Wound Washed Immediately", "First Aid Given",
        ]:
            self.assertIn(label, content)
        for value in [
            "October 1, 2026", "9:30 AM", "Barangay Hall grounds", "Poblacion",
            "Dog", "Owned", "Under Observation", "Unknown", "Bite",
            "right forearm", "Two puncture wounds",
            "Wound cleaned with soap and water", "Referred to ABTC immediately",
        ]:
            self.assertIn(value, content)
        self.assertNotIn("Date &amp; Time of Bite</p>", content)
        self.assertNotIn("Animal</p>", content)

    def test_vitals_show_latest_record_with_all_fields(self):
        response = self._get()
        content = response.content.decode()
        for label in [
            "Date and Time Taken", "Blood Pressure", "Temperature", "Pulse Rate",
            "Respiratory Rate", "SpO₂", "Weight", "Height", "BMI", "Pain Scale",
            "Blood Glucose", "General Appearance and Notes", "Recorded By",
        ]:
            self.assertIn(label, content)
        for value in [
            "October 1, 2026", "10:15 AM", "120/80", "36.8", "82", "18", "98",
            "58.50", "160.00", "22.85", "95.0", "Random", "Well",
            "No allergy reaction observed", self.nurse.username,
        ]:
            self.assertIn(value, content)
        self.assertNotIn("NO VITALS YET", content)

    def test_clinical_assessment_shows_complete_plan(self):
        response = self._get()
        content = response.content.decode()
        letters = [
            '<span class="pd-group-letter pd-group-letter--round">A</span>',
            '<span class="pd-group-letter pd-group-letter--round">B</span>',
        ] + [f'<span class="pd-group-letter">{letter}</span>' for letter in "CDEF"]
        positions = [content.index(marker) for marker in letters]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn('pd-group-letter">H', content)

        for label in [
            "Chief Complaint",
            "Exposure Narrative (Verified)", "Narrative as Verified by the Doctor",
            "WHO Bite Category", "Dose Schedule", "Date (1st Dose)",
            "Dose Schedule Records", "Vaccine Brand (from Stock)",
            "Volume per Dose (mL)", "Quantity (Vials/Doses)", "1st Dose Date",
            "Treatment Plan", "Prescription",
            "Dosage Instructions — Frequency &amp; Duration",
            "Follow-up Instructions", "Additional Notes / Allergy Re-check",
        ]:
            self.assertIn(label, content)
        for value in [
            "Category II", "Nibbling, minor scratches without bleeding",
            "5 Doses", "October 02, 2026",
            "1st Dose", "2nd Dose", "3rd Dose", "4th Dose", "5th Dose",
            "Verorab", "0.5", "5",
            "Complete 5-dose PEP", "Amoxicillin 500 mg", "21 tablets",
            "500 mg every 8 hours for 7 days", "Return on Day 3",
            "Recheck allergy before each dose",
            "Completed", "Pending Scheduling", "Not yet set",
        ]:
            self.assertIn(value, content)
        self.assertNotIn("— Select protocol —", content)
        self.assertNotIn("Medicine Quantity", content)
        self.assertNotIn("Vaccine &amp; Treatment Plan", content)

    def test_clinical_assessment_simplifies_sections_a_and_b(self):
        self.assessment.status = "final"
        self.assessment.is_finalized = True
        self.assessment.save(update_fields=["status", "is_finalized"])
        response = self._get()
        content = response.content.decode()
        start = content.index('id="clinical-assessment"')
        section = content[start:content.index('id="portal-access"')]

        self.assertIn('<span class="pd-group-letter pd-group-letter--round">A</span>', section)
        self.assertIn('<span class="pd-group-letter pd-group-letter--round">B</span>', section)
        self.assertIn(self.assessment.chief_complaint, section)
        self.assertIn(self.assessment.exposure_narrative, section)

        for hidden in [
            "Attending Doctor", "Assessment Date", "Status:", "PRC No.",
            "History of Present Illness", "Verified incident details",
            "Examination findings", "Verified Animal Type",
            "Ownership (Verified)", "Animal Vaccination Status (Verified)",
            "Wound Washed Immediately (Verified)", "Body Part (Verified)",
            "Number of Wounds (Verified)", "Date &amp; Time of Bite (Verified)",
            "HEENT", "Chest / Lungs", "Cardiac", "Abdomen",
            "Skin / Wound Description", "Systemic Review",
        ]:
            self.assertNotIn(hidden, section)
        self.assertNotIn(self.assessment.get_status_display(), section)

        for kept in [
            "WHO Bite Category", "Dose Schedule Records",
            "Vaccine Brand (from Stock)", "Treatment Plan", "Prescription",
            "Dosage Instructions — Frequency &amp; Duration",
            "Follow-up Instructions", "Additional Notes / Allergy Re-check",
        ]:
            self.assertIn(kept, section)

        self.assessment.refresh_from_db()
        self.assertTrue(self.assessment.is_finalized)
        self.assertEqual(self.assessment.get_status_display(), "Final - Signed")

    def test_dose_records_use_saved_dates_and_never_auto_calculate(self):
        VaccineDose.objects.create(
            schedule=self.schedule, dose_label="Day 7", protocol_day=7,
            scheduled_date=date(2026, 10, 9), dose_status="scheduled",
        )
        response = self._get()
        content = response.content.decode()
        self.assertIn("Oct 09, 2026", content)
        self.assertIn("Scheduled", content)
        self.assertIn("3rd Dose", content)
        self.assertIn("5th Dose", content)
        # Day 3 has no saved appointment; no future protocol date may be invented.
        self.assertNotIn("Oct 05, 2026", content)

    def test_dose_section_follows_doctor_count_and_hides_legacy_helpers(self):
        self.assessment.dose_protocol = "zagreb"
        self.assessment.save(update_fields=["dose_protocol"])
        response = self._get()
        content = response.content.decode()
        start = content.index('<span class="pd-group-letter">D</span>')
        section = content[start:content.index('<span class="pd-group-letter">E</span>')]

        self.assertIn("3 Doses", section)
        self.assertIn("1st Dose", section)
        self.assertIn("2nd Dose", section)
        self.assertIn("3rd Dose", section)
        self.assertNotIn("4th Dose", section)
        self.assertNotIn("Day 0", section)
        self.assertNotIn("Day 3", section)
        self.assertNotIn("doses on this schedule", section)
        self.assertNotIn("Manage Schedule", section)
        self.assertNotIn("RIG given", section)
        self.assertNotIn("Tetanus toxoid given", section)

    def test_vaccine_brand_combines_name_and_brand_without_lot(self):
        vaccine = VaccineInventory.objects.create(
            vaccine_name="RIG (Rabies Immunoglobulin)",
            brand="Imogam",
            batch_number="IG-D3001",
            expiration_date=date(2027, 12, 31),
            quantity_received=10,
            quantity_available=10,
            created_by=self.doctor,
        )
        self.assessment.vaccine_inventory = vaccine
        self.assessment.save(update_fields=["vaccine_inventory"])
        response = self._get()
        content = response.content.decode()
        start = content.index('<span class="pd-group-letter">E</span>')
        section = content[start:content.index('<span class="pd-group-letter">F</span>')]

        self.assertIn("RIG (Rabies Immunoglobulin) (Imogam)", section)
        self.assertIn("Volume per Dose (mL)", section)
        self.assertIn("Quantity (Vials/Doses)", section)
        self.assertIn("1st Dose Date", section)
        self.assertIn("0.5", section)
        self.assertIn("5", section)
        self.assertIn("October 02, 2026", section)
        self.assertNotIn("IG-D3001", section)
        self.assertNotIn("LOT-42", section)
        self.assertNotIn("Lot / batch", section)
        self.assertNotIn("Batch:", section)

    def test_treatment_section_hides_flags_and_status_and_shows_prescription_cards(self):
        response = self._get()
        content = response.content.decode()
        start = content.index('<span class="pd-group-letter">F</span>')
        section = content[start:content.index('id="portal-access"')]

        self.assertIn("Treatment Plan", section)
        self.assertIn(self.assessment.treatment_plan, section)
        self.assertIn("Prescription (from Medicine Inventory)", section)
        self.assertIn("Quantity", section)
        self.assertIn("Amoxicillin 500 mg · Capsule · Amoxicillin", section)
        self.assertIn("21 tablets", section)
        self.assertIn("Dosage Instructions — Frequency &amp; Duration", section)
        self.assertIn("Follow-up Instructions", section)
        self.assertIn("Additional Notes / Allergy Re-check", section)
        self.assertIn("Recheck allergy before each dose", section)

        self.assertNotIn("PEP Indicated", section)
        self.assertNotIn("RIG Indicated", section)
        self.assertNotIn("Tetanus Toxoid Indicated", section)
        self.assertNotIn("Prescription note:", section)
        self.assertNotIn("Dispensed", section)
        self.assertNotIn("pd-record--rx2", section)

    def test_prescription_lists_every_medicine_and_quantity(self):
        ibuprofen = MedicineInventory.objects.create(
            medicine_name="Ibuprofen",
            generic_name="Ibuprofen",
            strength="200 mg",
            dosage_form="tablet",
            unit="tablet",
            batch_number="MED-D2002",
            expiration_date=date(2027, 12, 31),
            quantity_received=50,
            quantity_available=50,
        )
        Prescription.objects.create(
            assessment=self.assessment, patient=self.patient, bite_case=self.case,
            medicine=ibuprofen, quantity=6,
        )
        response = self._get()
        content = response.content.decode()
        self.assertIn("Amoxicillin", content)
        self.assertIn("Ibuprofen", content)
        self.assertIn("21 tablets", content)
        self.assertIn("6 tablets", content)

    def test_print_sheet_keeps_full_a_to_f_assessment(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("patients:patient_print", args=[self.patient.pk]))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        letters = [f'<span class="cs-letter">{letter}</span>' for letter in "ABCDEF"]
        positions = [content.index(marker) for marker in letters]
        self.assertEqual(positions, sorted(positions))
        for label in [
            "Chief Complaint", "Narrative as Verified by the Doctor",
            "WHO Bite Category", "Dose Schedule Records", "Vaccine Brand (from Stock)",
            "Volume per Dose (mL)", "Quantity (Vials/Doses)", "1st Dose Date",
            "Treatment Plan", "Prescription",
            "Dosage Instructions — Frequency &amp; Duration",
            "Follow-up Instructions", "Additional Notes / Allergy Re-check",
        ]:
            self.assertIn(label, content)
        for value in [
            "Category II", "Nibbling, minor scratches without bleeding",
            "Days 0, 3, 7, 14, 28", "Day 0", "Day 3", "Completed",
            "Pending Scheduling", "Not yet set", "Verorab", "LOT-42",
            "0.5", "5", "Amoxicillin", "21 tablets",
            "500 mg every 8 hours for 7 days", "Return on Day 3",
            "Recheck allergy before each dose",
        ]:
            self.assertIn(value, content)

    def test_print_sheet_empty_assessment_message(self):
        empty_patient = Patient.objects.create(
            patient_number="PAT-D2003",
            first_name="Nora",
            last_name="Bautista",
            birthdate=date(1993, 7, 9),
            sex="female",
            contact_number="09170002222",
            address="Barangay San Jose",
        )
        self.client.force_login(self.admin)
        response = self.client.get(reverse("patients:patient_print", args=[empty_patient.pk]))
        content = response.content.decode()
        self.assertIn("NO ASSESSMENT YET", content)
        self.assertIn("No doctor assessment has been recorded for this case.", content)

    def test_pdf_export_builds_full_assessment_document(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("patients:patient_print_pdf", args=[self.patient.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertGreater(len(response.content), 5000)

    def test_portal_access_is_last_and_does_not_repeat_stored_password(self):
        response = self._get()
        content = response.content.decode()
        self.assertGreater(
            content.index('id="portal-access"'), content.index('id="clinical-assessment"'))
        self.assertIn("Patient Portal Access", content)
        self.assertIn(self.portal.username, content)
        self.assertIn("Reset Temporary Password", content)
        self.assertIn("Open Login Page", content)
        self.assertNotIn("Temporary Password (shown once)", content)
        self.assertNotIn("Bite Cases &amp; Vaccination Schedules", content)
        self.assertNotIn("Bite Cases & Vaccination Schedules", content)

    def test_actions_and_qr_remain_available(self):
        response = self._get()
        content = response.content.decode()
        self.assertIn(reverse("patients:patient_print", args=[self.patient.pk]), content)
        self.assertIn(reverse("patients:patient_print_pdf", args=[self.patient.pk]), content)
        self.assertIn(reverse("patients:patient_edit", args=[self.patient.pk]), content)
        self.assertIn(reverse("bite_cases:case_create"), content)
        self.assertIn("Download QR", content)
        self.assertIn(reverse("bite_cases:case_detail", args=[self.case.pk]), content)
        self.assertIn(reverse("vaccination:schedule_detail", args=[self.schedule.pk]), content)

    def test_doctor_does_not_see_portal_access(self):
        response = self._get(user=self.doctor)
        content = response.content.decode()
        self.assertIn('id="patient-information"', content)
        self.assertNotIn('id="portal-access"', content)
        self.assertNotIn("Patient Portal Access", content)

    def test_nurse_does_not_see_portal_access_but_keeps_clinical_sections(self):
        response = self._get(user=self.nurse)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        for section_id in (
            'id="patient-information"',
            'id="medical-history"',
            'id="bite-incidents"',
            'id="vitals"',
            'id="clinical-assessment"',
        ):
            self.assertIn(section_id, content)
        self.assertNotIn('id="portal-access"', content)
        for hidden in (
            "Patient Portal Access",
            "Has Login",
            "Account Status",
            "Patient Login Details",
            "Reset Temporary Password",
            "Open Login Page",
            self.portal.username,
            self.portal.email,
            reverse("patients:patient_reset_password", args=[self.patient.pk]),
        ):
            self.assertNotIn(hidden, content)

    def test_nurse_cannot_reset_portal_password(self):
        self.client.force_login(self.nurse)
        response = self.client.post(
            reverse("patients:patient_reset_password", args=[self.patient.pk]))
        self.assertEqual(response.status_code, 403)
        self.portal.refresh_from_db()
        self.assertTrue(self.portal.check_password("pw12345678"))

    def test_doctor_cannot_reset_portal_password(self):
        self.client.force_login(self.doctor)
        response = self.client.post(
            reverse("patients:patient_reset_password", args=[self.patient.pk]))
        self.assertEqual(response.status_code, 403)
        self.portal.refresh_from_db()
        self.assertTrue(self.portal.check_password("pw12345678"))

    @patch("vaccination.sms.send_semaphore_sms", return_value={"success": False})
    def test_admin_can_still_reset_portal_password(self, _sms):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("patients:patient_reset_password", args=[self.patient.pk]))
        self.assertRedirects(
            response,
            reverse("patients:patient_detail", args=[self.patient.pk]),
            fetch_redirect_response=False,
        )
        self.assertIn(f"temp_creds_{self.patient.pk}", self.client.session)

    def test_explicit_none_allergy_is_not_styled_as_alert(self):
        self.patient.allergy_info = "None"
        self.patient.save(update_fields=["allergy_info"])
        response = self._get()
        content = response.content.decode()
        self.assertIn("None", content)
        self.assertNotIn("pd-tile--alert", content)

    def test_empty_states_for_patient_without_records(self):
        empty_patient = Patient.objects.create(
            patient_number="PAT-D2002",
            first_name="Oscar",
            last_name="Rivera",
            birthdate=date(1995, 3, 2),
            sex="male",
            contact_number="09170001111",
            address="Barangay San Jose",
        )
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("patients:patient_detail", args=[empty_patient.pk]))
        content = response.content.decode()
        self.assertIn("NO BITE INCIDENT YET", content)
        self.assertIn("No bite incident has been recorded for this patient.", content)
        self.assertIn("NO VITALS YET", content)
        self.assertIn("No vital signs recorded for this patient.", content)
        self.assertIn("NO ASSESSMENT YET", content)
        self.assertIn("No doctor assessment has been recorded for this case.", content)
        self.assertIn("Not recorded", content)
        self.assertNotIn("No allergies", content)
        self.assertIn("Allergy status is not documented", content)