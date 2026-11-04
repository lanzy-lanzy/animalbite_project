"""Nurse Portal → View Doctor Findings read-only assessment page."""
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from bite_cases.models import AnimalBiteCase
from doctor.models import ClinicalAssessment, Prescription
from inventory.models import MedicineInventory, VaccineInventory
from patients.models import Barangay, Patient, VitalSign
from vaccination.models import VaccinationSchedule, VaccineDose
from vaccination.schedule_utils import ensure_vaccination_schedule

U = get_user_model()


class DoctorFindingDetailTests(TestCase):

    def setUp(self):
        self.barangay = Barangay.objects.create(name="Dumingag")
        self.admin = U.objects.create_user(
            "admin.findings", password="pw12345678", role="admin",
            first_name="Ada", last_name="Admin",
        )
        self.nurse = U.objects.create_user(
            "nurse.findings", password="pw12345678", role="nurse",
            first_name="Nina", last_name="Nurse",
        )
        self.doctor = U.objects.create_user(
            "doctor.findings", password="pw12345678", role="doctor",
            first_name="Dana", last_name="Doctor",
        )
        self.patient = Patient.objects.create(
            patient_number="PAT-D0001", first_name="Record", last_name="Patient",
            birthdate=datetime.date(1990, 4, 4), sex="female", civil_status="married",
            contact_number="09171234567", address="Purok 2", barangay=self.barangay,
            parent_guardian="Maria Patient", emergency_contact_name="Jose Patient",
            emergency_contact_number="09179876543", created_by=self.admin,
        )
        self.case = AnimalBiteCase.objects.create(
            case_number="ABC-D0001", patient=self.patient, created_by=self.admin,
            animal_type="dog", exposure_type="bite",
            date_time_bite=datetime.datetime(2026, 10, 1, 9, 30),
            place_of_incident="Purok 2", incident_barangay="Dumingag",
            animal_ownership="owned", animal_vax_status="vaccinated",
            animal_condition="alive", body_part_affected="left forearm",
            number_of_wounds=2, wound_washed_immediately=True,
            first_aid_given="Washed with soap and water",
        )
        AnimalBiteCase.objects.filter(pk=self.case.pk).update(
            date_time_consultation=timezone.make_aware(datetime.datetime(2026, 10, 1, 10, 0))
        )
        self.case.refresh_from_db()
        self.schedule, _ = ensure_vaccination_schedule(self.case, created_by=self.nurse)
        self.assessment = self._assessment(self.case, chief_complaint="Dog bite on the left forearm")
        self.medicine = MedicineInventory.objects.create(
            medicine_name="Amoxicillin", strength="500 mg", dosage_form="capsule",
            unit="capsule", batch_number="MED-1", expiration_date=datetime.date(2027, 1, 1),
            quantity_received=100, quantity_available=100,
        )
        Prescription.objects.create(
            assessment=self.assessment, patient=self.patient, bite_case=self.case,
            medicine=self.medicine, quantity=21,
        )
        VitalSign.objects.create(
            patient=self.patient, taken_at=timezone.now(),
            bp_systolic=120, bp_diastolic=80, temperature=36.7,
            pulse_rate=72, spo2=98, weight=55, height=160, bmi=21.48,
            pain_score=3, taken_by=self.nurse,
        )
        self.url = reverse("nurse:doctor_finding_detail", args=[self.case.pk])

    def _assessment(self, case, **overrides):
        data = {
            "patient": self.patient,
            "bite_case": case,
            "assessed_by": self.doctor,
            "chief_complaint": "Dog bite on the left forearm",
            "exposure_narrative": "Patient was bitten by the family dog while feeding it.",
            "category_confirmed": "category_iii",
            "dose_protocol": "essen",
            "first_dose_date": datetime.date(2026, 10, 1),
            "vaccine_brand_plan": "Verorab",
            "vaccine_dose_ml": "0.5",
            "vaccine_quantity": 5,
            "treatment_plan": "Complete the Essen regimen and monitor the wound.",
            "dosage_instructions": "1 tablet every 8 hours for 7 days.",
            "follow_up_instructions": "Return on Day 3 and Day 7.",
            "additional_notes": "No known drug allergies.",
            "is_finalized": True,
            "status": "final",
        }
        data.update(overrides)
        return ClinicalAssessment.objects.create(**data)

    def _login(self, user=None):
        self.client.force_login(user or self.nurse)

    # -------------------------------------------------- access
    def test_nurse_can_open_the_findings_page(self):
        self._login()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Doctor Findings")
        self.assertContains(response, "Read Only")

    def test_non_nurse_staff_are_redirected(self):
        for user in (self.doctor, self.admin):
            self.client.force_login(user)
            response = self.client.get(self.url)
            self.assertRedirects(
                response, reverse("dashboard:index"), fetch_redirect_response=False
            )

    def test_anonymous_user_is_sent_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_page_is_get_only(self):
        self._login()
        self.assertEqual(self.client.post(self.url).status_code, 405)

    def test_archived_case_is_admin_only(self):
        AnimalBiteCase.objects.filter(pk=self.case.pk).update(case_status="archived")
        self._login()
        self.assertRedirects(self.client.get(self.url), reverse("bite_cases:case_list"))

    # -------------------------------------------------- header + layout
    def test_header_values_come_from_the_record(self):
        first = self.schedule.doses.order_by("id").first()
        VaccineDose.objects.filter(pk=first.pk).update(
            dose_status="completed", actual_date=datetime.date(2026, 10, 1),
            scheduled_date=datetime.date(2026, 10, 1),
        )
        self._login()
        response = self.client.get(self.url)
        self.assertContains(response, "record-hero__initials")
        self.assertContains(response, ">RP</span>")
        self.assertContains(response, self.patient.full_name())
        self.assertContains(response, self.patient.patient_number)
        self.assertContains(response, self.case.case_number)
        self.assertContains(response, "October 01, 2026")
        self.assertContains(response, "5 Doses")
        self.assertContains(response, "ASSESSMENT FINALIZED")

    def test_sections_render_in_required_vertical_order(self):
        self._login()
        content = self.client.get(self.url).content.decode()
        markers = [
            "1</span> Patient Information",
            "2</span> Animal Bite Case",
            "3</span> Vital Signs",
            "4</span> Clinical Assessment",
            "Chief Complaint",
            "Exposure Narrative (Verified)",
            "Bite Category",
            "Dose Schedule &amp; Vaccine",
            "Dose Schedule Records",
            "Vaccine Brand",
            "Treatment, Prescription &amp; Follow-up",
        ]
        positions = [content.index(marker) for marker in markers]
        self.assertEqual(positions, sorted(positions))

    # -------------------------------------------------- patient + case data
    def test_all_patient_information_is_shown(self):
        self._login()
        response = self.client.get(self.url)
        for value in (
            "PAT-D0001", "Record Patient", "April 04, 1990", "Female", "Married",
            self.patient.contact_display, "Maria Patient", "Jose Patient",
            self.patient.emergency_contact_display,
            "Purok 2", "Dumingag", "Ada Admin",
        ):
            self.assertContains(response, value)

    def test_all_bite_case_information_is_shown(self):
        self._login()
        response = self.client.get(self.url)
        for value in (
            "ABC-D0001", "New", "October 01, 2026", "09:30 AM", "Purok 2",
            "Dog", "Owned", "Alive", "Vaccinated", "Bite", "2",
            "left forearm", "Yes", "Washed with soap and water",
        ):
            self.assertContains(response, value)

    def test_selected_case_never_mixes_another_case_of_the_same_patient(self):
        second = AnimalBiteCase.objects.create(
            case_number="ABC-D0002", patient=self.patient, created_by=self.admin,
            animal_type="cat", exposure_type="scratch",
            date_time_bite=datetime.datetime(2026, 10, 5, 9, 0),
        )
        self._assessment(second, chief_complaint="Assessment for the second case")
        self._login()
        response = self.client.get(reverse("nurse:doctor_finding_detail", args=[second.pk]))
        self.assertContains(response, "ABC-D0002")
        self.assertContains(response, "Assessment for the second case")
        self.assertNotContains(response, "ABC-D0001")
        self.assertNotContains(response, "Dog bite on the left forearm")

    # -------------------------------------------------- vitals
    def test_recorded_vitals_render(self):
        self._login()
        response = self.client.get(self.url)
        for value in ("120/80 mmHg", "36.7", "72 bpm", "98 %", "55", "160", "21.48", "3 / 10", "Nina Nurse"):
            self.assertContains(response, value)
        self.assertNotContains(response, "NO VITALS YET")

    def test_no_vitals_empty_state(self):
        patient = Patient.objects.create(
            patient_number="PAT-D0002", first_name="No", last_name="Vitals",
            birthdate=datetime.date(1995, 2, 2), sex="male",
            contact_number="09170001111", address="Purok 3",
        )
        case = AnimalBiteCase.objects.create(
            case_number="ABC-D0003", patient=patient, created_by=self.admin,
            animal_type="dog", exposure_type="bite",
            date_time_bite=datetime.datetime(2026, 10, 6, 9, 0),
        )
        self._login()
        response = self.client.get(reverse("nurse:doctor_finding_detail", args=[case.pk]))
        self.assertContains(response, "NO VITALS YET")
        self.assertContains(response, "No vital signs recorded for this patient.")

    # -------------------------------------------------- assessment
    def test_complete_assessment_is_shown(self):
        self._login()
        response = self.client.get(self.url)
        for value in (
            "Dog bite on the left forearm",
            "family dog while feeding it",
            "Category III",
            "Transdermal bites/scratches",
            "Complete the Essen regimen",
            "Amoxicillin",
            "21 capsules",
            "1 tablet every 8 hours for 7 days.",
            "Return on Day 3 and Day 7.",
            "No known drug allergies.",
            "Verorab",
            "0.5",
        ):
            self.assertContains(response, value)

    def test_no_assessment_empty_state_is_not_final(self):
        case = AnimalBiteCase.objects.create(
            case_number="ABC-D0004", patient=self.patient, created_by=self.admin,
            animal_type="dog", exposure_type="bite",
            date_time_bite=datetime.datetime(2026, 10, 7, 9, 0),
        )
        self._login()
        response = self.client.get(reverse("nurse:doctor_finding_detail", args=[case.pk]))
        self.assertContains(response, "NO ASSESSMENT YET")
        self.assertContains(response, "No doctor assessment has been recorded for this case.")
        self.assertContains(response, ">No Assessment<")
        self.assertNotContains(response, ">Final<")
        self.assertNotContains(response, ">Draft<")

    def test_draft_assessment_stays_draft(self):
        ClinicalAssessment.objects.filter(pk=self.assessment.pk).update(
            is_finalized=False, status="draft"
        )
        self._login()
        response = self.client.get(self.url)
        self.assertContains(response, ">Draft<")
        self.assertNotContains(response, ">Final<")

    def test_empty_assessment_fields_show_not_recorded(self):
        ClinicalAssessment.objects.filter(pk=self.assessment.pk).update(
            chief_complaint="", exposure_narrative="", category_confirmed="",
            treatment_plan="", dosage_instructions="", follow_up_instructions="",
            additional_notes="", vaccine_dose_ml="", vaccine_quantity=None,
        )
        self._login()
        response = self.client.get(self.url)
        self.assertContains(response, "Not recorded")

    # -------------------------------------------------- doses
    def test_dose_rows_use_stored_dates_and_statuses(self):
        doses = list(self.schedule.doses.order_by("id"))
        VaccineDose.objects.filter(pk=doses[0].pk).update(
            dose_status="completed", actual_date=datetime.date(2026, 10, 1)
        )
        VaccineDose.objects.filter(pk=doses[1].pk).update(
            dose_status="scheduled", scheduled_date=datetime.date(2026, 10, 4), actual_date=None
        )
        VaccineDose.objects.filter(pk=doses[2].pk).update(
            dose_status="pending", scheduled_date=None, actual_date=None
        )
        self._login()
        content = self.client.get(self.url).content.decode()
        self.assertIn("status-pill--completed", content)
        self.assertIn("status-pill--scheduled", content)
        self.assertIn("status-pill--pending", content)
        self.assertIn("Pending Scheduling", content)
        self.assertIn("Not yet set", content)

    def test_case_without_schedule_renders_without_dose_rows(self):
        case = AnimalBiteCase.objects.create(
            case_number="ABC-D0005", patient=self.patient, created_by=self.admin,
            animal_type="dog", exposure_type="bite",
            date_time_bite=datetime.datetime(2026, 10, 8, 9, 0),
        )
        self._assessment(case, chief_complaint="Assessed case without a schedule")
        self._login()
        response = self.client.get(reverse("nurse:doctor_finding_detail", args=[case.pk]))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        main = content[content.index("<main"):content.index("</main>")]
        self.assertIn("No dose records have been created for this case.", main)
        self.assertNotIn("status-pill", main)

    # -------------------------------------------------- strict read-only
    def test_page_has_no_editable_controls(self):
        self._login()
        content = self.client.get(self.url).content.decode()
        main = content[content.index("<main"):content.index("</main>")]
        for token in (
            "<form", "<select", "<textarea", "<input", "Manage Doses",
            "Manage Case", "Patient File", "Save", "Remove", "Add medicine",
        ):
            self.assertNotIn(token, main)
        self.assertIn("Print Sheet", main)

    def test_viewing_never_changes_stored_records(self):
        stock = VaccineInventory.objects.create(
            vaccine_name="Rabies Vaccine", brand="Verorab", batch_number="LOT-1",
            expiration_date=datetime.date(2027, 1, 1),
            quantity_received=10, quantity_available=10, quantity_used=0,
        )
        before_doses = list(
            self.schedule.doses.values_list("dose_status", "scheduled_date", "actual_date")
        )
        before_assessment = ClinicalAssessment.objects.filter(pk=self.assessment.pk).values().get()
        before_medicine = MedicineInventory.objects.filter(pk=self.medicine.pk).values().get()
        audit_before = AuditLog.objects.count()

        self._login()
        self.client.get(self.url)

        self.assertEqual(
            list(self.schedule.doses.values_list("dose_status", "scheduled_date", "actual_date")),
            before_doses,
        )
        self.assertEqual(
            ClinicalAssessment.objects.filter(pk=self.assessment.pk).values().get(),
            before_assessment,
        )
        self.assertEqual(
            MedicineInventory.objects.filter(pk=self.medicine.pk).values().get(),
            before_medicine,
        )
        stock.refresh_from_db()
        self.assertEqual(stock.quantity_available, 10)
        self.assertEqual(stock.quantity_used, 0)
        self.assertEqual(AuditLog.objects.count(), audit_before)


