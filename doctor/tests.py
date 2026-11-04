"""
Tests for the Doctor Portal clinical encounter.

The encounter page reads the patient chart and lets the doctor complete the
clinical assessment; vital signs are read-only reference data recorded by
clinic staff. A signed PEP plan becomes a dose schedule without touching stock.
"""
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from bite_cases.models import AnimalBiteCase, ExposureClassification
from inventory.models import MedicineInventory, MedicineStockMovement, VaccineInventory
from patients.models import Patient, VitalSign
from vaccination.models import VaccinationSchedule, VaccineDose

from .models import ClinicalAssessment, Prescription

U = get_user_model()


class EncounterTestCase(TestCase):

    def setUp(self):
        self.patient = Patient.objects.create(
            patient_number="PAT-D0001", first_name="Dora", last_name="Test",
            birthdate=datetime.date(1995, 5, 5), contact_number="09170000000",
            allergy_info="None known",
        )
        self.doctor = U.objects.create_user("doc.one", password="pw12345678", role="doctor")
        self.nurse = U.objects.create_user("nurse.one", password="pw12345678", role="nurse")
        self.case = AnimalBiteCase.objects.create(
            case_number="ABC-D0001", patient=self.patient, created_by=self.nurse,
            animal_type="dog", animal_ownership="owned", animal_vax_status="unknown",
            animal_condition="under_observation", exposure_type="bite",
            body_part_affected="right hand", number_of_wounds=1,
            date_time_bite=datetime.datetime(2026, 10, 30, 9, 0),
            date_time_consultation=datetime.datetime(2026, 10, 30, 10, 0),
        )
        self.inventory = VaccineInventory.objects.create(
            vaccine_name="Anti-rabies vaccine", brand="Verorab", batch_number="B-1",
            expiration_date=datetime.date(2027, 1, 1),
            quantity_received=50, quantity_available=50,
        )

    def _payload(self, **overrides):
        data = {
            "assess-chief_complaint": "Dog bite on the right hand, 1 hour ago",
            "assess-history_present_illness": "Bled immediately, washed with soap.",
            "assess-exposure_narrative": "Stray dog bit the right hand while walking home.",
            "assess-animal_type_verified": "dog",
            "assess-category_confirmed": "category_iii",
            "assess-pep_indicated": "on",
            "assess-vaccine_inventory": str(self.inventory.pk),
            "assess-vaccine_lot_number": "B-1",
            "assess-vaccine_dose_ml": "0.5",
            "assess-vaccine_quantity": "5",
            "assess-dose_protocol": "essen",
            "assess-dose_schedule": "5",
            "assess-dose_schedule_date": "2026-10-30",
            "assess-first_dose_date": "2026-10-30",
            "assess-treatment_plan": "Wound irrigation, rabies PEP, antibiotics.",
            "assess-prc_number": "1234567",
            # The hidden field no longer decides finalization; Save validates the
            # completed encounter and signs it server-side.
            "assess-status": "draft",
        }
        data.update(overrides)
        return data

    def _post(self, **overrides):
        self.client.force_login(self.doctor)
        return self.client.post(
            reverse("doctor:assess_case", args=[self.case.pk]), self._payload(**overrides),
            follow=True,
        )


