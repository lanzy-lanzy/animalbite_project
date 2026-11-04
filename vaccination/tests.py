"""
Tests for the nurse-triggered vaccination schedule workflow.

Schedules are deliberately NOT created automatically when a bite case is
created or converted. A nurse (or admin) triggers them explicitly once the
case has been assessed.
"""
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from bite_cases.models import AnimalBiteCase
from doctor.models import ClinicalAssessment, Prescription
from inventory.models import MedicineInventory, VaccineInventory
from patients.models import Barangay, Patient, VitalSign
from pre_registrations.models import PreRegistration
from settings_app.models import SystemSetting

from .forms import VaccineDoseForm
from .models import SMSLog, VaccinationSchedule, VaccineDose
from .schedule_utils import ensure_vaccination_schedule, get_dose_days, schedule_base_date

U = get_user_model()


class NurseTriggeredScheduleTests(TestCase):

    def setUp(self):
        self.patient = Patient.objects.create(
            patient_number="PAT-T0001", first_name="Test", last_name="Patient",
            birthdate=datetime.date(1995, 5, 5), contact_number="09170000000",
        )
        self.nurse = U.objects.create_user("n.t", password="pw12345678", role="nurse")
        self.doctor = U.objects.create_user("d.t", password="pw12345678", role="doctor")
        self.admin = U.objects.create_user("a.t", password="pw12345678", role="admin")

    def _case(self, number="ABC-T0001"):
        return AnimalBiteCase.objects.create(
            case_number=number, patient=self.patient, created_by=self.admin,
            animal_type="dog", exposure_type="bite",
            date_time_bite=datetime.datetime(2026, 10, 1, 9, 0),
        )

    # -------------------------------------------------- no auto-creation
    def test_creating_a_case_does_not_create_a_schedule(self):
        case = self._case()
        self.assertFalse(VaccinationSchedule.objects.filter(bite_case=case).exists())

    def test_case_create_view_does_not_create_a_schedule(self):
        """The admin case_create view must not materialise a schedule."""
        self.client.force_login(self.admin)
        before = VaccinationSchedule.objects.count()
        response = self.client.get(reverse("bite_cases:case_create"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(VaccinationSchedule.objects.count(), before)

    # -------------------------------------------------- RBAC
    def test_doctor_cannot_set_schedule(self):
        case = self._case()
        self.client.force_login(self.doctor)
        self.client.get(reverse("vaccination:schedule_trigger", args=[case.pk]))
        self.client.get(reverse("vaccination:schedule_create", args=[case.pk]))
        self.assertFalse(VaccinationSchedule.objects.filter(bite_case=case).exists())

    def test_admin_may_set_schedule(self):
        case = self._case()
        self.client.force_login(self.admin)
        response = self.client.get(reverse("vaccination:schedule_trigger", args=[case.pk]))
        self.assertEqual(response.status_code, 200)

    def test_nurse_can_open_trigger_page(self):
        case = self._case()
        self.client.force_login(self.nurse)
        response = self.client.get(reverse("vaccination:schedule_trigger", args=[case.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Doses that will be created")

    # -------------------------------------------------- nurse triggers
    def test_nurse_trigger_creates_schedule_and_doses(self):
        case = self._case()
        self.client.force_login(self.nurse)
        self.client.post(reverse("vaccination:schedule_trigger", args=[case.pk]), follow=True)

        sched = VaccinationSchedule.objects.get(bite_case=case)
        self.assertEqual(sched.created_by, self.nurse)
        doses = list(VaccineDose.objects.filter(schedule=sched).order_by("id"))
        self.assertEqual(len(doses), 5)
        # Only the 1st dose is dated; the rest wait for the nurse to assign them.
        self.assertEqual(doses[0].scheduled_date, timezone.localdate())
        self.assertEqual(doses[0].dose_status, "scheduled")
        for dose in doses[1:]:
            self.assertIsNone(dose.scheduled_date)
            self.assertEqual(dose.dose_status, "pending")
        self.assertTrue(
            AuditLog.objects.filter(
                action="CREATE_SCHEDULE", description__contains=case.case_number
            ).exists()
        )

    def test_triggering_twice_does_not_duplicate(self):
        case = self._case()
        self.client.force_login(self.nurse)
        url = reverse("vaccination:schedule_trigger", args=[case.pk])
        self.client.post(url, follow=True)
        self.client.post(url, follow=True)
        self.assertEqual(VaccinationSchedule.objects.filter(bite_case=case).count(), 1)
        self.assertEqual(VaccineDose.objects.filter(schedule__bite_case=case).count(), 5)

    # -------------------------------------------------- nurse dashboard queue
    def test_nurse_dashboard_lists_awaiting_cases(self):
        case = self._case()
        self.client.force_login(self.nurse)
        response = self.client.get("/nurse/")
        self.assertContains(response, "Cases Awaiting Schedule")
        self.assertContains(response, case.case_number)
        self.assertEqual(response.context["awaiting_schedule_count"], 1)

        self.client.post(reverse("vaccination:schedule_trigger", args=[case.pk]), follow=True)
        response = self.client.get("/nurse/")
        self.assertEqual(response.context["awaiting_schedule_count"], 0)
        self.assertContains(response, "Every case has a schedule")

    # -------------------------------------------------- helpers
    def test_dose_days_defaults_and_setting_override(self):
        SystemSetting.objects.all().delete()
        self.assertEqual(get_dose_days(), [0, 3, 7, 14, 28])

        s = SystemSetting.objects.create(key="vaccination_days", value="0,7,21")
        self.assertEqual(get_dose_days(), [0, 7, 21])

        # a malformed setting falls back rather than exploding
        s.value = "not,numbers"
        s.save()
        self.assertEqual(get_dose_days(), [0, 3, 7, 14, 28])

    def test_base_date_prefers_consultation_then_today(self):
        case = self._case()
        self.assertEqual(schedule_base_date(case), timezone.localdate())

        AnimalBiteCase.objects.filter(pk=case.pk).update(
            date_time_consultation=timezone.make_aware(datetime.datetime(2026, 9, 30, 10, 0))
        )
        case.refresh_from_db()
        self.assertEqual(schedule_base_date(case), datetime.date(2026, 9, 30))

    def test_ensure_schedule_is_idempotent_and_honours_settings(self):
        SystemSetting.objects.all().delete()
        SystemSetting.objects.create(key="vaccination_days", value="0,7,21")
        case = self._case()
        case.date_time_consultation = datetime.datetime(2026, 9, 30, 10, 0)
        case.save()

        sched, created = ensure_vaccination_schedule(case, created_by=self.nurse)
        self.assertTrue(created)
        doses = list(VaccineDose.objects.filter(schedule=sched).order_by("id"))
        self.assertEqual(len(doses), 3)
        # Only the 1st dose carries the base date; later doses stay undated.
        self.assertEqual(
            [d.scheduled_date for d in doses],
            [datetime.date(2026, 9, 30), None, None],
        )
        self.assertEqual([d.dose_status for d in doses], ["scheduled", "pending", "pending"])
        self.assertEqual([d.protocol_day for d in doses], [0, 7, 21])
        self.assertEqual([d.dose_label for d in doses], ["Day 0", "Day 7", "Day 21"])
        self.assertEqual(doses[1].expected_date(), datetime.date(2026, 10, 7))

        sched2, created2 = ensure_vaccination_schedule(case, created_by=self.nurse)
        self.assertFalse(created2)
        self.assertEqual(sched2.pk, sched.pk)
        self.assertEqual(VaccineDose.objects.filter(schedule=sched).count(), 3)


class NurseSchedulingTests(TestCase):
    """The nurse dates the doctor-created pending doses within protocol."""

    def setUp(self):
        self.patient = Patient.objects.create(
            patient_number="PAT-N0001", first_name="Nurse", last_name="Patient",
            birthdate=datetime.date(1995, 5, 5), contact_number="09170000001",
        )
        self.nurse = U.objects.create_user("n.s", password="pw12345678", role="nurse")
        self.case = AnimalBiteCase.objects.create(
            case_number="ABC-N0001", patient=self.patient, created_by=self.nurse,
            animal_type="dog", exposure_type="bite",
            date_time_bite=datetime.datetime(2026, 10, 1, 9, 0),
        )
        # date_time_consultation is auto_now_add; pin it so the protocol is fixed.
        AnimalBiteCase.objects.filter(pk=self.case.pk).update(
            date_time_consultation=timezone.make_aware(datetime.datetime(2026, 10, 1, 9, 0))
        )
        self.case.refresh_from_db()
        self.schedule, _ = ensure_vaccination_schedule(self.case, created_by=self.nurse)
        self.first, self.second, self.third = list(self.schedule.doses.order_by("id"))[:3]
        self.stock = VaccineInventory.objects.create(
            vaccine_name="Rabies Vaccine", brand="Verorab", batch_number="LOT-1",
            expiration_date=datetime.date(2027, 1, 1),
            quantity_received=10, quantity_available=10, quantity_used=0,
        )

    def _payload(self, dose, **overrides):
        data = {
            "dose_label": dose.dose_label,
            "dose_status": "scheduled",
            "scheduled_date": "",
            "actual_date": "",
            "vaccine_brand": "",
            "batch_number": "",
            "expiration_date": "",
            "remarks": "",
        }
        data.update(overrides)
        return data

    def _administer(self, **overrides):
        payload = self._payload(
            self.second,
            dose_status="completed",
            scheduled_date="2026-10-04",
            actual_date="2026-10-04",
            vaccine_brand="Verorab",
            batch_number="LOT-1",
        )
        payload.update(overrides)
        return self.client.post(
            reverse("vaccination:dose_update", args=[self.second.pk]), payload
        )

    # -------------------------------------------------- pending prefill
    def test_pending_dose_form_offers_protocol_date(self):
        form = VaccineDoseForm(instance=self.second)
        self.assertEqual(form.initial["scheduled_date"], datetime.date(2026, 10, 4))
        self.assertEqual(form.initial["dose_status"], "scheduled")
        # The doctor's pending dose itself is untouched until the nurse saves.
        self.second.refresh_from_db()
        self.assertEqual(self.second.dose_status, "pending")
        self.assertIsNone(self.second.scheduled_date)

    # -------------------------------------------------- assign a date
    def test_nurse_assigning_protocol_date_schedules_dose(self):
        self.client.force_login(self.nurse)
        response = self.client.post(
            reverse("vaccination:dose_update", args=[self.second.pk]),
            self._payload(self.second, scheduled_date="2026-10-04"),
        )
        self.assertEqual(response.status_code, 302)
        self.second.refresh_from_db()
        self.assertEqual(self.second.scheduled_date, datetime.date(2026, 10, 4))
        self.assertEqual(self.second.dose_status, "scheduled")
        # Scheduling never touches inventory.
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity_available, 10)

    def test_confirming_a_date_promotes_a_pending_submission(self):
        self.client.force_login(self.nurse)
        self.client.post(
            reverse("vaccination:dose_update", args=[self.second.pk]),
            self._payload(self.second, scheduled_date="2026-10-04", dose_status="pending"),
        )
        self.second.refresh_from_db()
        self.assertEqual(self.second.dose_status, "scheduled")

    def test_later_date_is_allowed(self):
        self.client.force_login(self.nurse)
        self.client.post(
            reverse("vaccination:dose_update", args=[self.second.pk]),
            self._payload(self.second, scheduled_date="2026-10-15"),
        )
        self.second.refresh_from_db()
        self.assertEqual(self.second.scheduled_date, datetime.date(2026, 10, 15))
        self.assertEqual(self.second.dose_status, "scheduled")

    # -------------------------------------------------- protocol conflicts
    def test_date_before_protocol_interval_is_rejected(self):
        self.client.force_login(self.nurse)
        response = self.client.post(
            reverse("vaccination:dose_update", args=[self.second.pk]),
            self._payload(self.second, scheduled_date="2026-10-03"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("scheduled_date", response.context["form"].errors)
        self.second.refresh_from_db()
        self.assertIsNone(self.second.scheduled_date)
        self.assertEqual(self.second.dose_status, "pending")

    def test_scheduling_without_a_date_is_rejected(self):
        self.client.force_login(self.nurse)
        response = self.client.post(
            reverse("vaccination:dose_update", args=[self.second.pk]),
            self._payload(self.second),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("scheduled_date", response.context["form"].errors)
        self.second.refresh_from_db()
        self.assertEqual(self.second.dose_status, "pending")

    # -------------------------------------------------- completion + stock
    def test_completion_requires_actual_administration_date(self):
        self.client.force_login(self.nurse)
        response = self._administer(actual_date="")
        self.assertEqual(response.status_code, 200)
        self.assertIn("actual_date", response.context["form"].errors)
        self.second.refresh_from_db()
        self.assertNotEqual(self.second.dose_status, "completed")

    def test_stock_is_deducted_once_on_completion(self):
        self.client.force_login(self.nurse)
        self.assertEqual(self._administer().status_code, 302)

        self.second.refresh_from_db()
        self.assertEqual(self.second.dose_status, "completed")
        self.assertEqual(self.second.actual_date, datetime.date(2026, 10, 4))
        self.assertEqual(self.second.administered_by, self.nurse)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity_available, 9)
        self.assertEqual(self.stock.quantity_used, 1)

        # Re-saving the completed dose must not deduct again.
        self._administer()
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity_available, 9)
        self.assertEqual(self.stock.quantity_used, 1)

    # -------------------------------------------------- access + presentation
    def test_patient_cannot_update_a_dose(self):
        user = U.objects.create_user("pat.n", password="pw12345678", role="patient")
        self.patient.account = user
        self.patient.save(update_fields=["account"])
        self.client.force_login(user)
        response = self.client.post(
            reverse("vaccination:dose_update", args=[self.second.pk]),
            self._payload(self.second, scheduled_date="2026-10-04", dose_status="completed"),
            follow=True,
        )
        self.assertNotContains(response, "Dose Day 3 updated")
        self.second.refresh_from_db()
        self.assertEqual(self.second.dose_status, "pending")
        self.assertEqual(response.redirect_chain[-1][0], reverse("accounts:patient_portal"))

    def test_sms_is_blocked_until_a_date_is_assigned(self):
        self.client.force_login(self.nurse)
        response = self.client.get(
            reverse("vaccination:send_sms_for_dose", args=[self.second.pk]), follow=True
        )
        self.assertEqual(response.redirect_chain[-1][0],
                         reverse("vaccination:schedule_detail", args=[self.schedule.pk]))
        self.assertFalse(SMSLog.objects.filter(dose=self.second).exists())

    def test_schedule_detail_marks_pending_doses(self):
        self.client.force_login(self.nurse)
        response = self.client.get(
            reverse("vaccination:schedule_detail", args=[self.schedule.pk])
        )
        self.assertContains(response, "Pending Scheduling")

    def test_nurse_dashboard_counts_pending_doses(self):
        self.client.force_login(self.nurse)
        response = self.client.get("/nurse/")
        self.assertEqual(response.context["pending_scheduling_count"], 4)


class VaccinationRecordViewTests(TestCase):
    """The read-only record sheet shown from Vaccination Records."""

    def setUp(self):
        self.patient = Patient.objects.create(
            patient_number="PAT-R0001", first_name="Record", last_name="Patient",
            birthdate=datetime.date(1990, 4, 4), sex="female", civil_status="married",
            contact_number="09171234567", address="Purok 2, Dumingag",
            parent_guardian="Maria Patient", emergency_contact_name="Jose Patient",
            emergency_contact_number="09179876543",
        )
        self.nurse = U.objects.create_user("n.r", password="pw12345678", role="nurse")
        self.doctor = U.objects.create_user("d.r", password="pw12345678", role="doctor", first_name="Dana")
        self.admin = U.objects.create_user("a.r", password="pw12345678", role="admin")
        self.case = AnimalBiteCase.objects.create(
            case_number="ABC-R0001", patient=self.patient, created_by=self.admin,
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
        self.url = reverse("vaccination:record_detail", args=[self.schedule.pk])

    def _assessment(self, **overrides):
        data = {
            "patient": self.patient,
            "bite_case": self.case,
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

    def _medicine(self):
        return MedicineInventory.objects.create(
            medicine_name="Amoxicillin", strength="500 mg", dosage_form="capsule",
            unit="capsule", batch_number="MED-1", expiration_date=datetime.date(2027, 1, 1),
            quantity_received=100, quantity_available=100,
        )

    # -------------------------------------------------- access
    def test_staff_roles_can_open_the_record(self):
        for user in (self.nurse, self.doctor, self.admin):
            self.client.force_login(user)
            response = self.client.get(self.url)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Vaccination Record")

    def test_patient_is_redirected_to_their_portal(self):
        user = U.objects.create_user("pat.r", password="pw12345678", role="patient")
        self.patient.account = user
        self.patient.save(update_fields=["account"])
        self.client.force_login(user)
        response = self.client.get(self.url, follow=True)
        self.assertEqual(response.redirect_chain[-1][0], reverse("accounts:patient_portal"))
        self.assertNotContains(response, "record-hero__initials")

    def test_patient_cannot_open_another_patients_record(self):
        other = Patient.objects.create(
            patient_number="PAT-R0002", first_name="Other", last_name="Patient",
            birthdate=datetime.date(1990, 1, 1), contact_number="09170002222", address="Elsewhere",
        )
        user = U.objects.create_user("pat.x", password="pw12345678", role="patient")
        other.account = user
        other.save(update_fields=["account"])
        self.client.force_login(user)
        response = self.client.get(self.url, follow=True)
        self.assertEqual(response.redirect_chain[-1][0], reverse("accounts:patient_portal"))
        self.assertNotContains(response, self.patient.patient_number)

    # -------------------------------------------------- structure
    def test_sections_render_in_required_vertical_order(self):
        self._assessment()
        self.client.force_login(self.nurse)
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
            "Vaccine Brand",
            "Treatment, Prescription &amp; Follow-up",
        ]
        positions = [content.index(marker) for marker in markers]
        self.assertEqual(positions, sorted(positions))

    def test_patient_header_uses_initials_and_full_name(self):
        self.client.force_login(self.nurse)
        response = self.client.get(self.url)
        self.assertContains(response, "record-hero__initials")
        self.assertContains(response, ">RP</span>")
        self.assertContains(response, self.patient.full_name())

    def test_print_sheet_button_and_print_styles(self):
        self.client.force_login(self.nurse)
        response = self.client.get(self.url)
        self.assertContains(response, "Print Sheet")
        self.assertContains(response, "window.print()")
        self.assertContains(response, "@page { size: A4 portrait")

    # -------------------------------------------------- empty states
    def test_no_assessment_and_no_vitals_empty_states(self):
        self.client.force_login(self.nurse)
        response = self.client.get(self.url)
        self.assertContains(response, "NO ASSESSMENT YET")
        self.assertContains(response, "No doctor assessment has been recorded for this case.")
        self.assertContains(response, "NO VITALS YET")
        self.assertContains(response, "No vital signs recorded for this patient.")
        self.assertNotContains(response, "Dose Schedule Records")

    # -------------------------------------------------- assessment content
    def test_assessment_details_and_dose_rows(self):
        self._assessment()
        medicine = self._medicine()
        Prescription.objects.create(
            assessment=ClinicalAssessment.objects.get(bite_case=self.case),
            patient=self.patient, bite_case=self.case,
            medicine=medicine, quantity=21,
        )
        first, second = list(self.schedule.doses.order_by("id"))[:2]
        VaccineDose.objects.filter(pk=first.pk).update(
            dose_status="completed", actual_date=datetime.date(2026, 10, 1)
        )
        self.client.force_login(self.nurse)
        response = self.client.get(self.url)

        self.assertContains(response, "Dog bite on the left forearm")
        self.assertContains(response, "family dog while feeding it")
        self.assertContains(response, "Category III")
        self.assertContains(response, "Transdermal bites/scratches")
        self.assertContains(response, "5 Doses")
        self.assertContains(response, "Verorab")
        self.assertContains(response, "0.5")
        self.assertContains(response, "Complete the Essen regimen")
        self.assertContains(response, "Amoxicillin")
        self.assertContains(response, "21 capsules")
        self.assertContains(response, "1 tablet every 8 hours for 7 days.")
        self.assertContains(response, "Return on Day 3 and Day 7.")
        self.assertContains(response, "No known drug allergies.")
        # First dose was administered; the second is still undated.
        self.assertContains(response, "1st Dose")
        self.assertContains(response, "Completed")
        self.assertContains(response, "Not yet set")
        self.assertContains(response, "Pending Scheduling")

    def test_completed_requires_an_actual_administration_date(self):
        self._assessment()
        first = self.schedule.doses.order_by("id").first()
        VaccineDose.objects.filter(pk=first.pk).update(dose_status="completed", actual_date=None)
        self.client.force_login(self.nurse)
        content = self.client.get(self.url).content.decode()
        row = content[content.index("1st Dose"):]
        self.assertNotIn("status-pill--completed", row[:400])

    def test_vitals_render_when_recorded(self):
        VitalSign.objects.create(
            patient=self.patient, taken_at=timezone.now(),
            bp_systolic=120, bp_diastolic=80, temperature=36.7,
            pulse_rate=72, spo2=98, weight=55, height=160, bmi=21.48,
            pain_score=3, taken_by=self.nurse,
        )
        self.client.force_login(self.nurse)
        response = self.client.get(self.url)
        self.assertContains(response, "120/80 mmHg")
        self.assertContains(response, "36.7")
        self.assertContains(response, "72 bpm")
        self.assertContains(response, "98 %")
        self.assertContains(response, "55")
        self.assertContains(response, "160")
        self.assertContains(response, "21.48")
        self.assertContains(response, "3 / 10")
        self.assertContains(response, "n.r")
        self.assertNotContains(response, "NO VITALS YET")

    def test_case_selection_does_not_mix_records(self):
        self._assessment(chief_complaint="Assessment for the first case")
        second_case = AnimalBiteCase.objects.create(
            case_number="ABC-R0002", patient=self.patient, created_by=self.admin,
            animal_type="cat", exposure_type="scratch",
            date_time_bite=datetime.datetime(2026, 10, 5, 9, 0),
        )
        self.client.force_login(self.nurse)
        response = self.client.get(self.url, {"case": second_case.pk})
        self.assertContains(response, second_case.case_number)
        self.assertContains(response, "NO ASSESSMENT YET")
        self.assertNotContains(response, "Assessment for the first case")

    def test_viewing_never_changes_inventory_stock(self):
        stock = VaccineInventory.objects.create(
            vaccine_name="Rabies Vaccine", brand="Verorab", batch_number="LOT-1",
            expiration_date=datetime.date(2027, 1, 1),
            quantity_received=10, quantity_available=10, quantity_used=0,
        )
        self.client.force_login(self.nurse)
        self.client.get(self.url)
        stock.refresh_from_db()
        self.assertEqual(stock.quantity_available, 10)
        self.assertEqual(stock.quantity_used, 0)


class VaccinationRecordsListTests(TestCase):
    """The simplified patient listing at /vaccination/schedules/patients/."""

    def setUp(self):
        self.barangay = Barangay.objects.create(name="Caridad")
        self.other_barangay = Barangay.objects.create(name="San Pablo")
        self.nurse = U.objects.create_user("n.list", password="pw12345678", role="nurse")
        self.admin = U.objects.create_user("a.list", password="pw12345678", role="admin")
        self.patient = Patient.objects.create(
            patient_number="PAT-L0001", first_name="Eragene", middle_name="A",
            last_name="Patihan", birthdate=datetime.date(2005, 5, 5), sex="female",
            contact_number="09392339688", address="Purok 7, Caridad",
            barangay=self.barangay,
        )
        self.case = AnimalBiteCase.objects.create(
            case_number="ABC-L0001", patient=self.patient, created_by=self.admin,
            animal_type="dog", exposure_type="bite",
            date_time_bite=datetime.datetime(2026, 10, 1, 9, 0),
        )
        self.schedule = VaccinationSchedule.objects.create(
            bite_case=self.case, created_by=self.nurse,
        )
        self.url = reverse("vaccination:patient_schedule_list")

    def _add_patient_with_schedule(self, number, first, last, barangay=None, sex="male"):
        patient = Patient.objects.create(
            patient_number=number, first_name=first, last_name=last,
            birthdate=datetime.date(1998, 3, 3), sex=sex,
            contact_number="09170000001", address="Dumingag",
            barangay=barangay or self.barangay,
        )
        case = AnimalBiteCase.objects.create(
            case_number=f"ABC-{number}", patient=patient, created_by=self.admin,
            animal_type="dog", exposure_type="bite",
            date_time_bite=datetime.datetime(2026, 10, 2, 9, 0),
        )
        VaccinationSchedule.objects.create(bite_case=case, created_by=self.nurse)
        return patient

    def _approve(self, patient, when):
        return PreRegistration.objects.create(
            first_name=patient.first_name, last_name=patient.last_name,
            birthdate=patient.birthdate, sex=patient.sex,
            contact_number=patient.contact_number, address=patient.address,
            barangay=patient.barangay.name if patient.barangay else "Caridad",
            medical_history="None", allergy_info="None", previous_rabies_vax="None",
            bite_datetime=datetime.datetime(2026, 10, 1, 9, 0),
            incident_place="Purok 7", incident_barangay="Caridad",
            animal_type="dog", animal_ownership="owned",
            animal_vaccination_status="vaccinated", animal_condition="alive",
            exposure_type="bite", body_part_affected="left forearm",
            status=PreRegistration.Status.CONVERTED,
            verified_by=self.admin, verified_at=when, converted_patient=patient,
            converted_case=patient.bite_cases.first(),
        )

    # -------------------------------------------------- structure
    def test_title_and_three_element_filter_row(self):
        self.client.force_login(self.nurse)
        response = self.client.get(self.url)

        self.assertContains(response, "<h1", html=False)
        self.assertContains(response, "Vaccination Records")
        self.assertContains(response, "Search by name, ID, contact, barangay, or address...")
        self.assertContains(response, "All Barangays")
        self.assertContains(response, ">Search</button>")
        self.assertNotContains(response, "Dose Status")
        self.assertNotContains(response, "Due filter")
        self.assertNotContains(response, "From (created)")
        self.assertNotContains(response, "To (created)")
        self.assertNotContains(response, ">Apply</button>")
        self.assertNotContains(response, ">Reset</button>")

    def test_table_has_the_five_required_columns(self):
        self.client.force_login(self.nurse)
        content = self.client.get(self.url).content.decode()

        self.assertEqual(content.count('class="table-header'), 5)
        for heading in ("#", "Patient", "Age/Sex", "Approved Date", "Actions"):
            self.assertIn(heading, content)
        self.assertLess(content.index(">#<"), content.index(">Patient<"))
        self.assertNotIn("Next Due", content)
        self.assertNotIn("Vaccination Status", content)
        self.assertNotIn("Progress", content)
        self.assertNotIn("Case #", content)

    def test_rows_are_numbered_and_continue_across_pages(self):
        for index in range(2, 17):
            self._add_patient_with_schedule(
                f"PAT-L{index:04d}", f"Patient{index}", "List",
            )
        self.client.force_login(self.nurse)

        page_one = self.client.get(
            self.url, {"q": "List", "barangay": self.barangay.pk}
        ).content.decode()
        page_two = self.client.get(
            self.url, {"q": "List", "barangay": self.barangay.pk, "page": 2}
        ).content.decode()

        self.assertIn(">1</td>", page_one)
        self.assertIn(">12</td>", page_one)
        self.assertNotIn(">13</td>", page_one)
        self.assertIn(">13</td>", page_two)

    def test_numbering_restarts_with_a_filtered_list(self):
        self._add_patient_with_schedule(
            "PAT-L0002", "Other", "Person", barangay=self.other_barangay,
        )
        self.client.force_login(self.nurse)
        content = self.client.get(
            self.url, {"barangay": self.other_barangay.pk}
        ).content.decode()
        self.assertIn(">1</td>", content)
        self.assertNotIn(">2</td>", content)

    def test_patient_identity_age_sex_and_barangay(self):
        self.client.force_login(self.nurse)
        response = self.client.get(self.url)

        self.assertContains(response, self.patient.full_name())
        self.assertContains(response, self.patient.patient_number)
        self.assertContains(response, self.patient.contact_display)
        self.assertContains(response, "Caridad")
        self.assertContains(response, f"{self.patient.age} / Female")
        self.assertRegex(response.content.decode(), r">\s*E\s*<")

    def test_only_view_and_manage_actions_are_rendered(self):
        self.client.force_login(self.nurse)
        response = self.client.get(self.url)

        self.assertContains(response, reverse("vaccination:record_detail", args=[self.schedule.pk]))
        self.assertContains(response, reverse("vaccination:schedule_detail", args=[self.schedule.pk]))
        self.assertNotContains(response, reverse("patients:patient_detail", args=[self.patient.pk]))
        self.assertNotContains(response, reverse("bite_cases:case_detail", args=[self.case.pk]))
        self.assertNotContains(response, ">Patient</a>")
        self.assertNotContains(response, ">Case →</a>")

    def test_nurse_actions_render_view_and_administer(self):
        self.client.force_login(self.nurse)
        content = self.client.get(self.url).content.decode()

        self.assertIn(reverse("vaccination:assessment_detail", args=[self.schedule.pk]), content)
        self.assertIn(reverse("vaccination:nurse_schedule_manage", args=[self.schedule.pk]), content)
        self.assertRegex(content, r">\s*Administer\s*<")
        self.assertIn("vr-act vr-view", content)
        self.assertIn("vr-act vr-add", content)
        self.assertIn("#16856A", content)
        self.assertIn("#106B55", content)
        self.assertIn("#08291F", content)
        self.assertIn("m18 2 4 4", content)
        self.assertLess(content.index("View"), content.index("Administer"))

    def test_admin_actions_keep_manage_and_never_show_administer(self):
        self.client.force_login(self.admin)
        content = self.client.get(self.url).content.decode()

        self.assertIn(reverse("vaccination:assessment_detail", args=[self.schedule.pk]), content)
        self.assertIn(reverse("vaccination:schedule_detail", args=[self.schedule.pk]), content)
        self.assertRegex(content, r">\s*Manage\s*<")
        self.assertNotIn("Administer", content)
        self.assertNotIn("vr-add", content)
        self.assertNotIn("vr-act", content)
        self.assertNotIn("#16856A", content)

    # -------------------------------------------------- data
    def test_patient_appears_once_even_with_multiple_bite_cases(self):
        second_case = AnimalBiteCase.objects.create(
            case_number="ABC-L0002", patient=self.patient, created_by=self.admin,
            animal_type="cat", exposure_type="scratch",
            date_time_bite=datetime.datetime(2026, 10, 9, 9, 0),
        )
        latest_schedule = VaccinationSchedule.objects.create(
            bite_case=second_case, created_by=self.nurse,
        )
        self.client.force_login(self.nurse)
        response = self.client.get(self.url)

        self.assertEqual(response.context["total"], 1)
        self.assertEqual(len(response.context["patients"].object_list), 1)
        row = response.context["patients"].object_list[0]
        self.assertEqual(row.pk, self.patient.pk)
        self.assertEqual(row.schedule_pk, latest_schedule.pk)

    def test_approved_date_uses_the_saved_verification_timestamp(self):
        approved_at = timezone.make_aware(datetime.datetime(2026, 10, 13, 9, 30))
        self._approve(self.patient, approved_at)
        self.client.force_login(self.nurse)

        response = self.client.get(self.url)
        self.assertContains(response, "Oct 13, 2026")

    def test_missing_approved_date_shows_not_recorded(self):
        self.client.force_login(self.nurse)
        response = self.client.get(self.url)
        self.assertContains(response, "Not recorded")

    # -------------------------------------------------- filters
    def test_search_covers_name_id_contact_barangay_and_address(self):
        self._add_patient_with_schedule(
            "PAT-L0002", "Other", "Person", barangay=self.other_barangay,
        )
        self.client.force_login(self.nurse)

        for query in ("Eragene", "PAT-L0001", "0939 233 9688", "Caridad", "Purok 7"):
            with self.subTest(query=query):
                response = self.client.get(self.url, {"q": query})
                self.assertEqual(response.context["total"], 1)
                self.assertEqual(
                    response.context["patients"].object_list[0].pk, self.patient.pk
                )

    def test_barangay_filter_limits_the_listing(self):
        other = self._add_patient_with_schedule(
            "PAT-L0002", "Other", "Person", barangay=self.other_barangay,
        )
        self.client.force_login(self.nurse)

        response = self.client.get(self.url, {"barangay": self.other_barangay.pk})
        self.assertEqual(response.context["total"], 1)
        self.assertEqual(response.context["patients"].object_list[0].pk, other.pk)

    def test_pagination_keeps_filters_and_does_not_duplicate_patients(self):
        for index in range(2, 17):
            self._add_patient_with_schedule(
                f"PAT-L{index:04d}", f"Patient{index}", "List",
            )
        self.client.force_login(self.nurse)
        response = self.client.get(
            self.url, {"q": "List", "barangay": self.barangay.pk}
        )

        self.assertEqual(response.context["total"], 15)
        first_page = response.context["patients"].object_list
        self.assertEqual(len(first_page), 12)
        self.assertEqual(len({p.pk for p in first_page}), 12)

        response = self.client.get(
            self.url, {"q": "List", "barangay": self.barangay.pk, "page": 2}
        )
        second_page = response.context["patients"].object_list
        self.assertEqual(len(second_page), 3)
        self.assertFalse({p.pk for p in first_page} & {p.pk for p in second_page})
        self.assertContains(response, "q=List&barangay=" + str(self.barangay.pk) + "&page=")

    # -------------------------------------------------- safety / access
    def test_opening_the_page_does_not_create_schedules_or_touch_stock(self):
        stock = VaccineInventory.objects.create(
            vaccine_name="Rabies Vaccine", brand="Verorab", batch_number="LOT-L1",
            expiration_date=datetime.date(2027, 1, 1),
            quantity_received=10, quantity_available=10, quantity_used=0,
        )
        schedule_count = VaccinationSchedule.objects.count()
        self.client.force_login(self.nurse)
        self.client.get(self.url)

        self.assertEqual(VaccinationSchedule.objects.count(), schedule_count)
        stock.refresh_from_db()
        self.assertEqual(stock.quantity_available, 10)
        self.assertEqual(stock.quantity_used, 0)

    def test_staff_roles_can_open_the_list(self):
        for user in (self.nurse, self.admin):
            self.client.force_login(user)
            with self.subTest(role=user.role):
                self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_patient_role_is_redirected_to_their_portal(self):
        user = U.objects.create_user("pat.list", password="pw12345678", role="patient")
        self.patient.account = user
        self.patient.save(update_fields=["account"])
        self.client.force_login(user)

        response = self.client.get(self.url, follow=True)
        self.assertEqual(response.redirect_chain[-1][0], reverse("accounts:patient_portal"))
        self.assertNotContains(response, self.patient.patient_number)


class ScheduleOwnershipEnforcementTests(TestCase):
    """The vaccination schedule detail page must be scoped to the owning patient:
    a patient can open only their own schedule, never another patient's."""

    def _make_patient_with_login(self, number, first, user_name):
        user = U.objects.create_user(user_name, password="pw12345678", role="patient")
        patient = Patient.objects.create(
            patient_number=number, first_name=first, last_name="Doe",
            birthdate=datetime.date(1990, 1, 1), contact_number="09170001111",
            account=user,
        )
        case = AnimalBiteCase.objects.create(
            case_number=f"ABC-{number}", patient=patient, created_by=user,
            animal_type="dog", exposure_type="bite",
            date_time_bite=datetime.datetime(2026, 10, 1, 9, 0),
        )
        sched = VaccinationSchedule.objects.create(bite_case=case)
        VaccineDose.objects.create(
            schedule=sched, dose_label="Day 0",
            scheduled_date=datetime.date(2026, 10, 1),
        )
        return user, patient, sched

    def setUp(self):
        self.alice, self.alice_patient, self.alice_sched = self._make_patient_with_login(
            "PAT-A0001", "Alice", "alice"
        )
        self.bob, self.bob_patient, self.bob_sched = self._make_patient_with_login(
            "PAT-B0002", "Bob", "bob"
        )
        self.admin = U.objects.create_user("adm", password="pw12345678", role="admin")

    # ---- patient portal read-only view (the page patients actually reach) ----
    def test_patient_can_view_own_schedule(self):
        self.client.force_login(self.alice)
        response = self.client.get(
            reverse("vaccination:my_schedule_detail", args=[self.alice_sched.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.alice_sched.bite_case.case_number)

    def test_patient_cannot_view_another_patient_schedule(self):
        """Bob must get a 404 (not a soft render) when requesting Alice's schedule."""
        self.client.force_login(self.bob)
        response = self.client.get(
            reverse("vaccination:my_schedule_detail", args=[self.alice_sched.pk])
        )
        self.assertEqual(response.status_code, 404)
        # Alice's case identifier must never appear in the response body.
        self.assertNotIn(self.alice_sched.bite_case.case_number.encode(), response.content)

    # ---- staff schedule detail ----
    def test_staff_can_view_any_schedule(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("vaccination:schedule_detail", args=[self.alice_sched.pk])
        )
        self.assertEqual(response.status_code, 200)

    def test_patient_redirected_away_from_staff_schedule_detail(self):
        """A patient hitting the staff schedule_detail URL must not see the data.
        Either the middleware or the view guard bounces them to their portal."""
        self.client.force_login(self.bob)
        response = self.client.get(
            reverse("vaccination:schedule_detail", args=[self.alice_sched.pk]),
            follow=True,
        )
        self.assertNotContains(response, self.alice_sched.bite_case.case_number)
        # Ultimately lands on the patient portal, not the schedule page.
        self.assertEqual(response.redirect_chain[-1][0], reverse("accounts:patient_portal"))


class DoctorAssessmentOnlyViewTests(TestCase):
    """View on Vaccination Records shows the doctor assessment and nothing else."""

    def setUp(self):
        self.nurse = U.objects.create_user("n.assess", password="pw12345678", role="nurse")
        self.admin = U.objects.create_user("a.assess", password="pw12345678", role="admin")
        self.patient_user = U.objects.create_user("p.assess", password="pw12345678", role="patient")
        self.patient = Patient.objects.create(
            patient_number="PAT-A0001", first_name="Maria", last_name="Santos",
            birthdate=datetime.date(1999, 6, 6), sex="female",
            contact_number="09171112222", address="Purok 3",
            account=self.patient_user,
        )
        self.case = AnimalBiteCase.objects.create(
            case_number="ABC-A0001", patient=self.patient, created_by=self.admin,
            animal_type="dog", exposure_type="bite",
            date_time_bite=datetime.datetime(2026, 10, 1, 9, 0),
        )
        self.schedule = VaccinationSchedule.objects.create(bite_case=self.case, created_by=self.nurse)
        VaccineDose.objects.create(
            schedule=self.schedule, dose_label="Day 0",
            scheduled_date=datetime.date(2026, 10, 1), dose_status="completed",
            actual_date=datetime.date(2026, 10, 1), protocol_day=0,
        )
        VaccineDose.objects.create(
            schedule=self.schedule, dose_label="Day 3", protocol_day=3, dose_status="pending",
        )
        self.medicine = MedicineInventory.objects.create(
            medicine_name="Amoxicillin", dosage_form="capsule", unit="capsule",
            batch_number="MED-A1", expiration_date=datetime.date(2027, 5, 1),
            quantity_received=30, quantity_available=30,
        )
        self.assessment = ClinicalAssessment.objects.create(
            patient=self.patient, bite_case=self.case, assessed_by=self.admin,
            chief_complaint="Dog bite on the left leg.",
            exposure_narrative="Verified: stray dog bit the patient while walking.",
            category_confirmed="category_iii", pep_indicated=True,
            vaccine_brand_plan="Verorab", vaccine_dose_ml="0.5", vaccine_quantity=5,
            dose_schedule_plan="Days 0, 3, 7, 14, 28", dose_protocol="essen",
            first_dose_date=datetime.date(2026, 10, 1),
            treatment_plan="Wound care and complete PEP series.",
            dosage_instructions="Take one capsule every 8 hours for 7 days.",
            follow_up_instructions="Return on Day 3 and Day 7.",
            additional_notes="No known drug allergies.",
            status="final", is_finalized=True,
        )
        Prescription.objects.create(
            assessment=self.assessment, patient=self.patient, bite_case=self.case,
            medicine=self.medicine, quantity=10,
        )
        self.url = reverse("vaccination:assessment_detail", args=[self.schedule.pk])

    def test_nurse_sees_only_the_doctor_assessment(self):
        self.client.force_login(self.nurse)
        content = self.client.get(self.url).content.decode()

        self.assertIn("Doctor's Assessment", content)
        self.assertIn("MS", content)
        self.assertIn(self.patient.full_name(), content)
        for text in (
            "Chief Complaint", "Dog bite on the left leg.",
            "Exposure Narrative", "Verified: stray dog bit",
            "WHO bite category", "Category III",
            "Dose Schedule", "Vaccine Brand", "Verorab",
            "Treatment &amp; Prescription", "Wound care and complete PEP series.",
            "Amoxicillin", "10 capsules",
            "Take one capsule every 8 hours",
            "Return on Day 3 and Day 7.",
            "No known drug allergies.",
        ):
            with self.subTest(text=text):
                self.assertIn(text, content)

    def test_patient_case_and_vitals_sections_are_not_rendered(self):
        self.client.force_login(self.nurse)
        content = self.client.get(self.url).content.decode()

        self.assertNotIn("Patient Information", content)
        self.assertNotIn("Animal Bite Case", content)
        self.assertNotIn("Vital Signs", content)
        self.assertNotIn("NO VITALS YET", content)
        self.assertNotIn("Date of incident", content)
        self.assertNotIn("Registered date", content)

    def test_assessment_page_has_no_editable_controls(self):
        self.client.force_login(self.nurse)
        content = self.client.get(self.url).content.decode()
        # Scope to the record sheet: the global app shell (QR scanner) has its
        # own lookup form, which is not part of the assessment.
        sheet = content.split('mx-auto record-sheet"', 1)[1].split("</main>", 1)[0]
        self.assertNotIn("<form", sheet)
        self.assertNotIn("<input", sheet)
        self.assertNotIn("<select", sheet)
        self.assertNotIn("<textarea", sheet)

    def test_draft_is_labelled_as_an_unapproved_draft(self):
        ClinicalAssessment.objects.filter(pk=self.assessment.pk).update(
            status="draft", is_finalized=False,
        )
        self.client.force_login(self.nurse)
        content = self.client.get(self.url).content.decode()

        self.assertIn("ASSESSMENT DRAFT", content)
        self.assertIn("not an approved medication or vaccination order", content)

    def test_no_assessment_shows_no_assessment_yet(self):
        self.assessment.delete()
        self.client.force_login(self.nurse)
        content = self.client.get(self.url).content.decode()
        self.assertIn("NO ASSESSMENT YET", content)

    def test_patient_cannot_open_the_assessment_view(self):
        self.client.force_login(self.patient_user)
        response = self.client.get(self.url, follow=True)
        self.assertEqual(response.redirect_chain[-1][0], reverse("accounts:patient_portal"))

    def test_viewing_never_touches_stock_or_records(self):
        stock = VaccineInventory.objects.create(
            vaccine_name="Rabies Vaccine", brand="Verorab", batch_number="LOT-V1",
            expiration_date=datetime.date(2027, 1, 1),
            quantity_received=10, quantity_available=10, quantity_used=0,
        )
        audit_before = AuditLog.objects.count()
        self.client.force_login(self.nurse)
        self.client.get(self.url)

        stock.refresh_from_db()
        self.assertEqual((stock.quantity_available, stock.quantity_used), (10, 0))
        self.assertEqual(AuditLog.objects.count(), audit_before)


@override_settings(SEMAPHORE_ENABLED=False)
class NurseVaccinationManageTests(TestCase):
    """Manage page: confirmed administration/dispensing deduct stock exactly once."""

    def setUp(self):
        self.nurse = U.objects.create_user("n.manage", password="pw12345678", role="nurse")
        self.admin = U.objects.create_user("a.manage", password="pw12345678", role="admin")
        self.doctor = U.objects.create_user("d.manage", password="pw12345678", role="doctor")
        self.patient_user = U.objects.create_user("p.manage", password="pw12345678", role="patient")
        self.patient = Patient.objects.create(
            patient_number="PAT-M0001", first_name="Jose", last_name="Cruz",
            birthdate=datetime.date(1988, 8, 8), sex="male",
            contact_number="09173334444", address="Purok 4",
            account=self.patient_user,
        )
        self.case = AnimalBiteCase.objects.create(
            case_number="ABC-M0001", patient=self.patient, created_by=self.admin,
            animal_type="dog", exposure_type="bite",
            date_time_bite=datetime.datetime(2026, 10, 1, 9, 0),
        )
        self.assessment = ClinicalAssessment.objects.create(
            patient=self.patient, bite_case=self.case, assessed_by=self.admin,
            chief_complaint="Bite wound",
            category_confirmed="category_iii", pep_indicated=True,
            vaccine_brand_plan="Verorab", vaccine_lot_number="LOT-V1",
            vaccine_dose_ml="0.5", vaccine_quantity=5,
            dose_protocol="essen", dose_schedule_plan="Days 0, 3, 7, 14, 28",
            first_dose_date=datetime.date(2026, 10, 1),
            treatment_plan="Complete PEP and antibiotics.",
            dosage_instructions="One capsule every 8 hours for 7 days.",
            follow_up_instructions="Return for the next dose.",
            status="final", is_finalized=True,
        )
        self.schedule = self.case.vaccination_schedule
        self.first_dose = self.schedule.doses.order_by("id").first()
        self.medicine = MedicineInventory.objects.create(
            medicine_name="Amoxicillin", dosage_form="capsule", unit="capsule",
            batch_number="MED-M1", expiration_date=datetime.date(2027, 5, 1),
            quantity_received=30, quantity_available=30,
        )
        self.rx = Prescription.objects.create(
            assessment=self.assessment, patient=self.patient, bite_case=self.case,
            medicine=self.medicine, quantity=10,
        )
        self.vaccine = VaccineInventory.objects.create(
            vaccine_name="Rabies Vaccine", brand="Verorab", batch_number="LOT-V1",
            expiration_date=datetime.date(2027, 1, 1),
            quantity_received=10, quantity_available=10, quantity_used=0,
        )
        self.url = reverse("vaccination:nurse_schedule_manage", args=[self.schedule.pk])

    def test_manage_page_shows_both_sections_and_prescriptions(self):
        self.client.force_login(self.nurse)
        response = self.client.get(self.url)
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        for text in ("Administer Vaccine", "Confirm Prescribed Medicines",
                     "Verorab", "Amoxicillin", "10 capsules", "Pending Dispensing"):
            with self.subTest(text=text):
                self.assertIn(text, content)
        self.assertContains(response, reverse("vaccination:dose_administer", args=[self.first_dose.pk]))
        self.assertContains(response, reverse("inventory:prescription_dispense", args=[self.rx.pk]))

    def test_other_roles_cannot_open_management(self):
        for user in (self.doctor, self.patient_user):
            self.client.force_login(user)
            with self.subTest(role=user.role):
                response = self.client.get(self.url, follow=True)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(self.patient.full_name().encode(), response.content)

    def test_confirming_administration_completes_dose_and_deducts_once(self):
        actual_date = timezone.localdate()
        payload = {
            "actual_date": actual_date.isoformat(),
            "actual_time": "10:30",
            "vaccine_brand": "Verorab",
            "batch_number": "LOT-V1",
        }
        self.client.force_login(self.nurse)
        self.client.post(reverse("vaccination:dose_administer", args=[self.first_dose.pk]), payload)

        self.first_dose.refresh_from_db()
        self.vaccine.refresh_from_db()
        self.assertEqual(self.first_dose.dose_status, "completed")
        self.assertEqual(self.first_dose.actual_date, actual_date)
        self.assertEqual(self.first_dose.actual_time, datetime.time(10, 30))
        self.assertEqual(self.first_dose.administered_by, self.nurse)
        self.assertEqual(self.vaccine.quantity_available, 9)
        self.assertEqual(self.vaccine.quantity_used, 1)
        self.assertTrue(AuditLog.objects.filter(action="ADMINISTER_VACCINE").exists())

        self.client.post(reverse("vaccination:dose_administer", args=[self.first_dose.pk]), payload)
        self.vaccine.refresh_from_db()
        self.assertEqual((self.vaccine.quantity_available, self.vaccine.quantity_used), (9, 1))

    def test_display_brand_with_the_lot_batch_still_deducts_stock(self):
        ClinicalAssessment.objects.filter(pk=self.assessment.pk).update(
            vaccine_brand_plan="Rabies Vaccine (Verorab)")
        self.client.force_login(self.nurse)
        self.client.post(
            reverse("vaccination:dose_administer", args=[self.first_dose.pk]),
            {
                "actual_date": timezone.localdate().isoformat(),
                "vaccine_brand": "Rabies Vaccine (Verorab)",
                "batch_number": "LOT-V1",
            },
        )
        self.vaccine.refresh_from_db()
        self.assertEqual((self.vaccine.quantity_available, self.vaccine.quantity_used), (9, 1))

    def test_administration_is_blocked_until_the_assessment_is_final(self):
        ClinicalAssessment.objects.filter(pk=self.assessment.pk).update(
            status="draft", is_finalized=False,
        )
        self.client.force_login(self.nurse)
        response = self.client.post(
            reverse("vaccination:dose_administer", args=[self.first_dose.pk]),
            {"actual_date": timezone.localdate().isoformat(), "vaccine_brand": "Verorab"},
            follow=True,
        )
        self.first_dose.refresh_from_db()
        self.vaccine.refresh_from_db()
        self.assertEqual(self.first_dose.dose_status, "scheduled")
        self.assertEqual(self.vaccine.quantity_available, 10)
        self.assertContains(response, "must be finalized")

    def test_scheduling_a_pending_dose_does_not_touch_stock(self):
        pending = self.schedule.doses.exclude(pk=self.first_dose.pk).order_by("id").first()
        earliest = self.first_dose.scheduled_date + datetime.timedelta(days=pending.protocol_day)
        self.client.force_login(self.nurse)
        self.client.post(
            reverse("vaccination:dose_schedule", args=[pending.pk]),
            {"scheduled_date": earliest.isoformat()},
        )

        pending.refresh_from_db()
        self.vaccine.refresh_from_db()
        self.assertEqual(pending.scheduled_date, earliest)
        self.assertEqual(pending.dose_status, "scheduled")
        self.assertEqual(self.vaccine.quantity_available, 10)
        self.assertEqual(self.vaccine.quantity_used, 0)

    def test_schedule_date_before_protocol_is_rejected(self):
        pending = self.schedule.doses.exclude(pk=self.first_dose.pk).order_by("id").first()
        too_early = self.first_dose.scheduled_date + datetime.timedelta(days=1)
        self.client.force_login(self.nurse)
        self.client.post(
            reverse("vaccination:dose_schedule", args=[pending.pk]),
            {"scheduled_date": too_early.isoformat()},
        )
        pending.refresh_from_db()
        self.assertIsNone(pending.scheduled_date)

    def test_confirming_dispensing_deducts_medicine_once(self):
        url = reverse("inventory:prescription_dispense", args=[self.rx.pk])
        self.client.force_login(self.nurse)
        self.client.post(url, {"next": self.url})

        self.rx.refresh_from_db()
        self.medicine.refresh_from_db()
        self.assertEqual(self.rx.status, "dispensed")
        self.assertEqual(self.rx.dispensed_by, self.nurse)
        self.assertIsNotNone(self.rx.dispensed_at)
        self.assertEqual(self.medicine.quantity_available, 20)
        self.assertEqual(self.medicine.quantity_dispensed, 10)
        self.assertTrue(AuditLog.objects.filter(action="DISPENSE_MEDICINE").exists())

        self.client.post(url, {"next": self.url})
        self.medicine.refresh_from_db()
        self.assertEqual((self.medicine.quantity_available, self.medicine.quantity_dispensed), (20, 10))

    def test_draft_assessment_cannot_dispense_medicines(self):
        ClinicalAssessment.objects.filter(pk=self.assessment.pk).update(
            status="draft", is_finalized=False,
        )
        self.client.force_login(self.nurse)
        self.client.post(
            reverse("inventory:prescription_dispense", args=[self.rx.pk]),
            {"next": self.url},
        )
        self.rx.refresh_from_db()
        self.medicine.refresh_from_db()
        self.assertEqual(self.rx.status, "pending")
        self.assertEqual(self.medicine.quantity_available, 30)
        self.assertFalse(AuditLog.objects.filter(action="DISPENSE_MEDICINE").exists())

    def test_opening_management_never_changes_stock(self):
        audit_before = AuditLog.objects.count()
        self.client.force_login(self.nurse)
        self.client.get(self.url)

        self.vaccine.refresh_from_db()
        self.medicine.refresh_from_db()
        self.assertEqual((self.vaccine.quantity_available, self.vaccine.quantity_used), (10, 0))
        self.assertEqual(self.medicine.quantity_available, 30)
        self.assertEqual(AuditLog.objects.count(), audit_before)

    def test_print_schedule_button_sits_below_section_b(self):
        self.client.force_login(self.nurse)
        response = self.client.get(self.url)
        content = response.content.decode()
        print_url = reverse("vaccination:nurse_schedule_print", args=[self.schedule.pk])

        self.assertIn(print_url, content)
        self.assertIn("Print Schedule", content)
        self.assertIn("btn-print-schedule", content)
        self.assertGreater(
            content.index("Print Schedule"),
            content.index("Confirm Prescribed Medicines"),
        )

    def test_print_slip_shows_patient_vaccine_and_saved_schedule(self):
        first = self.first_dose
        first.dose_status = "completed"
        first.actual_date = datetime.date(2026, 10, 1)
        first.scheduled_date = datetime.date(2026, 10, 1)
        first.save(update_fields=["dose_status", "actual_date", "scheduled_date"])

        second = self.schedule.doses.order_by("id")[1]
        second.dose_status = "scheduled"
        second.scheduled_date = datetime.date(2026, 10, 4)
        second.save(update_fields=["dose_status", "scheduled_date"])

        self.client.force_login(self.nurse)
        response = self.client.get(
            reverse("vaccination:nurse_schedule_print", args=[self.schedule.pk]))
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("/static/images/lgu_dumingag_logo.png", content)
        self.assertIn("size: A4 portrait", content)
        self.assertIn("width: 60px", content)
        for text in (
            "Animal Bite Treatment Center", "Municipal Health Office",
            "Patient Vaccination Schedule", "Patient Information",
            "Vaccination Information", "Vaccination Schedule",
            "Appointment Reminder",
            self.patient.full_name(), self.patient.patient_number,
            self.case.case_number, "Verorab", "5 Doses", "Oct 01, 2026",
            "1st Dose", "2nd Dose", "3rd Dose", "4th Dose", "5th Dose",
            "Completed", "Scheduled", "Pending Scheduling", "Not yet set",
        ):
            with self.subTest(text=text):
                self.assertIn(text, content)
        self.assertIn(timezone.localdate().strftime("%B %d, %Y"), content)
        self.assertIn(
            "Please follow your confirmed vaccination appointments. "
            "For doses marked Pending Scheduling, contact the Animal Bite "
            "Treatment Center to confirm your next appointment. Bring this "
            "schedule slip during your clinic visits.",
            content,
        )
        for hidden in (
            "Confirm Prescribed Medicines", "Administer Vaccine",
            "Confirm Dispensed", "Set date", "Nurse Portal",
        ):
            with self.subTest(hidden=hidden):
                self.assertNotIn(hidden, content)

    def test_print_slip_uses_the_doctor_selected_dose_count(self):
        ClinicalAssessment.objects.filter(pk=self.assessment.pk).update(
            dose_protocol="zagreb", dose_schedule_plan="Days 0, 7, 21",
        )
        self.client.force_login(self.nurse)
        content = self.client.get(
            reverse("vaccination:nurse_schedule_print", args=[self.schedule.pk])
        ).content.decode()

        self.assertIn("3 Doses", content)
        self.assertIn("3rd Dose", content)
        self.assertNotIn("4th Dose", content)
        self.assertNotIn("5th Dose", content)

    def test_print_slip_identifies_rig_by_its_inventory_name(self):
        rig = VaccineInventory.objects.create(
            vaccine_name="RIG (Rabies Immunoglobulin)", brand="Imogam",
            batch_number="RIG-P1", expiration_date=datetime.date(2027, 6, 1),
            quantity_received=5, quantity_available=5,
        )
        ClinicalAssessment.objects.filter(pk=self.assessment.pk).update(
            vaccine_inventory=rig)
        self.client.force_login(self.nurse)
        content = self.client.get(
            reverse("vaccination:nurse_schedule_print", args=[self.schedule.pk])
        ).content.decode()

        self.assertIn("RIG (Rabies Immunoglobulin) (Imogam)", content)

    def test_print_slip_reflects_updated_dates_without_writing_records(self):
        pending = self.schedule.doses.exclude(pk=self.first_dose.pk).order_by("id").first()
        pending.dose_status = "scheduled"
        pending.scheduled_date = datetime.date(2026, 10, 20)
        pending.save(update_fields=["dose_status", "scheduled_date"])
        stock_before = (self.vaccine.quantity_available, self.vaccine.quantity_used)
        audit_before = AuditLog.objects.count()

        self.client.force_login(self.nurse)
        content = self.client.get(
            reverse("vaccination:nurse_schedule_print", args=[self.schedule.pk])
        ).content.decode()

        self.assertIn("Oct 20, 2026", content)
        pending.refresh_from_db()
        self.assertEqual(pending.scheduled_date, datetime.date(2026, 10, 20))
        self.vaccine.refresh_from_db()
        self.assertEqual(
            (self.vaccine.quantity_available, self.vaccine.quantity_used), stock_before)
        self.assertEqual(AuditLog.objects.count(), audit_before)
        self.assertNotIn("Oct 04, 2026", content)
        self.assertNotIn("Oct 08, 2026", content)

    def test_only_vaccination_managers_can_open_the_print_slip(self):
        url = reverse("vaccination:nurse_schedule_print", args=[self.schedule.pk])
        for user in (self.nurse, self.admin):
            self.client.force_login(user)
            with self.subTest(role=user.role):
                self.assertEqual(self.client.get(url).status_code, 200)

        for user, destination in (
            (self.doctor, reverse("dashboard:index")),
            (self.patient_user, reverse("accounts:patient_portal")),
        ):
            self.client.force_login(user)
            with self.subTest(role=user.role):
                response = self.client.get(url)
                self.assertRedirects(
                    response, destination, fetch_redirect_response=False)

    # -------------------------------------------------- SMS reminders
    def _next_dose_and_protocol_date(self):
        pending = (
            self.schedule.doses.exclude(pk=self.first_dose.pk).order_by("id").first()
        )
        target = self.first_dose.scheduled_date + datetime.timedelta(
            days=pending.protocol_day)
        return pending, target

    def _schedule(self, dose, target):
        return self.client.post(
            reverse("vaccination:dose_schedule", args=[dose.pk]),
            {"scheduled_date": target.isoformat()},
            follow=True,
        )

    def test_setting_a_dose_date_texts_the_patient(self):
        pending, target = self._next_dose_and_protocol_date()
        self.client.force_login(self.nurse)

        response = self._schedule(pending, target)

        pending.refresh_from_db()
        log = SMSLog.objects.get(dose=pending)
        self.assertEqual(pending.scheduled_date, target)
        self.assertEqual(log.reminder_type, "schedule_set")
        self.assertEqual(log.scheduled_date, target)
        self.assertTrue(log.recipient.endswith("9173334444"))
        self.assertIn(log.status, ("queued", "sent"))
        self.assertIn("SMS reminder sent", response.content.decode())
        self.assertTrue(AuditLog.objects.filter(action="SEND_SMS").exists())

    def test_resaving_the_same_date_does_not_resend(self):
        pending, target = self._next_dose_and_protocol_date()
        self.client.force_login(self.nurse)

        self._schedule(pending, target)
        self._schedule(pending, target)

        self.assertEqual(
            SMSLog.objects.filter(
                dose=pending, reminder_type="schedule_set").count(),
            1,
        )

    def test_changing_the_date_sends_an_updated_confirmation(self):
        pending, target = self._next_dose_and_protocol_date()
        later = target + datetime.timedelta(days=1)
        self.client.force_login(self.nurse)

        self._schedule(pending, target)
        self._schedule(pending, later)

        logs = SMSLog.objects.filter(
            dose=pending, reminder_type="schedule_set").order_by("sent_at")
        self.assertEqual(logs.count(), 2)
        self.assertEqual(logs.last().scheduled_date, later)

    def test_missing_patient_number_still_saves_the_schedule(self):
        pending, target = self._next_dose_and_protocol_date()
        Patient.objects.filter(pk=self.patient.pk).update(
            contact_number="not-a-number",
            emergency_contact_number="",
            account=None,
        )
        self.client.force_login(self.nurse)

        response = self._schedule(pending, target)

        pending.refresh_from_db()
        self.assertEqual(pending.scheduled_date, target)
        self.assertFalse(SMSLog.objects.filter(dose=pending).exists())
        self.assertIn("no valid mobile number", response.content.decode())

    def test_manual_reminder_for_a_scheduled_dose(self):
        self.client.force_login(self.nurse)

        response = self.client.post(
            reverse("vaccination:send_sms_for_dose", args=[self.first_dose.pk]),
            {"reminder_type": "on_day"},
            follow=True,
        )

        log = SMSLog.objects.get(dose=self.first_dose)
        self.assertEqual(log.reminder_type, "on_day")
        self.assertIn("SMS reminder sent", response.content.decode())

    def test_send_due_reminders_is_one_click_and_never_duplicates(self):
        today = timezone.localdate()
        self.first_dose.scheduled_date = today
        self.first_dose.dose_status = "scheduled"
        self.first_dose.save(update_fields=["scheduled_date", "dose_status"])
        self.client.force_login(self.nurse)
        url = reverse("vaccination:trigger_sms_reminders")

        first = self.client.post(url, {"rtype": "all"}, follow=True)
        self.assertContains(first, "Reminders sent: 1")
        self.assertEqual(
            SMSLog.objects.filter(
                dose=self.first_dose, reminder_type="on_day").count(),
            1,
        )

        second = self.client.post(url, {"rtype": "all"}, follow=True)
        self.assertContains(second, "Already sent today: 1")
        self.assertEqual(
            SMSLog.objects.filter(
                dose=self.first_dose, reminder_type="on_day").count(),
            1,
        )

    def test_sms_pages_do_not_name_the_provider(self):
        self.client.force_login(self.nurse)
        for url in (
            reverse("vaccination:sms_log_list"),
            reverse("vaccination:schedule_detail", args=[self.schedule.pk]),
        ):
            content = self.client.get(url).content.decode()
            with self.subTest(url=url):
                for term in ("Semaphore", "SEMAPHORE", "RHUDumingag",
                             "api.semaphore"):
                    self.assertNotIn(term, content)

        content = self.client.get(
            reverse("vaccination:sms_log_list")).content.decode()
        self.assertIn("Send Due Reminders", content)
        self.assertIn("Confirmation", content)


class SmsReminderPagesTests(TestCase):
    """The Admin and Nurse portals share one redesigned SMS reminders surface."""

    def setUp(self):
        self.admin = U.objects.create_user("a.sms", password="pw12345678", role="admin")
        self.nurse = U.objects.create_user("n.sms", password="pw12345678", role="nurse")
        self.doctor = U.objects.create_user("d.sms", password="pw12345678", role="doctor")
        self.patient_user = U.objects.create_user("p.sms", password="pw12345678", role="patient")
        self.patient = Patient.objects.create(
            patient_number="PAT-S0001", first_name="Maria", last_name="Santos",
            birthdate=datetime.date(1990, 1, 1), contact_number="09173334444",
            account=self.patient_user,
        )
        self.case = AnimalBiteCase.objects.create(
            case_number="ABC-S0001", patient=self.patient, created_by=self.admin,
            animal_type="dog", exposure_type="bite",
            date_time_bite=datetime.datetime(2026, 10, 1, 9, 0),
        )
        self.schedule = VaccinationSchedule.objects.create(
            bite_case=self.case, created_by=self.admin)
        self.first_dose = VaccineDose.objects.create(
            schedule=self.schedule, dose_label="Day 0",
            scheduled_date=datetime.date(2026, 11, 10), dose_status="scheduled")
        self.second_dose = VaccineDose.objects.create(
            schedule=self.schedule, dose_label="Day 3", protocol_day=3,
            scheduled_date=datetime.date(2026, 11, 13), dose_status="scheduled")
        self.confirmation = SMSLog.objects.create(
            dose=self.first_dose, patient=self.patient, bite_case=self.case,
            reminder_type="schedule_set", recipient="639173334444",
            message="ABTC Dumingag: your appointment is confirmed.",
            scheduled_date=datetime.date(2026, 11, 10), status="queued",
        )
        # No patient FK on this row: the display must fall back to the case.
        self.advance = SMSLog.objects.create(
            dose=self.second_dose, bite_case=self.case,
            reminder_type="3days_before", recipient="639173334444",
            message="ABTC Dumingag: your appointment is in 3 days.",
            scheduled_date=datetime.date(2026, 11, 13), status="sent",
        )
        self.failed = SMSLog.objects.create(
            patient=self.patient, reminder_type="on_day",
            recipient="639173334444",
            message="ABTC Dumingag: your appointment is today.",
            scheduled_date=datetime.date(2026, 11, 10), status="failed",
            api_response="Semaphore error: SEMAPHORE_API_KEY not configured",
        )

    def _list(self, user, **params):
        self.client.force_login(user)
        return self.client.get(reverse("vaccination:sms_log_list"), params)

    def test_both_managers_see_the_same_redesigned_layout(self):
        markers = (
            "SMS Reminders",
            "Manage and monitor patient vaccination reminders.",
            "Send Due Reminders",
            "How SMS Reminders Work",
            "Confirmation SMS",
            "Advance Reminder",
            "On-Day Reminder",
            "Search Reminders",
            "Search patient name, case number, or mobile number...",
            "Reminder History",
            "Patient / Case",
            "Dose / Date",
            "Reminder Type",
            "Status",
            "Actions",
        )
        for user in (self.admin, self.nurse):
            with self.subTest(role=user.role):
                response = self._list(user)
                self.assertEqual(response.status_code, 200)
                content = response.content.decode()
                for text in markers:
                    self.assertIn(text, content)
                self.assertEqual(response.context["total"], 3)
                self.assertEqual(response.context["sent"], 1)
                self.assertEqual(response.context["queued"], 1)
                self.assertEqual(response.context["failed"], 1)
                self.assertContains(response, reverse(
                    "vaccination:sms_log_detail", args=[self.confirmation.pk]))
                # Message bodies belong on the details page, not the history table.
                self.assertNotIn(self.confirmation.message, content)
                # Only the search field remains; the extra filters are hidden.
                self.assertNotIn('id="sms-type"', content)
                self.assertNotIn('id="sms-status"', content)
                self.assertNotIn('id="sms-scheduled"', content)
                self.assertNotIn("Apply Filters", content)
                self.assertNotIn(">Reset<", content)

    def test_history_maps_patient_case_and_saved_dose_sequence(self):
        content = self._list(self.nurse).content.decode()
        self.assertIn("Maria Santos", content)
        self.assertIn("ABC-S0001", content)
        self.assertIn("1st Dose", content)
        self.assertIn("2nd Dose", content)
        self.assertNotIn("Day 0", content)
        self.assertNotIn("Day 3", content)

    def test_reminder_type_uses_portal_labels(self):
        content = self._list(self.nurse).content.decode()
        self.assertIn("Confirmation", content)
        self.assertIn("3 Days Before", content)
        self.assertIn("On Day", content)
        self.assertNotIn('<option value="schedule_set"', content)

    def test_filters_narrow_the_history(self):
        self.assertEqual(
            self._list(self.nurse, type="3days_before").context["page_obj"].paginator.count, 1)
        self.assertEqual(
            self._list(self.nurse, status="failed").context["page_obj"].paginator.count, 1)
        self.assertEqual(
            self._list(self.nurse, scheduled="2026-11-13").context["page_obj"].paginator.count, 1)
        self.assertEqual(
            self._list(self.nurse, q="ABC-S0001").context["page_obj"].paginator.count, 2)
        self.assertEqual(
            self._list(self.nurse, q="Maria").context["page_obj"].paginator.count, 3)
        self.assertEqual(
            self._list(self.nurse, q="639173334444").context["page_obj"].paginator.count, 3)
        self.assertEqual(
            self._list(self.nurse, q="PAT-S0001").context["page_obj"].paginator.count, 2)
        self.assertEqual(
            self._list(self.nurse, q="").context["page_obj"].paginator.count, 3)

        empty = self._list(self.nurse, q="no-such-patient")
        self.assertEqual(empty.context["page_obj"].paginator.count, 0)
        self.assertIn("No reminders match your search", empty.content.decode())

    def test_detail_page_shows_the_complete_saved_record(self):
        self.client.force_login(self.nurse)
        response = self.client.get(
            reverse("vaccination:sms_log_detail", args=[self.confirmation.pk]))
        content = response.content.decode()
        self.assertEqual(response.status_code, 200)
        for text in (
            "SMS Reminder Details",
            "Maria Santos",
            "ABC-S0001",
            "1st Dose",
            "Confirmation",
            "639173334444",
            "Nov 10, 2026",
            "Complete SMS Message",
            self.confirmation.message,
        ):
            with self.subTest(text=text):
                self.assertIn(text, content)
        self.assertNotIn("Semaphore", content)
        self.assertNotIn("RHUDumingag", content)

    def test_detail_page_shows_a_sanitized_failure_reason(self):
        self.client.force_login(self.nurse)
        content = self.client.get(
            reverse("vaccination:sms_log_detail", args=[self.failed.pk])).content.decode()
        self.assertIn("Failure Reason", content)
        self.assertIn("SMS provider", content)
        self.assertIn("not configured", content)
        self.assertNotIn("Semaphore", content)

    def test_non_staff_roles_cannot_reach_sms_pages(self):
        detail_url = reverse("vaccination:sms_log_detail", args=[self.confirmation.pk])
        for user, destination in (
            (self.doctor, reverse("dashboard:index")),
            (self.patient_user, reverse("accounts:patient_portal")),
        ):
            self.client.force_login(user)
            for url in (reverse("vaccination:sms_log_list"), detail_url):
                with self.subTest(role=user.role, url=url):
                    self.assertRedirects(
                        self.client.get(url), destination,
                        fetch_redirect_response=False)

    def test_pagination_keeps_active_filters(self):
        for index in range(18):
            SMSLog.objects.create(
                dose=self.first_dose, bite_case=self.case,
                reminder_type="schedule_set", recipient="639173334444",
                message="ABTC Dumingag: extra reminder %s." % index,
                scheduled_date=datetime.date(2026, 11, 10), status="sent",
            )
        response = self._list(self.nurse, q="Maria")
        self.assertEqual(response.context["page_obj"].paginator.count, 21)
        self.assertIn("?page=2&q=Maria", response.content.decode())


@override_settings(SEMAPHORE_ENABLED=False)
class SmsUpcomingAndManualSendTests(TestCase):
    """Upcoming 2nd+ doses and the additional manual reminder action."""

    def setUp(self):
        self.admin = U.objects.create_user("a.up", password="pw12345678", role="admin")
        self.nurse = U.objects.create_user("n.up", password="pw12345678", role="nurse")
        self.doctor = U.objects.create_user("d.up", password="pw12345678", role="doctor")
        self.patient_user = U.objects.create_user("p.up", password="pw12345678", role="patient")
        self.patient = Patient.objects.create(
            patient_number="PAT-U0001", first_name="Juan", last_name="Dela Cruz",
            birthdate=datetime.date(1988, 5, 5), contact_number="09173334444",
            account=self.patient_user,
        )
        self.case = AnimalBiteCase.objects.create(
            case_number="ABC-U0001", patient=self.patient, created_by=self.admin,
            animal_type="dog", exposure_type="bite",
            date_time_bite=datetime.datetime(2026, 10, 1, 9, 0),
        )
        self.schedule = VaccinationSchedule.objects.create(
            bite_case=self.case, created_by=self.admin)
        self.today = timezone.localdate()
        day = datetime.timedelta(days=1)
        self.first = VaccineDose.objects.create(
            schedule=self.schedule, dose_label="Day 0",
            scheduled_date=self.today + 2 * day, dose_status="scheduled")
        self.second = VaccineDose.objects.create(
            schedule=self.schedule, dose_label="Day 3", protocol_day=3,
            scheduled_date=self.today + 2 * day, dose_status="scheduled")
        self.third = VaccineDose.objects.create(
            schedule=self.schedule, dose_label="Day 7", protocol_day=7,
            scheduled_date=self.today + day, dose_status="scheduled")
        self.fourth = VaccineDose.objects.create(
            schedule=self.schedule, dose_label="Day 14", protocol_day=14,
            scheduled_date=self.today + 5 * day, dose_status="rescheduled")
        self.due = VaccineDose.objects.create(
            schedule=self.schedule, dose_label="Day 28", protocol_day=28,
            scheduled_date=self.today, dose_status="scheduled")
        self.completed = VaccineDose.objects.create(
            schedule=self.schedule, dose_label="Day 35", protocol_day=35,
            scheduled_date=self.today + 2 * day, dose_status="completed")
        self.cancelled = VaccineDose.objects.create(
            schedule=self.schedule, dose_label="Day 42", protocol_day=42,
            scheduled_date=self.today + 2 * day, dose_status="cancelled")
        self.unscheduled = VaccineDose.objects.create(
            schedule=self.schedule, dose_label="Day 49", protocol_day=49,
            dose_status="pending")
        self.list_url = reverse("vaccination:sms_log_list")
        self.send_url = reverse("vaccination:sms_manual_send", args=[self.second.pk])

    def _list(self, user=None, **params):
        self.client.force_login(user or self.nurse)
        return self.client.get(self.list_url, params)

    def _send(self, dose=None, kind="manual_2days_before", note="",
              confirm=False, user=None):
        self.client.force_login(user or self.nurse)
        payload = {"reminder_type": kind, "custom_message": note}
        if confirm:
            payload["confirm_duplicate"] = "1"
        dose = dose or self.second
        return self.client.post(
            reverse("vaccination:sms_manual_send", args=[dose.pk]), payload)

    def test_upcoming_lists_only_eligible_second_dose_onwards(self):
        response = self._list()
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()

        self.assertIn("Upcoming Vaccination Reminders", content)
        self.assertIn(
            "Patients with upcoming scheduled vaccine doses. Send an additional reminder when necessary.",
            content)
        self.assertEqual(
            [dose.pk for dose in response.context["upcoming_doses"]],
            [self.due.pk, self.third.pk, self.second.pk, self.fourth.pk])
        doses = {dose.pk: dose for dose in response.context["upcoming_doses"]}
        self.assertEqual(doses[self.due.pk].date_badge, "Today")
        self.assertEqual(doses[self.third.pk].date_badge, "Tomorrow")
        self.assertEqual(doses[self.second.pk].date_badge, "2 Days Left")
        self.assertEqual(doses[self.fourth.pk].date_badge, "Upcoming")
        self.assertEqual(doses[self.second.pk].reminder_status_label, "Not Sent")

        self.assertIn("Juan Dela Cruz", content)
        self.assertIn("ABC-U0001", content)
        self.assertIn("2nd Dose", content)
        self.assertIn("3rd Dose", content)
        self.assertIn("4th Dose", content)
        self.assertIn("Send SMS", content)
        self.assertIn("Mobile Number", content)
        self.assertIn("View", content)
        self.assertIn(
            'data-mobile="%s"' % doses[self.second.pk].masked_mobile, content)
        # The full number is available to the details modal, but the table masks it.
        self.assertEqual(doses[self.second.pk].full_mobile, "639173334444")
        self.assertTrue(doses[self.second.pk].has_mobile)
        self.assertIn('data-full-mobile="639173334444"', content)
        self.assertIn('data-has-mobile="1"', content)
        self.assertNotIn(">639173334444<", content)
        self.assertIn("Send Now", content)
        self.assertIn(
            'data-msg-now="%s"' % doses[self.second.pk].preview_immediate, content)
        # Opening the page must never send a reminder.
        self.assertEqual(SMSLog.objects.count(), 0)

    def test_upcoming_details_modal_is_read_only_and_shows_patient_information(self):
        response = self._list()
        content = response.content.decode()
        doses = {dose.pk: dose for dose in response.context["upcoming_doses"]}
        second = doses[self.second.pk]

        self.assertIn("Vaccination Reminder Details", content)
        self.assertIn("Patient appointment information.", content)
        self.assertIn("Section A – Patient Information", content)
        self.assertIn("Section B – SMS Reminder Information", content)
        self.assertIn("Section C – SMS Message Preview", content)
        self.assertIn("No previous SMS record.", content)
        self.assertIn(
            "Preview only — no SMS is sent until you confirm.", content)
        self.assertIn('data-appt-status="Scheduled"', content)
        self.assertIn('data-reminder-status="Not Sent"', content)
        self.assertIn('data-has-sms="0"', content)
        self.assertIn(
            'data-preview-details="%s"' % second.preview_details, content)
        self.assertEqual(
            second.preview_details,
            "ABTC Dumingag: Paalala, Juan Dela Cruz (ABC-U0001) - "
            "2nd Dose sa %s. Pumunta sa RHU sa loob ng 2 araw. "
            "Dalhin ang card. -ABTC Dumingag"
            % second.scheduled_date.strftime("%b %d, %Y (%a)"),
        )
        # Opening the page or its details modal must never send a reminder.
        self.assertEqual(SMSLog.objects.count(), 0)

    def test_upcoming_details_modal_summarises_the_latest_sms_record(self):
        SMSLog.objects.create(
            dose=self.second, patient=self.patient, bite_case=self.case,
            reminder_type="manual_2days_before", recipient="639173334444",
            message="ABTC Dumingag: earlier manual reminder.",
            scheduled_date=self.second.scheduled_date, status="sent",
            sent_at=timezone.now(),
        )
        response = self._list()
        content = response.content.decode()
        doses = {dose.pk: dose for dose in response.context["upcoming_doses"]}
        second = doses[self.second.pk]

        self.assertTrue(second.has_sms)
        self.assertEqual(second.last_sms_type, "Manual – 2 Days Before")
        self.assertEqual(second.last_sms_result, "Sent")
        self.assertTrue(second.last_sms_at)
        self.assertIn('data-has-sms="1"', content)
        self.assertIn('data-last-type="Manual – 2 Days Before"', content)
        self.assertIn('data-last-result="Sent"', content)
        self.assertEqual(
            self.client.get(self.list_url).status_code, 200)
        self.assertEqual(SMSLog.objects.count(), 1)

    def test_upcoming_shows_no_registered_number_and_disables_send(self):
        self.patient.contact_number = "not-a-number"
        self.patient.emergency_contact_number = ""
        self.patient.save(update_fields=["contact_number", "emergency_contact_number"])
        self.patient_user.mobile = ""
        self.patient_user.save(update_fields=["mobile"])

        response = self._list()
        content = response.content.decode()
        doses = {dose.pk: dose for dose in response.context["upcoming_doses"]}
        second = doses[self.second.pk]

        self.assertEqual(second.full_mobile, "")
        self.assertFalse(second.has_mobile)
        self.assertEqual(second.masked_mobile, "No registered number.")
        self.assertIn("No registered number.", content)
        self.assertIn('data-has-mobile="0"', content)
        self.assertIn(
            'disabled title="No registered mobile number"', content)
        self.assertEqual(SMSLog.objects.count(), 0)

    def test_upcoming_filters_use_saved_appointment_dates(self):
        tomorrow = self._list(upcoming="tomorrow")
        self.assertEqual(
            [dose.pk for dose in tomorrow.context["upcoming_doses"]], [self.third.pk])
        two_days = self._list(upcoming="2days")
        self.assertEqual(
            [dose.pk for dose in two_days.context["upcoming_doses"]], [self.second.pk])
        today_qs = self._list(upcoming="today")
        self.assertEqual(
            [dose.pk for dose in today_qs.context["upcoming_doses"]], [self.due.pk])
        all_qs = self._list(upcoming="all")
        self.assertEqual(len(all_qs.context["upcoming_doses"]), 4)
        self.assertEqual(all_qs.context["upcoming_filter"], "all")

    def test_manual_send_records_history_and_details(self):
        response = self._send(self.second, "manual_2days_before")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "queued")

        log = SMSLog.objects.get(
            dose=self.second, reminder_type="manual_2days_before")
        self.assertEqual(log.status, "queued")
        self.assertEqual(log.recipient, "639173334444")
        self.assertEqual(log.patient_id, self.patient.pk)
        self.assertEqual(log.bite_case_id, self.case.pk)
        self.assertEqual(log.scheduled_date, self.second.scheduled_date)
        self.assertIn("2nd Dose", log.message)
        self.assertIn(self.second.scheduled_date.strftime("%b %d, %Y"), log.message)
        self.assertTrue(
            AuditLog.objects.filter(
                action="SEND_SMS", description__contains="Manual SMS").exists())

        listing = self._list().content.decode()
        self.assertIn("Manual – 2 Days Before", listing)
        self.assertIn("Queued", listing)

        detail = self.client.get(
            reverse("vaccination:sms_log_detail", args=[log.pk])).content.decode()
        self.assertIn("Manual – 2 Days Before", detail)
        self.assertIn("Manual", detail)
        self.assertIn(log.message, detail)

    def test_manual_send_now_works_for_any_appointment_date(self):
        two_days = self._send(self.second, "manual_immediate")
        self.assertEqual(two_days.status_code, 200)
        tomorrow = self._send(self.third, "manual_immediate")
        self.assertEqual(tomorrow.status_code, 200)

        log = SMSLog.objects.get(dose=self.second, reminder_type="manual_immediate")
        self.assertEqual(log.get_reminder_type_display(), "Manual – Send Now")
        self.assertIn("2nd Dose", log.message)
        self.assertIn("Pumunta sa RHU sa nakatakdang petsa.", log.message)
        self.assertNotIn("2 araw", log.message)
        self.assertIn(
            "3rd Dose",
            SMSLog.objects.get(dose=self.third, reminder_type="manual_immediate").message)
        self.assertIn("Manual – Send Now", self._list().content.decode())

        duplicate = self._send(self.second, "manual_immediate")
        self.assertEqual(duplicate.status_code, 409)
        self.assertTrue(duplicate.json()["duplicate"])

    def test_manual_send_rejects_mismatched_day_and_inactive_doses(self):
        mismatched = self._send(self.second, "manual_1day_before")
        self.assertEqual(mismatched.status_code, 400)
        self.assertIn("2 day(s) away", mismatched.json()["error"])
        for dose in (self.completed, self.cancelled, self.unscheduled, self.first):
            with self.subTest(dose=dose.pk):
                response = self._send(dose, "manual_2days_before")
                self.assertEqual(response.status_code, 400)
        self.assertEqual(SMSLog.objects.count(), 0)

    def test_manual_send_requires_a_valid_registered_number(self):
        self.patient.contact_number = "not-a-number"
        self.patient.save(update_fields=["contact_number"])
        response = self._send(self.second)
        self.assertEqual(response.status_code, 400)
        self.assertIn("missing or invalid", response.json()["error"])
        self.assertEqual(SMSLog.objects.count(), 0)

    def test_manual_send_warns_on_duplicate_and_requires_confirmation(self):
        self.assertEqual(self._send(self.second).status_code, 200)
        duplicate = self._send(self.second)
        self.assertEqual(duplicate.status_code, 409)
        self.assertTrue(duplicate.json()["duplicate"])
        self.assertIn("already", duplicate.json()["warning"])
        self.assertEqual(SMSLog.objects.filter(dose=self.second).count(), 1)

        again = self._send(self.second)
        self.assertEqual(again.status_code, 409)
        self.assertEqual(SMSLog.objects.filter(dose=self.second).count(), 1)

        confirmed = self._send(self.second, confirm=True)
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(SMSLog.objects.filter(dose=self.second).count(), 2)

    def test_manual_custom_requires_and_uses_a_note(self):
        missing = self._send(self.fourth, "manual_custom")
        self.assertEqual(missing.status_code, 400)
        sent = self._send(
            self.fourth, "manual_custom", note="Palihog tawag sa RHU kung dili makaadto.")
        self.assertEqual(sent.status_code, 200)
        log = SMSLog.objects.get(dose=self.fourth, reminder_type="manual_custom")
        self.assertIn("4th Dose", log.message)
        self.assertIn("Palihog tawag sa RHU kung dili makaadto.", log.message)

    def test_manual_send_is_limited_to_staff_and_post(self):
        self.client.force_login(self.nurse)
        self.assertEqual(self.client.get(self.send_url).status_code, 405)

        doctor_response = self._send(self.second, user=self.doctor)
        self.assertEqual(doctor_response.status_code, 403)
        # Patients are kept inside patient-facing routes by the portal middleware.
        self.client.force_login(self.patient_user)
        patient_response = self.client.post(
            self.send_url, {"reminder_type": "manual_2days_before"})
        self.assertRedirects(
            patient_response, reverse("accounts:patient_portal"),
            fetch_redirect_response=False)
        self.assertEqual(SMSLog.objects.count(), 0)

    def test_manual_send_does_not_create_a_second_automatic_reminder(self):
        automatic = SMSLog.objects.create(
            dose=self.second, patient=self.patient, bite_case=self.case,
            reminder_type="3days_before", recipient="639173334444",
            message="ABTC Dumingag: automatic three-day reminder.",
            scheduled_date=self.second.scheduled_date, status="sent",
        )
        response = self._send(self.second)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(SMSLog.objects.filter(pk=automatic.pk).exists())
        self.assertEqual(SMSLog.objects.filter(dose=self.second).count(), 2)