class DoctorFindingListTests(TestCase):

    def setUp(self):
        self.barangay = Barangay.objects.create(name="Dumingag")
        self.admin = U.objects.create_user("admin.list", password="pw12345678", role="admin")
        self.nurse = U.objects.create_user("nurse.list", password="pw12345678", role="nurse")
        self.patient = Patient.objects.create(
            patient_number="PAT-L0001", first_name="List", last_name="Patient",
            birthdate=datetime.date(1990, 1, 1), sex="female",
            contact_number="09170000000", address="Purok 1", barangay=self.barangay,
        )
        self.case = AnimalBiteCase.objects.create(
            case_number="ABC-L0001", patient=self.patient, created_by=self.admin,
            animal_type="dog", exposure_type="bite",
            date_time_bite=datetime.datetime(2026, 10, 1, 9, 0),
        )

    def test_nurse_view_button_opens_the_read_only_findings_page(self):
        self.client.force_login(self.nurse)
        response = self.client.get(reverse("bite_cases:case_list"))
        self.assertContains(
            response, reverse("nurse:doctor_finding_detail", args=[self.case.pk])
        )
        self.assertNotContains(
            response, reverse("bite_cases:case_edit", args=[self.case.pk])
        )

    def test_admin_view_and_edit_buttons_are_unchanged(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("bite_cases:case_list"))
        self.assertContains(
            response, reverse("bite_cases:case_detail", args=[self.case.pk])
        )
        self.assertContains(
            response, reverse("bite_cases:case_edit", args=[self.case.pk])
        )