class EncounterLayoutTests(EncounterTestCase):

    def setUp(self):
        super().setUp()
        self.client.force_login(self.doctor)
        self.response = self.client.get(reverse("doctor:assess_case", args=[self.case.pk]))

    def test_page_renders_the_six_sections(self):
        self.assertEqual(self.response.status_code, 200)
        for heading in ("Patient Information", "Medical History", "Bite Incident Details",
                        "Vital Signs", "Clinical Assessment", "Treatment Plan"):
            self.assertContains(self.response, heading)

    def test_header_shows_only_back_title_and_status(self):
        html = self.response.content.decode()
        header = html[html.index("<!-- Top bar -->"):html.index("<!-- PATIENT HEADER")]
        self.assertIn("← Back", header)
        self.assertIn("Clinical Encounter", header)
        self.assertIn(">New<", header)
        for label in ("Overview / Dashboard", "Patient File →", "Print Sheet ↗"):
            self.assertNotIn(label, header)
        self.assertNotIn(self.case.case_number, header)

    def test_patient_chart_is_displayed_read_only(self):
        self.assertNotContains(self.response, 'name="patient-first_name"')
        self.assertNotContains(self.response, 'name="patient-allergy_info"')
        self.assertContains(self.response, "PAT-D0001")
        self.assertContains(self.response, "None known")

    def test_bite_incident_details_show_reported_case_information(self):
        self.assertContains(self.response, "Place of incident")
        self.assertContains(self.response, "First aid given")
        self.assertContains(self.response, "Animal vaccination status")

    def test_assessment_fields_are_editable(self):
        self.assertContains(self.response, 'name="assess-chief_complaint"')

    def test_history_of_present_illness_is_hidden_but_kept_in_the_form(self):
        self.assertNotContains(self.response, "History of present illness")
        self.assertNotContains(self.response, '<textarea name="assess-history_present_illness"')
        self.assertContains(self.response, '<input type="hidden" name="assess-history_present_illness"')

    def test_existing_history_of_present_illness_is_preserved(self):
        ClinicalAssessment.objects.create(
            patient=self.patient, bite_case=self.case, assessed_by=self.doctor,
            chief_complaint="Old complaint", history_present_illness="Bled immediately, washed with soap.",
            category_confirmed="category_ii", status="draft",
        )
        response = self.client.get(reverse("doctor:assess_case", args=[self.case.pk]))
        self.assertContains(response, 'value="Bled immediately, washed with soap."')

    def test_exposure_narrative_is_the_only_visible_section_b_field(self):
        self.assertContains(self.response, "Narrative as verified by the doctor")
        for label in ("Animal (verified)", "Owned / Stray", "Vaccination status",
                      "Wound washed?", "Body part", "No. of wounds",
                      "Date / time of bite (verified)"):
            self.assertNotContains(self.response, ">%s</label>" % label)

    def test_verified_exposure_details_are_submitted_hidden(self):
        ClinicalAssessment.objects.create(
            patient=self.patient, bite_case=self.case, assessed_by=self.doctor,
            chief_complaint="Old complaint", category_confirmed="category_ii", status="draft",
            animal_type_verified="dog", animal_owned_verified="stray",
            animal_vax_verified="not_vaccinated", wound_washed_verified=True,
            body_part_verified="leg", number_of_wounds_verified=2,
            date_time_bite_verified=datetime.datetime(2026, 10, 13, 3, 3, tzinfo=datetime.timezone.utc),
        )
        response = self.client.get(reverse("doctor:assess_case", args=[self.case.pk]))
        for fragment in (
            'name="assess-animal_type_verified" value="dog"',
            'name="assess-animal_owned_verified" value="stray"',
            'name="assess-animal_vax_verified" value="not_vaccinated"',
            'name="assess-wound_washed_verified" value="True"',
            'name="assess-body_part_verified" value="leg"',
            'name="assess-number_of_wounds_verified" value="2"',
            'name="assess-date_time_bite_verified" value="2026-10-13 11:03:00"',
        ):
            self.assertContains(response, fragment)

    def test_section_c_shows_only_the_who_category_dropdown(self):
        self.assertContains(self.response, "Bite Category")
        self.assertContains(self.response, "WHO bite category *")
        self.assertContains(self.response, 'name="assess-category_confirmed"')
        for text in ("Licks intact skin", "Minor scratches, no bleeding", "Transdermal / bat",
                     "Saved to the case record's exposure classification.",
                     ">PEP indicated</label>", ">RIG indicated</label>", ">TT indicated</label>",
                     ">PRC License No.</label>", ">Status *</label>", "Attending Doctor",
                     "Draft = save; Final = sign"):
            self.assertNotContains(self.response, text)

    def test_hidden_section_c_values_are_submitted(self):
        ClinicalAssessment.objects.create(
            patient=self.patient, bite_case=self.case, assessed_by=self.doctor,
            chief_complaint="Old complaint", category_confirmed="category_iii",
            pep_indicated=True, rig_indicated=False, tt_indicated=True,
            prc_number="1234567", status="final",
        )
        response = self.client.get(reverse("doctor:assess_case", args=[self.case.pk]))
        for fragment in (
            'name="assess-pep_indicated" value="True"',
            'name="assess-rig_indicated" value="False"',
            'name="assess-tt_indicated" value="True"',
            'name="assess-prc_number" value="1234567"',
            'name="assess-status" value="final"',
        ):
            self.assertContains(response, fragment)

    def test_section_six_renders_dose_schedule_and_vaccine_brand(self):
        for text in ("Dose Schedule &amp; Vaccine", "Dose schedule *", "Date (1st dose) *",
                     "Vaccine Brand", "Vaccine brand (from stock) *",
                     "Volume per dose (mL)", "Quantity (vials/doses)", "1st dose date",
                     "Treatment, Prescription &amp; Follow-up"):
            self.assertContains(self.response, text)
        for label in ("1 Dose", "2 Doses", "3 Doses", "4 Doses", "5 Doses"):
            self.assertContains(self.response, label)
        for name in ("assess-dose_schedule", "assess-dose_schedule_date",
                     "assess-vaccine_inventory", "assess-vaccine_dose_ml",
                     "assess-vaccine_quantity", "assess-first_dose_date"):
            self.assertContains(self.response, 'name="%s"' % name)

    def test_section_a_only_shows_dose_count_and_date(self):
        self.assertContains(self.response, "Dose Schedule &amp; Vaccine")
        self.assertContains(self.response, "Dose schedule *")
        self.assertContains(self.response, ">Date (1st dose) *</label>")
        for text in (
            "Required number of doses (1st–5th). Choosing 5th Dose creates five separate records.",
            "Confirmed 1st Dose date. The nurse schedules every remaining dose.",
            "Dose schedule protocol",
            "Dose records",
            "Choose a protocol and 1st dose date to preview the records.",
            "Saving this assessment creates the selected number of records",
            "Schedule already set",
        ):
            self.assertNotContains(self.response, text)

    def test_hidden_protocol_keeps_the_saved_value(self):
        ClinicalAssessment.objects.create(
            patient=self.patient, bite_case=self.case, assessed_by=self.doctor,
            chief_complaint="Old complaint", dose_protocol="zagreb", status="draft")
        response = self.client.get(reverse("doctor:assess_case", args=[self.case.pk]))
        self.assertContains(
            response, 'type="hidden" name="assess-dose_protocol" value="zagreb"')
        self.assertNotContains(response, '<select name="assess-dose_protocol"')

    def test_returning_patient_defaults_to_existing_count_and_first_date(self):
        schedule = VaccinationSchedule.objects.create(bite_case=self.case, created_by=self.nurse)
        VaccineDose.objects.create(schedule=schedule, dose_label="Day 0",
                                   scheduled_date=datetime.date(2026, 10, 1),
                                   dose_status="completed", protocol_day=0)
        VaccineDose.objects.create(schedule=schedule, dose_label="Day 3",
                                   scheduled_date=datetime.date(2026, 10, 4),
                                   dose_status="scheduled", protocol_day=3)
        response = self.client.get(reverse("doctor:assess_case", args=[self.case.pk]))
        form = response.context["form"]
        self.assertEqual(form.initial["dose_schedule"], "2")
        self.assertEqual(form.initial["dose_schedule_date"], datetime.date(2026, 10, 1))
        self.assertEqual(form.initial["first_dose_date"], datetime.date(2026, 10, 1))

    def test_vital_signs_section_has_no_inputs(self):
        self.assertNotContains(self.response, 'name="vitals-')
        self.assertNotContains(self.response, "confirm_vitals")

    def test_new_encounter_defaults_day_zero_to_today(self):
        self.assertRegex(
            self.response.content.decode(),
            r'name="assess-first_dose_date"[^>]*value="%s"' % timezone.localdate().isoformat(),
        )

    def test_recorded_vitals_are_displayed_read_only(self):
        VitalSign.objects.create(
            patient=self.patient, taken_by=self.nurse,
            taken_at=datetime.datetime(2026, 10, 30, 9, 30, tzinfo=datetime.timezone.utc),
            bp_systolic=118, bp_diastolic=76, temperature=36.8, pulse_rate=84,
            spo2=98, weight=68, height=165, pain_score=2)
        response = self.client.get(reverse("doctor:assess_case", args=[self.case.pk]))
        for text in ("118 mmHg", "76 mmHg", "36.8 °C", "84 bpm", "98 %",
                     "68.00 kg", "165.00 cm", "2 / 10", "Read-only", "nurse.one"):
            self.assertContains(response, text)
        self.assertNotContains(response, 'name="vitals-')
        self.assertNotContains(response, "never overwritten")


class AssessmentSaveTests(EncounterTestCase):

    def test_chief_complaint_is_required(self):
        response = self._post(**{"assess-chief_complaint": ""})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This field is required.")
        self.assertFalse(ClinicalAssessment.objects.filter(bite_case=self.case).exists())

    def test_signed_pep_assessment_creates_five_doses_with_only_the_first_dated(self):
        response = self._post()
        self.assertEqual(response.status_code, 200)

        assessment = ClinicalAssessment.objects.get(bite_case=self.case)
        self.assertTrue(assessment.is_finalized)
        self.assertEqual(assessment.dose_schedule_plan,
                         "Essen — 5 doses IM (Days 0, 3, 7, 14, 28) (5 doses)")

        schedule = VaccinationSchedule.objects.get(bite_case=self.case)
        self.assertEqual(schedule.created_by, self.doctor)
        doses = list(schedule.doses.order_by("id"))
        self.assertEqual([d.dose_label for d in doses],
                         ["Day 0", "Day 3", "Day 7", "Day 14", "Day 28"])
        self.assertEqual(doses[0].scheduled_date, datetime.date(2026, 10, 30))
        self.assertEqual(doses[0].dose_status, "scheduled")
        for dose in doses[1:]:
            self.assertIsNone(dose.scheduled_date)
            self.assertEqual(dose.dose_status, "pending")
        self.assertEqual([d.protocol_day for d in doses], [0, 3, 7, 14, 28])

        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity_available, 50)
        self.assertEqual(self.inventory.quantity_used, 0)

    def test_zagreb_protocol_with_three_doses(self):
        self._post(**{"assess-dose_protocol": "zagreb", "assess-dose_schedule": "3"})
        schedule = VaccinationSchedule.objects.get(bite_case=self.case)
        self.assertEqual(list(schedule.doses.order_by("id").values_list("dose_label", flat=True)),
                         ["Day 0", "Day 7", "Day 21"])
        self.assertEqual(schedule.doses.filter(dose_status="pending").count(), 2)

    def test_complete_save_finalizes_and_records_the_attestation(self):
        self._post()
        assessment = ClinicalAssessment.objects.get(bite_case=self.case)
        self.assertEqual(assessment.status, "final")
        self.assertTrue(assessment.is_finalized)
        self.assertIsNotNone(assessment.finalized_at)
        self.assertIn("I attest that I personally reviewed", assessment.attestation)

        audit = AuditLog.objects.get(action="DOCTOR_ASSESSMENT_FINALIZED")
        self.assertEqual(audit.user, self.doctor)
        self.assertIn(f"user {self.doctor.pk}", audit.description)
        self.assertIn(self.doctor.username, audit.description)
        self.assertIn(assessment.attestation, audit.description)
        self.assertEqual(audit.target_type, "bite_case")
        self.assertEqual(audit.target_id, self.case.pk)
        self.case.refresh_from_db()
        self.assertEqual(self.case.case_status, "under_treatment")

    def test_hidden_status_does_not_decide_finalization(self):
        self._post(**{"assess-status": "draft"})
        assessment = ClinicalAssessment.objects.get(bite_case=self.case)
        self.assertEqual(assessment.status, "final")
        self.assertTrue(assessment.is_finalized)

    def test_incomplete_save_stays_draft_and_locks_treatment_orders(self):
        response = self._post(**{"assess-treatment_plan": ""})
        self.assertContains(response, "Treatment plan is required.")
        self.assertContains(response, "Saved as Draft")

        assessment = ClinicalAssessment.objects.get(bite_case=self.case)
        self.assertEqual(assessment.status, "draft")
        self.assertFalse(assessment.is_finalized)
        self.assertIsNone(assessment.finalized_at)
        self.assertEqual(assessment.attestation, "")
        self.assertFalse(AuditLog.objects.filter(
            action="DOCTOR_ASSESSMENT_FINALIZED").exists())

        schedule = VaccinationSchedule.objects.get(bite_case=self.case)
        self.assertEqual(schedule.created_by, self.doctor)
        self.assertEqual(schedule.doses.filter(dose_status="scheduled").count(), 1)
        self.assertEqual(schedule.doses.filter(dose_status="pending").count(), 4)
        self.case.refresh_from_db()
        self.assertEqual(self.case.case_status, "new")
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity_available, 50)
        self.assertEqual(self.inventory.quantity_used, 0)

    def test_missing_exposure_narrative_keeps_the_assessment_in_draft(self):
        response = self._post(**{"assess-exposure_narrative": ""})
        self.assertContains(response, "Verified exposure narrative is required.")
        assessment = ClinicalAssessment.objects.get(bite_case=self.case)
        self.assertFalse(assessment.is_finalized)
        self.assertFalse(AuditLog.objects.filter(
            action="DOCTOR_ASSESSMENT_FINALIZED").exists())

    def test_pep_schedule_without_a_vaccine_lot_stays_draft(self):
        response = self._post(**{
            "assess-vaccine_inventory": "",
            "assess-vaccine_lot_number": "",
            "assess-vaccine_brand_plan": "",
        })
        self.assertContains(response, "Select the vaccine lot")
        assessment = ClinicalAssessment.objects.get(bite_case=self.case)
        self.assertFalse(assessment.is_finalized)
        self.assertEqual(assessment.status, "draft")
        self.assertFalse(AuditLog.objects.filter(
            action="DOCTOR_ASSESSMENT_FINALIZED").exists())

    def test_incomplete_resubmission_does_not_overwrite_a_signed_assessment(self):
        self._post()
        signed = ClinicalAssessment.objects.get(bite_case=self.case)
        signature = (signed.finalized_at, signed.attestation, signed.chief_complaint)

        response = self._post(
            **{"assess-chief_complaint": "Changed complaint", "assess-treatment_plan": ""})
        self.assertContains(response, "The signed assessment was not changed.")

        signed.refresh_from_db()
        self.assertEqual(signed.status, "final")
        self.assertEqual(signed.chief_complaint, signature[2])
        self.assertEqual(signed.finalized_at, signature[0])
        self.assertEqual(signed.attestation, signature[1])
        self.assertEqual(AuditLog.objects.filter(
            action="DOCTOR_ASSESSMENT_FINALIZED").count(), 1)

    def test_a_nurse_cannot_finalize_a_doctor_assessment(self):
        self.client.force_login(self.nurse)
        response = self.client.post(
            reverse("doctor:assess_case", args=[self.case.pk]), self._payload())
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ClinicalAssessment.objects.filter(bite_case=self.case).exists())

    def test_assessment_without_pep_still_creates_the_schedule(self):
        payload = self._payload()
        payload.pop("assess-pep_indicated")
        self.client.force_login(self.doctor)
        self.client.post(reverse("doctor:assess_case", args=[self.case.pk]), payload, follow=True)
        self.assertTrue(VaccinationSchedule.objects.filter(bite_case=self.case).exists())
        self.assertEqual(
            VaccineDose.objects.filter(schedule__bite_case=self.case, dose_status="pending").count(), 4)

    def test_protocol_needs_a_first_dose_date(self):
        response = self._post(**{"assess-dose_schedule_date": "", "assess-first_dose_date": ""})
        self.assertContains(response, "Needed to date the 5 doses of this protocol.")
        self.assertFalse(VaccinationSchedule.objects.filter(bite_case=self.case).exists())

    def test_existing_schedule_is_not_duplicated_and_first_date_syncs(self):
        schedule = VaccinationSchedule.objects.create(bite_case=self.case, created_by=self.nurse)
        VaccineDose.objects.create(schedule=schedule, dose_label="Day 0",
                                   scheduled_date=datetime.date(2026, 11, 2))
        self._post(**{"assess-dose_schedule": "5", "assess-first_dose_date": "2026-10-30"})
        self.assertEqual(VaccinationSchedule.objects.filter(bite_case=self.case).count(), 1)
        doses = list(schedule.doses.order_by("id"))
        self.assertEqual(len(doses), 5)
        self.assertEqual(doses[0].scheduled_date, datetime.date(2026, 10, 30))
        self.assertTrue(all(d.scheduled_date is None for d in doses[1:]))
        self.assertTrue(all(d.dose_status == "pending" for d in doses[1:]))

    def test_dose_count_controls_how_many_records_exist(self):
        self._post(**{"assess-dose_schedule": "2"})
        schedule = VaccinationSchedule.objects.get(bite_case=self.case)
        doses = list(schedule.doses.order_by("id"))
        self.assertEqual(len(doses), 2)
        self.assertEqual(doses[0].scheduled_date, datetime.date(2026, 10, 30))
        self.assertIsNone(doses[1].scheduled_date)
        self.assertEqual(doses[1].dose_status, "pending")
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity_used, 0)

    def test_first_dose_date_drives_the_plan_date(self):
        self._post(**{"assess-dose_schedule_date": "2026-11-01",
                      "assess-first_dose_date": "2026-10-30"})
        assessment = ClinicalAssessment.objects.get(bite_case=self.case)
        self.assertEqual(assessment.first_dose_date, datetime.date(2026, 11, 1))
        schedule = VaccinationSchedule.objects.get(bite_case=self.case)
        self.assertEqual(schedule.doses.order_by("id").first().scheduled_date,
                         datetime.date(2026, 11, 1))

    def test_raising_the_count_adds_pending_doses_only(self):
        self._post(**{"assess-dose_schedule": "2"})
        self._post(**{"assess-dose_schedule": "5"})
        schedule = VaccinationSchedule.objects.get(bite_case=self.case)
        doses = list(schedule.doses.order_by("id"))
        self.assertEqual(len(doses), 5)
        self.assertEqual(doses[0].dose_status, "scheduled")
        self.assertTrue(all(d.dose_status == "pending" for d in doses[1:]))

    def test_lowering_the_count_keeps_scheduled_history(self):
        self._post(**{"assess-dose_schedule": "5"})
        schedule = VaccinationSchedule.objects.get(bite_case=self.case)
        second = list(schedule.doses.order_by("id"))[1]
        second.scheduled_date = datetime.date(2026, 11, 2)
        second.dose_status = "scheduled"
        second.save(update_fields=["scheduled_date", "dose_status", "updated_at"])
        self._post(**{"assess-dose_schedule": "2"})
        doses = list(schedule.doses.order_by("id"))
        self.assertEqual(len(doses), 2)
        self.assertEqual(doses[1].scheduled_date, datetime.date(2026, 11, 2))
        self.assertEqual(doses[1].dose_status, "scheduled")

    def test_completed_doses_are_never_redated(self):
        schedule = VaccinationSchedule.objects.create(bite_case=self.case, created_by=self.nurse)
        VaccineDose.objects.create(schedule=schedule, dose_label="Day 0",
                                   scheduled_date=datetime.date(2026, 10, 1),
                                   dose_status="completed")
        self._post(**{"assess-dose_schedule": "1", "assess-dose_schedule_date": "2026-11-10",
                      "assess-first_dose_date": "2026-11-10"})
        dose = VaccineDose.objects.get(schedule=schedule)
        self.assertEqual(dose.scheduled_date, datetime.date(2026, 10, 1))

    def test_saved_schedule_renders_the_read_only_records_table_in_section_a(self):
        self._post(**{"assess-dose_schedule": "2"})
        response = self.client.get(reverse("doctor:assess_case", args=[self.case.pk]))
        self.assertContains(response, "Dose Schedule Records")
        self.assertContains(response, ">1st Dose<")
        self.assertContains(response, ">2nd Dose<")
        self.assertNotContains(response, ">3rd Dose<")
        self.assertContains(response, "Not yet set")
        self.assertContains(response, "Pending Scheduling")
        self.assertContains(response, "Scheduled")

    def test_resaving_does_not_duplicate_the_schedule(self):
        self._post()
        self._post()
        schedule = VaccinationSchedule.objects.get(bite_case=self.case)
        self.assertEqual(VaccinationSchedule.objects.filter(bite_case=self.case).count(), 1)
        self.assertEqual(schedule.doses.count(), 5)

    def test_confirmed_category_is_saved_on_the_case_record(self):
        self._post()
        exposure = ExposureClassification.objects.get(bite_case=self.case)
        self.assertEqual(exposure.category, "category_iii")
        self.case.refresh_from_db()
        self.assertEqual(self.case.case_status, "under_treatment")