class DoctorFindingListTableTests(TestCase):
    """Nurse-only table numbering and green View button on the findings list."""

    def setUp(self):
        self.admin = U.objects.create_user("admin.tbl", password="pw12345678", role="admin")
        self.nurse = U.objects.create_user("nurse.tbl", password="pw12345678", role="nurse")
        self.patient = Patient.objects.create(
            patient_number="PAT-TBL01", first_name="Table", last_name="Patient",
            birthdate=datetime.date(1992, 2, 2), sex="male",
            contact_number="09170000001", address="Purok 2",
        )

    def _make_case(self, number, animal="dog"):
        return AnimalBiteCase.objects.create(
            case_number=number, patient=self.patient, created_by=self.admin,
            animal_type=animal, exposure_type="bite",
            date_time_bite=datetime.datetime(2026, 10, 1, 9, 0),
        )

    def test_nurse_table_has_number_column_first_in_required_order(self):
        self._make_case("ABC-TBL01")
        self.client.force_login(self.nurse)
        response = self.client.get(reverse("bite_cases:case_list"))
        content = response.content.decode()

        headers = ["No.", "Case #", "Patient", "Date", "Animal", "Exposure", "Status", "Actions"]
        positions = [content.index(f">{label}<") for label in headers]
        self.assertEqual(positions, sorted(positions))

    def test_numbering_is_sequential_and_resets_with_filters(self):
        self._make_case("ABC-TBL01", animal="dog")
        self._make_case("ABC-TBL02", animal="cat")
        self._make_case("ABC-TBL03", animal="dog")
        self.client.force_login(self.nurse)

        response = self.client.get(reverse("bite_cases:case_list"))
        content = response.content.decode()
        self.assertIn('row-number">1</td>', content)
        self.assertIn('row-number">2</td>', content)
        self.assertIn('row-number">3</td>', content)

        response = self.client.get(reverse("bite_cases:case_list"), {"animal": "cat"})
        content = response.content.decode()
        self.assertIn('row-number">1</td>', content)
        self.assertNotIn('row-number">2</td>', content)

    def test_numbering_continues_across_pages(self):
        for index in range(25):
            self._make_case(f"ABC-P{index:04d}")
        self.client.force_login(self.nurse)

        page_one = self.client.get(reverse("bite_cases:case_list")).content.decode()
        page_two = self.client.get(
            reverse("bite_cases:case_list"), {"page": 2}
        ).content.decode()

        self.assertIn('row-number">1</td>', page_one)
        self.assertIn('row-number">20</td>', page_one)
        self.assertNotIn('row-number">21</td>', page_one)
        self.assertIn('row-number">21</td>', page_two)
        self.assertNotIn('row-number">1</td>', page_two)

    def test_nurse_view_button_is_dark_green_with_eye_icon(self):
        self._make_case("ABC-TBL01")
        self.client.force_login(self.nurse)
        response = self.client.get(reverse("bite_cases:case_list"))
        self.assertContains(response, "findings-view-btn")
        self.assertContains(response, "#0B4D3B")
        self.assertContains(response, "#0E6349")
        self.assertContains(response, 'aria-label="View doctor findings for ABC-TBL01')

    def test_nurse_list_is_view_only(self):
        self._make_case("ABC-TBL01")
        self.client.force_login(self.nurse)
        response = self.client.get(reverse("bite_cases:case_list"))
        self.assertNotContains(response, "+ New Case")
        self.assertNotContains(
            response, reverse("bite_cases:case_edit", args=[AnimalBiteCase.objects.get().pk])
        )

    def test_admin_table_is_unchanged(self):
        self._make_case("ABC-TBL01")
        self.client.force_login(self.admin)
        response = self.client.get(reverse("bite_cases:case_list"))
        self.assertNotContains(response, ">No.<")
        self.assertContains(response, "+ New Case")
        self.assertNotContains(response, "findings-view-btn")