class PrescriptionTests(EncounterTestCase):
    """Doctors prescribe several medicines per encounter; stock moves only on dispensing."""

    def setUp(self):
        super().setUp()
        self.amoxicillin = MedicineInventory.objects.create(
            medicine_name="Amoxicillin", generic_name="Amoxicillin trihydrate",
            strength="500 mg", dosage_form="capsule", unit="capsule",
            batch_number="MED-1", expiration_date=datetime.date(2027, 6, 1),
            quantity_received=50, quantity_available=50,
        )
        self.paracetamol = MedicineInventory.objects.create(
            medicine_name="Paracetamol", generic_name="Acetaminophen",
            strength="500 mg", dosage_form="tablet", unit="tablet",
            batch_number="MED-2", expiration_date=datetime.date(2027, 6, 1),
            quantity_received=30, quantity_available=30,
        )

    def _rx_payload(self, rows, initial=None, **overrides):
        """rows: [(medicine, quantity)]; initial: [(pk, medicine, quantity)]."""
        data = self._payload(**overrides)
        initial = initial or []
        data.update({
            "rx-TOTAL_FORMS": str(len(initial) + len(rows)),
            "rx-INITIAL_FORMS": str(len(initial)),
            "rx-MIN_NUM_FORMS": "0",
            "rx-MAX_NUM_FORMS": "1000",
        })
        index = 0
        for pk, medicine, quantity in initial:
            data.update({
                f"rx-{index}-id": str(pk),
                f"rx-{index}-medicine": str(medicine.pk),
                f"rx-{index}-quantity": str(quantity),
            })
            index += 1
        for medicine, quantity in rows:
            data.update({
                f"rx-{index}-medicine": str(medicine.pk),
                f"rx-{index}-quantity": str(quantity),
            })
            index += 1
        return data

    def _post_rx(self, rows, initial=None, **overrides):
        self.client.force_login(self.doctor)
        return self.client.post(
            reverse("doctor:assess_case", args=[self.case.pk]),
            self._rx_payload(rows, initial, **overrides), follow=True,
        )

    def test_section_c_renders_dynamic_prescription_rows(self):
        self.client.force_login(self.doctor)
        response = self.client.get(reverse("doctor:assess_case", args=[self.case.pk]))
        for fragment in ("Prescription (from medicine inventory)",
                         ">Quantity</label>",
                         "Dosage instructions",
                         "+ Add Another Medicine", 'name="rx-0-medicine"',
                         'name="rx-0-quantity"',
                         'name="rx-TOTAL_FORMS"',
                         'name="assess-dosage_instructions"'):
            self.assertContains(response, fragment)
        for absent in ("Dose / volume per dose", 'name="rx-0-dose"',
                       'name="assess-prescription"'):
            self.assertNotContains(response, absent)

    def test_multiple_medicines_save_as_separate_records(self):
        response = self._post_rx(
            [(self.amoxicillin, 21), (self.paracetamol, 10)],
            **{"assess-dosage_instructions":
               "Amoxicillin 1 capsule three times daily × 7 days; Paracetamol 1 tablet every 4 hours as needed"},
        )
        self.assertEqual(response.status_code, 200)

        assessment = ClinicalAssessment.objects.get(bite_case=self.case)
        prescriptions = list(assessment.prescriptions.order_by("id"))
        self.assertEqual(len(prescriptions), 2)
        self.assertEqual({p.medicine_id for p in prescriptions},
                         {self.amoxicillin.pk, self.paracetamol.pk})
        for prescription in prescriptions:
            self.assertEqual(prescription.patient, self.patient)
            self.assertEqual(prescription.bite_case, self.case)
            self.assertEqual(prescription.status, "pending")
            self.assertEqual(prescription.dose, "")
        self.assertIn("Amoxicillin", assessment.prescription)
        self.assertIn("Paracetamol", assessment.prescription)
        self.assertIn("; ", assessment.prescription)
        self.assertIn("three times daily", assessment.prescription)
        self.assertIn("Instructions:", assessment.prescription)

        # Prescribing never touches the shelf.
        self.amoxicillin.refresh_from_db()
        self.paracetamol.refresh_from_db()
        self.assertEqual(self.amoxicillin.quantity_available, 50)
        self.assertEqual(self.paracetamol.quantity_available, 30)
        self.assertFalse(MedicineStockMovement.objects.filter(movement_type="dispense").exists())
        self.assertEqual(ClinicalAssessment.objects.filter(bite_case=self.case).count(), 1)

    def test_dosage_instructions_save_without_medicine_rows(self):
        self._post_rx(
            [], **{"assess-dosage_instructions":
                   "Paracetamol 500 mg every 4 hours as needed × 3 days"})
        assessment = ClinicalAssessment.objects.get(bite_case=self.case)
        self.assertIn("every 4 hours", assessment.dosage_instructions)
        self.assertIn("every 4 hours", assessment.prescription)

    def test_resaving_updates_rows_instead_of_duplicating_them(self):
        self._post_rx([(self.amoxicillin, 21), (self.paracetamol, 10)])
        assessment = ClinicalAssessment.objects.get(bite_case=self.case)
        first, second = assessment.prescriptions.order_by("id")

        self._post_rx(
            [],
            initial=[(first.pk, self.amoxicillin, 14),
                     (second.pk, self.paracetamol, 12)],
            **{"assess-dosage_instructions": "Take after meals × 5 days"},
        )
        self.assertEqual(ClinicalAssessment.objects.filter(bite_case=self.case).count(), 1)
        rows = list(Prescription.objects.filter(assessment=assessment).order_by("id"))
        self.assertEqual([r.pk for r in rows], [first.pk, second.pk])
        self.assertEqual(rows[0].quantity, 14)
        self.assertEqual(rows[0].dose, "")
        self.assertEqual(rows[1].quantity, 12)
        assessment.refresh_from_db()
        self.assertIn("Take after meals", assessment.prescription)

    def test_existing_rows_submit_their_primary_key(self):
        self._post_rx([(self.amoxicillin, 21)])
        self.client.force_login(self.doctor)
        response = self.client.get(reverse("doctor:assess_case", args=[self.case.pk]))
        prescription = Prescription.objects.get(bite_case=self.case)
        self.assertContains(response, 'name="rx-0-id" value="%d"' % prescription.pk)

    def test_quantity_above_stock_is_rejected(self):
        response = self._post_rx([(self.amoxicillin, 99)])
        self.assertContains(response, "available in stock")
        self.assertFalse(ClinicalAssessment.objects.filter(bite_case=self.case).exists())
        self.assertFalse(Prescription.objects.exists())

    def test_duplicate_medicine_rows_are_rejected(self):
        response = self._post_rx([(self.amoxicillin, 7), (self.amoxicillin, 7)])
        self.assertContains(response, "already prescribed")
        self.assertFalse(ClinicalAssessment.objects.filter(bite_case=self.case).exists())

    def test_dispensed_rows_are_locked_history(self):
        self._post_rx([(self.amoxicillin, 21)])
        prescription = Prescription.objects.get(bite_case=self.case)
        prescription.status = "dispensed"
        prescription.save(update_fields=["status"])

        response = self.client.get(reverse("doctor:assess_case", args=[self.case.pk]))
        self.assertContains(response, "Dispensed")
        self.assertContains(response, 'name="rx-0-medicine"', html=False)
        self.assertContains(response, "disabled")

        self._post_rx([], initial=[(prescription.pk, self.amoxicillin, 99)])
        prescription.refresh_from_db()
        self.assertEqual(prescription.dose, "")
        self.assertEqual(prescription.quantity, 21)
        self.assertTrue(Prescription.objects.filter(pk=prescription.pk).exists())

    def test_legacy_free_text_prescription_survives_until_rows_change(self):
        assessment = ClinicalAssessment.objects.create(
            patient=self.patient, bite_case=self.case, assessed_by=self.doctor,
            chief_complaint="Old visit", prescription="Amoxicillin 500 mg 1 cap TID x 7 days",
            status="draft")
        self.client.force_login(self.doctor)
        data = self._payload()
        data.update({"rx-TOTAL_FORMS": "1", "rx-INITIAL_FORMS": "0",
                     "rx-MIN_NUM_FORMS": "0", "rx-MAX_NUM_FORMS": "1000"})
        self.client.post(reverse("doctor:assess_case", args=[self.case.pk]), data, follow=True)
        assessment.refresh_from_db()
        self.assertEqual(assessment.prescription,
                         "Amoxicillin 500 mg 1 cap TID x 7 days")

    def test_general_encounter_saves_prescriptions_without_a_case(self):
        other = Patient.objects.create(
            patient_number="PAT-D0002", first_name="Gene", last_name="Ral",
            birthdate=datetime.date(1990, 1, 1), contact_number="09170000001",
        )
        self.client.force_login(self.doctor)
        data = {
            "assess-chief_complaint": "Fever review",
            "assess-exposure_narrative": "No animal exposure; review of febrile illness.",
            "assess-category_confirmed": "category_i",
            "assess-dose_schedule": "1",
            "assess-dose_schedule_date": "2026-10-30",
            "assess-first_dose_date": "2026-10-30",
            "assess-treatment_plan": "Symptomatic care and review in 48 hours.",
            "assess-status": "draft",
            "rx-TOTAL_FORMS": "1", "rx-INITIAL_FORMS": "0",
            "rx-MIN_NUM_FORMS": "0", "rx-MAX_NUM_FORMS": "1000",
            "rx-0-medicine": str(self.paracetamol.pk),
            "rx-0-quantity": "6",
        }
        self.client.post(reverse("doctor:assess_patient", args=[other.pk]), data, follow=True)
        prescription = Prescription.objects.get(patient=other)
        self.assertIsNone(prescription.bite_case)
        self.assertEqual(prescription.assessment.bite_case, None)
        self.assertTrue(prescription.assessment.is_finalized)
        self.assertEqual(AuditLog.objects.filter(
            user=self.doctor, action="DOCTOR_ASSESSMENT_FINALIZED").count(), 1)


class EncounterVitalsReadOnlyTests(EncounterTestCase):
    """Vitals are recorded by clinic staff — the doctor encounter never writes them."""

    def test_doctor_cannot_record_vitals_from_the_encounter(self):
        response = self._post(**{"vitals-bp_systolic": "120", "vitals-bp_diastolic": "80",
                                 "vitals-temperature": "36.8", "vitals-pulse_rate": "84"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(VitalSign.objects.filter(patient=self.patient).exists())
        self.assertTrue(ClinicalAssessment.objects.filter(bite_case=self.case).exists())

    def test_doctor_cannot_overwrite_recorded_vitals(self):
        recorded = VitalSign.objects.create(
            patient=self.patient, taken_by=self.nurse,
            taken_at=datetime.datetime(2026, 10, 30, 10, 0, tzinfo=datetime.timezone.utc),
            bp_systolic=118, bp_diastolic=76, temperature=36.8, pulse_rate=84)
        self._post(**{"vitals-bp_systolic": "130", "vitals-bp_diastolic": "90",
                      "vitals-temperature": "38.0", "confirm_vitals": "1"})
        recorded.refresh_from_db()
        self.assertEqual(VitalSign.objects.filter(patient=self.patient).count(), 1)
        self.assertEqual(recorded.bp_systolic, 118)
        self.assertEqual(recorded.bp_diastolic, 76)
        self.assertEqual(str(recorded.temperature), "36.8")
        self.assertEqual(recorded.taken_by, self.nurse)

    def test_general_encounter_ignores_posted_vitals(self):
        patient = Patient.objects.create(
            patient_number="PAT-D0004", first_name="Gina", last_name="General",
            birthdate=datetime.date(1992, 2, 2), contact_number="09170000004")
        self.client.force_login(self.doctor)
        response = self.client.post(
            reverse("doctor:assess_patient", args=[patient.pk]),
            {"assess-chief_complaint": "Fever and wound check", "assess-status": "draft",
             "vitals-bp_systolic": "120", "vitals-temperature": "37.0"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(VitalSign.objects.filter(patient=patient).exists())


class PatientRecordIsAdminOwnedTests(TestCase):

    def test_doctor_cannot_open_the_patient_edit_form(self):
        patient = Patient.objects.create(
            patient_number="PAT-D0002", first_name="Ed", last_name="It",
            birthdate=datetime.date(1990, 1, 1), contact_number="09170000001")
        doctor = U.objects.create_user("doc.two", password="pw12345678", role="doctor")
        self.client.force_login(doctor)
        response = self.client.post(
            reverse("patients:patient_edit", args=[patient.pk]),
            {"first_name": "Changed", "last_name": "Name", "birthdate": "1990-01-01",
             "contact_number": "09170000001", "address": "Somewhere"},
        )
        self.assertEqual(response.status_code, 302)
        patient.refresh_from_db()
        self.assertEqual(patient.first_name, "Ed")

    def test_nurse_can_still_record_vitals_from_the_patient_page(self):
        patient = Patient.objects.create(
            patient_number="PAT-D0003", first_name="Nora", last_name="Nurse",
            birthdate=datetime.date(1990, 1, 1), contact_number="09170000002")
        nurse = U.objects.create_user("nurse.two", password="pw12345678", role="nurse")
        self.client.force_login(nurse)
        response = self.client.post(
            reverse("patients:patient_vital_create", args=[patient.pk]),
            {"taken_at": "2026-10-30T09:00", "bp_systolic": "118", "bp_diastolic": "76"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(VitalSign.objects.filter(patient=patient).count(), 1)


class AssessmentDetailReadOnlyTests(EncounterTestCase):
    """The My Assessments → Open page is a strict read-only view of saved data."""

    def setUp(self):
        super().setUp()
        self.assessment = ClinicalAssessment.objects.create(
            patient=self.patient,
            bite_case=self.case,
            assessed_by=self.doctor,
            chief_complaint="Puncture wound on the right hand after a dog bite",
            exposure_narrative="Dog owned by a neighbour bit the patient one hour ago.",
            category_confirmed="category_iii",
            treatment_plan="Wound irrigation and rabies post-exposure prophylaxis.",
            dosage_instructions="Amoxicillin 1 capsule three times daily × 7 days.",
            follow_up_instructions="Return on the scheduled dose dates.",
            additional_notes="No known drug allergies. Allergy re-checked.",
            vaccine_brand_plan="Anti-rabies vaccine (Verorab)",
            vaccine_dose_ml="0.5",
            vaccine_quantity=2,
            first_dose_date=datetime.date(2026, 11, 1),
            prc_number="1234567",
            status="final",
            is_finalized=True,
        )
        self.schedule = VaccinationSchedule.objects.create(
            bite_case=self.case, created_by=self.nurse)
        VaccineDose.objects.create(
            schedule=self.schedule, dose_label="Day 0",
            scheduled_date=datetime.date(2026, 11, 1),
            actual_date=datetime.date(2026, 11, 1),
            dose_status="completed", protocol_day=0)
        VaccineDose.objects.create(
            schedule=self.schedule, dose_label="Day 3",
            scheduled_date=None, dose_status="pending", protocol_day=3)

        self.amoxicillin = MedicineInventory.objects.create(
            medicine_name="Amoxicillin", generic_name="Amoxicillin trihydrate",
            strength="500 mg", dosage_form="capsule", unit="capsule",
            batch_number="MED-D1", expiration_date=datetime.date(2027, 6, 1),
            quantity_received=50, quantity_available=50,
        )
        self.paracetamol = MedicineInventory.objects.create(
            medicine_name="Paracetamol", generic_name="Acetaminophen",
            strength="500 mg", dosage_form="tablet", unit="tablet",
            batch_number="MED-D2", expiration_date=datetime.date(2027, 6, 1),
            quantity_received=30, quantity_available=30,
        )
        Prescription.objects.create(
            assessment=self.assessment, patient=self.patient, bite_case=self.case,
            medicine=self.amoxicillin, quantity=21)
        Prescription.objects.create(
            assessment=self.assessment, patient=self.patient, bite_case=self.case,
            medicine=self.paracetamol, quantity=10)
        VitalSign.objects.create(
            patient=self.patient, taken_by=self.nurse,
            taken_at=datetime.datetime(2026, 10, 30, 9, 30, tzinfo=datetime.timezone.utc),
            bp_systolic=118, bp_diastolic=76, temperature=36.8, pulse_rate=84,
            spo2=98, weight=68, height=165, pain_score=2)

        self.url = reverse("doctor:assessment_detail", args=[self.assessment.pk])
        self.client.force_login(self.doctor)

    def test_my_assessments_open_link_goes_to_the_read_only_detail(self):
        response = self.client.get(reverse("doctor:assessment_list"))
        self.assertContains(response, self.url)
        self.assertContains(response, ">View</a>")
        self.assertNotContains(response, ">Open</a>")
        self.assertNotContains(
            response, reverse("doctor:assess_case", args=[self.case.pk]))

    def test_header_shows_patient_case_doctor_status_and_view_only(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        for text in ("Assessment Details", "Dora Test", "ABC-D0001",
                     "doc.one", "Final", "View Only",
                     timezone.localtime(self.assessment.encounter_date)
                     .strftime("%B %d, %Y")):
            self.assertContains(response, text)

    def test_six_sections_render_in_the_required_order(self):
        html = self.client.get(self.url).content.decode()
        markers = [
            'id="section-patient"', 'id="section-history"', 'id="section-incident"',
            'id="section-vitals"', 'id="section-assessment"', 'id="section-plan"',
        ]
        self.assertEqual([html.index(m) for m in markers],
                         sorted(html.index(m) for m in markers))

    def test_patient_information_has_separate_containers(self):
        response = self.client.get(self.url)
        for label in ("Patient ID", "Full name", "Birthdate", "Age", "Sex",
                      "Civil status", "Address", "Contact number",
                      "Parent / Guardian", "Emergency contact", "Registered"):
            self.assertContains(response, ">%s</p>" % label)
        self.assertContains(response, "PAT-D0001")
        self.assertContains(response, "Dora Test")

    def test_missing_medical_history_is_not_treated_as_no_allergies(self):
        self.patient.medical_history = ""
        self.patient.allergy_info = ""
        self.patient.previous_rabies_vax = ""
        self.patient.remarks = ""
        self.patient.save()
        response = self.client.get(self.url)
        html = response.content.decode()
        history = html[html.index('id="section-history"'):html.index('id="section-incident"')]
        self.assertEqual(history.count("Not recorded"), 4)
        self.assertNotContains(response, "No allergies")

    def test_bite_incident_and_vitals_show_saved_values_with_units(self):
        response = self.client.get(self.url)
        for text in ("Bite Incident Details", "right hand · 1 wound(s)",
                     "Animal vaccination status", "First aid given",
                     "118 mmHg", "76 mmHg", "36.8 °C", "84 bpm", "98 %",
                     "68.00 kg", "165.00 cm", "2 / 10"):
            self.assertContains(response, text)

    def test_section_five_follows_chief_complaint_exposure_and_category_order(self):
        html = self.client.get(self.url).content.decode()
        markers = ("Chief Complaint", "Exposure Narrative (Verified)", "Bite Category")
        positions = [html.index(m) for m in markers]
        self.assertEqual(positions, sorted(positions))
        self.assertContains(self.client.get(self.url),
                            "Puncture wound on the right hand after a dog bite")
        self.assertContains(self.client.get(self.url),
                            "Dog owned by a neighbour bit the patient one hour ago.")
        self.assertContains(self.client.get(self.url), "Category III")

    def test_dose_schedule_records_only_show_the_prescribed_rows(self):
        response = self.client.get(self.url)
        self.assertEqual(response.context["doses"], list(self.schedule.doses.all()))
        self.assertEqual(len(response.context["doses"]), 2)
        self.assertContains(response, "Dose Schedule Records")
        self.assertContains(response, "2 Doses")
        self.assertContains(response, ">1st Dose<")
        self.assertContains(response, ">2nd Dose<")
        self.assertNotContains(response, ">3rd Dose<")
        self.assertContains(response, "November 01, 2026")
        self.assertContains(response, "Not yet set")
        self.assertContains(response, "Completed")
        self.assertContains(response, "Pending Scheduling")

    def test_vaccine_and_treatment_plan_follow_the_abc_order(self):
        html = self.client.get(self.url).content.decode()
        markers = ("Dose Schedule &amp; Vaccine", "Vaccine Brand",
                   "Treatment, Prescription &amp; Follow-up")
        positions = [html.index(m) for m in markers]
        self.assertEqual(positions, sorted(positions))

    def test_prescriptions_render_as_separate_medicine_rows_without_controls(self):
        response = self.client.get(self.url)
        self.assertEqual(list(response.context["prescriptions"]),
                         list(self.assessment.prescriptions.order_by("id")))
        for text in ("Amoxicillin", "Paracetamol", "21 capsules", "10 tablets",
                     "Prescription (from medicine inventory)", ">Quantity<",
                     "Dosage instructions", "Follow-up instructions",
                     "Additional notes / allergy re-check"):
            self.assertContains(response, text)
        for text in ("Add Another Medicine", "data-rx-row", "Remove",
                     "Save Assessment", "Update Final Assessment"):
            self.assertNotContains(response, text)

    def test_legacy_prescription_still_shows_a_quantity_tile(self):
        self.assessment.prescriptions.all().delete()
        self.assessment.prescription = "Paracetamol 500 mg every 6 hours"
        self.assessment.save()
        html = self.client.get(self.url).content.decode()
        section = html[html.index("Treatment, Prescription &amp; Follow-up"):html.index("</section>", html.index("Treatment, Prescription &amp; Follow-up"))]
        self.assertIn("Paracetamol 500 mg every 6 hours", section)
        self.assertIn(">Quantity<", section)
        self.assertIn("Not recorded", section)

    def test_assessment_without_prescription_shows_both_placeholder_tiles(self):
        self.assessment.prescriptions.all().delete()
        self.assessment.prescription = ""
        self.assessment.save()
        html = self.client.get(self.url).content.decode()
        section = html[html.index("Treatment, Prescription &amp; Follow-up"):html.index("</section>", html.index("Treatment, Prescription &amp; Follow-up"))]
        self.assertGreaterEqual(section.count(">Quantity<"), 1)
        self.assertIn(">Prescription<", section)
        self.assertEqual(section.count("Not recorded"), 2)

    def test_page_has_no_assessment_inputs(self):
        response = self.client.get(self.url)
        for fragment in ('name="assess-', 'name="rx-', 'name="vitals-',
                         "<textarea", "<select", 'type="date"'):
            self.assertNotContains(response, fragment)

    def test_print_sheet_button_and_a4_print_styles_are_present(self):
        response = self.client.get(self.url)
        self.assertContains(response, "Print Sheet")
        self.assertContains(response, "window.print()")
        self.assertContains(response, "size: A4 portrait")
        self.assertContains(response, "display: table-header-group")

    def test_page_rejects_writes(self):
        response = self.client.post(self.url, {"assess-chief_complaint": "Changed"})
        self.assertEqual(response.status_code, 405)
        self.assessment.refresh_from_db()
        self.assertEqual(self.assessment.chief_complaint,
                         "Puncture wound on the right hand after a dog bite")

    def test_viewing_does_not_change_schedule_records_assessment_or_stock(self):
        doses_before = list(VaccineDose.objects.values())
        assessment_before = ClinicalAssessment.objects.get(pk=self.assessment.pk)
        stock_before = (self.amoxicillin.quantity_available,
                        self.amoxicillin.quantity_dispensed)

        self.client.get(self.url)

        self.assertEqual(list(VaccineDose.objects.values()), doses_before)
        self.assessment.refresh_from_db()
        self.assertEqual(self.assessment.updated_at, assessment_before.updated_at)
        self.amoxicillin.refresh_from_db()
        self.assertEqual((self.amoxicillin.quantity_available,
                          self.amoxicillin.quantity_dispensed), stock_before)
        self.assertFalse(MedicineStockMovement.objects.filter(
            medicine__in=[self.amoxicillin, self.paracetamol]).exists())

    def test_only_the_owning_doctor_can_open_the_detail_page(self):
        other_doctor = U.objects.create_user(
            "doc.other.detail", password="pw12345678", role="doctor")
        self.client.force_login(other_doctor)
        self.assertEqual(self.client.get(self.url).status_code, 404)

        self.client.force_login(self.nurse)
        self.assertEqual(self.client.get(self.url).status_code, 302)

        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_general_encounter_without_a_bite_case_renders_read_only(self):
        patient = Patient.objects.create(
            patient_number="PAT-D0900", first_name="Gene", last_name="Ral",
            birthdate=datetime.date(1990, 1, 1), contact_number="09170000900")
        assessment = ClinicalAssessment.objects.create(
            patient=patient, bite_case=None, assessed_by=self.doctor,
            chief_complaint="Fever review", category_confirmed="category_i",
            status="draft")
        response = self.client.get(
            reverse("doctor:assessment_detail", args=[assessment.pk]))
        self.assertContains(response, "General encounter")
        self.assertContains(response, "No bite case is linked")
        self.assertContains(response, "No dose records have been created")
        self.assertContains(response, "Draft")
        self.assertNotContains(response, 'name="assess-')


class DoctorDashboardTests(TestCase):
    """The redesigned dashboard must stay scoped to the logged-in doctor."""

    def setUp(self):
        self.patient = Patient.objects.create(
            patient_number="PAT-D0100", first_name="Maria", last_name="Santos",
            birthdate=datetime.date(1990, 1, 1), contact_number="09170000100")
        self.other_patient = Patient.objects.create(
            patient_number="PAT-D0101", first_name="Other", last_name="Patient",
            birthdate=datetime.date(1991, 1, 1), contact_number="09170000101")
        self.doctor = U.objects.create_user(
            "doc.dash", password="pw12345678", role="doctor",
            first_name="Maria", last_name="Santos")
        self.other_doctor = U.objects.create_user("doc.other", password="pw12345678", role="doctor")
        self.nurse = U.objects.create_user("nurse.dash", password="pw12345678", role="nurse")

        def case(number, patient):
            return AnimalBiteCase.objects.create(
                case_number=number, patient=patient, created_by=self.nurse,
                animal_type="dog", animal_ownership="owned", animal_vax_status="unknown",
                animal_condition="under_observation", exposure_type="bite",
                body_part_affected="left leg",
                date_time_bite=datetime.datetime(2025, 3, 1, 9, 0))

        self.unclaimed = case("ABC-D0100", self.patient)
        self.my_draft_case = case("ABC-D0101", self.patient)
        self.my_final_case = case("ABC-D0102", self.patient)
        self.other_case = case("ABC-D0103", self.other_patient)

        self.my_draft = ClinicalAssessment.objects.create(
            patient=self.patient, bite_case=self.my_draft_case, assessed_by=self.doctor,
            chief_complaint="Minor scratch", category_confirmed="category_ii", status="draft")
        self.my_final = ClinicalAssessment.objects.create(
            patient=self.patient, bite_case=self.my_final_case, assessed_by=self.doctor,
            chief_complaint="Bite wound", category_confirmed="category_i", status="final",
            is_finalized=True)
        self.other_final = ClinicalAssessment.objects.create(
            patient=self.other_patient, bite_case=self.other_case, assessed_by=self.other_doctor,
            chief_complaint="Bite wound", category_confirmed="category_iii", status="final",
            is_finalized=True)

        # Stable recorded months for the chart and month filter.
        AnimalBiteCase.objects.filter(pk=self.unclaimed.pk).update(
            created_at=datetime.datetime(2025, 3, 10, 10, 0, tzinfo=datetime.timezone.utc))
        AnimalBiteCase.objects.filter(pk=self.my_draft_case.pk).update(
            created_at=datetime.datetime(2025, 3, 12, 10, 0, tzinfo=datetime.timezone.utc))
        AnimalBiteCase.objects.filter(pk=self.my_final_case.pk).update(
            created_at=datetime.datetime(2025, 8, 2, 10, 0, tzinfo=datetime.timezone.utc))
        AnimalBiteCase.objects.filter(pk=self.other_case.pk).update(
            created_at=datetime.datetime(2025, 3, 15, 10, 0, tzinfo=datetime.timezone.utc))

        self.client.force_login(self.doctor)

    def test_dashboard_is_doctor_only(self):
        self.client.force_login(self.nurse)
        response = self.client.get(reverse("doctor:dashboard"))
        self.assertEqual(response.status_code, 302)

    def test_page_renders_real_doctor_name_and_notifications(self):
        response = self.client.get(reverse("doctor:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Welcome back, Dr. Maria Santos!")
        self.assertContains(response, 'aria-label="Notifications"')
        self.assertEqual(response.context["bell_queue"], 1)
        self.assertEqual(response.context["bell_drafts"], 1)
        self.assertContains(response, "Waiting in queue")

    def test_stats_are_scoped_to_the_logged_in_doctor(self):
        response = self.client.get(reverse("doctor:dashboard"))
        self.assertEqual(response.context["my_total"], 2)
        self.assertEqual(response.context["draft_assess"], 1)
        self.assertEqual(response.context["final_today"], 1)
        self.assertEqual(response.context["pending_assess"], 1)

    def test_who_donuts_count_only_my_confirmed_categories(self):
        response = self.client.get(reverse("doctor:dashboard"))
        categories = {row["key"]: row for row in response.context["who_categories"]}
        self.assertEqual(categories["category_i"]["count"], 1)
        self.assertEqual(categories["category_ii"]["count"], 1)
        self.assertEqual(categories["category_iii"]["count"], 0)
        self.assertEqual(categories["category_i"]["pct"], 50)

    def test_monthly_series_uses_the_selected_year(self):
        response = self.client.get(reverse("doctor:dashboard"), {"year": 2025})
        series = {row["month"]: row["count"] for row in response.context["monthly_series"]}
        self.assertEqual(series[3], 3)
        self.assertEqual(series[8], 1)
        self.assertEqual(response.context["monthly_total"], 4)
        self.assertEqual(response.context["selected_year"], 2025)

    def test_queue_hides_other_doctors_work(self):
        response = self.client.get(reverse("doctor:dashboard"))
        numbers = [case.case_number for case in response.context["queue"]]
        self.assertIn(self.unclaimed.case_number, numbers)
        self.assertIn(self.my_draft_case.case_number, numbers)
        self.assertIn(self.my_final_case.case_number, numbers)
        self.assertNotIn(self.other_case.case_number, numbers)

    def test_queue_rows_carry_status_and_category(self):
        response = self.client.get(reverse("doctor:dashboard"))
        rows = {case.case_number: case for case in response.context["queue"]}
        self.assertEqual(rows[self.unclaimed.case_number].assessment_status, "pending")
        self.assertEqual(rows[self.my_draft_case.case_number].assessment_status, "for_assessment")
        self.assertEqual(rows[self.my_draft_case.case_number].who_category, "category_ii")
        self.assertEqual(rows[self.my_final_case.case_number].assessment_status, "completed")

    def test_month_filter_narrows_the_queue(self):
        response = self.client.get(reverse("doctor:dashboard"), {"year": 2025, "month": 3})
        numbers = [case.case_number for case in response.context["queue"]]
        self.assertIn(self.unclaimed.case_number, numbers)
        self.assertIn(self.my_draft_case.case_number, numbers)
        self.assertNotIn(self.my_final_case.case_number, numbers)


class DoctorSidebarTests(EncounterTestCase):

    def test_medicine_inventory_is_hidden_from_the_doctor_sidebar(self):
        self.client.force_login(self.doctor)
        response = self.client.get(reverse("doctor:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Medicine Inventory")
        self.assertContains(response, "Clinical Assessment")

    def test_medicine_inventory_route_still_works_for_doctors(self):
        self.client.force_login(self.doctor)
        response = self.client.get(reverse("inventory:medicine_list"))
        self.assertEqual(response.status_code, 200)

    def test_other_portals_keep_medicine_inventory(self):
        self.client.force_login(self.nurse)
        self.assertContains(self.client.get(reverse("nurse:dashboard")), "Medicine Inventory")

        admin = U.objects.create_user("admin.sidebar", password="pw12345678", role="admin")
        self.client.force_login(admin)
        self.assertContains(self.client.get(reverse("dashboard:index")), "Medicine Inventory")
