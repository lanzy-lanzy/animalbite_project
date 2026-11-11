from datetime import date, datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from bite_cases.models import AnimalBiteCase
from inventory.models import MedicineInventory, VaccineInventory
from patients.models import Patient
from reports.services import animal_type_distribution, barangay_distribution
from vaccination.models import VaccinationSchedule, VaccineDose


def _months_ago(today, count):
    first = today.replace(day=1)
    for _ in range(count):
        first = (first - timedelta(days=1)).replace(day=1)
    return first


class AdminDashboardChartTests(TestCase):
    """The admin dashboard renders six live charts from saved records; other
    roles keep their existing dashboards."""

    @classmethod
    def setUpTestData(cls):
        cls.User = get_user_model()
        cls.admin = cls.User.objects.create_user(
            "admin.charts", password="pw12345678", role="admin")
        cls.patient = Patient.objects.create(
            patient_number="PAT-CHART1", first_name="Chart", last_name="Tester",
            birthdate=date(1990, 1, 1), sex="male",
            contact_number="09175550001", address="Poblacion",
        )

    def setUp(self):
        self.today = timezone.localdate()
        self.client.force_login(self.admin)

    def _case(self, number, **kwargs):
        defaults = {
            "case_number": number,
            "patient": self.patient,
            "date_time_bite": timezone.now(),
            "incident_barangay": "San Pedro",
            "animal_type": "dog",
            "case_status": "new",
        }
        defaults.update(kwargs)
        return AnimalBiteCase.objects.create(**defaults)

    def _payload(self):
        response = self.client.get(reverse("dashboard:index"))
        self.assertEqual(response.status_code, 200)
        return response.context["chart_data"]

    def test_admin_dashboard_renders_all_six_charts_directly(self):
        self._case("ABC-00001")
        response = self.client.get(reverse("dashboard:index"))
        self.assertTemplateUsed(response, "dashboard/admin_index.html")
        for canvas in (
            "chart-barangay", "chart-animal", "chart-monthly",
            "chart-treatment", "chart-inventory", "chart-doses",
        ):
            self.assertContains(response, f'id="{canvas}"')
        self.assertContains(response, 'id="dashboard-chart-data"')
        self.assertContains(response, "vendor/chart.umd.min.js")
        for title in (
            "Cases by Barangay", "Cases by Animal Type", "Monthly Bite Cases",
            "Treatment Status", "Inventory Stock Levels", "Vaccination Dose Status",
        ):
            self.assertContains(response, title)
        self.assertNotContains(response, "View Report")
        self.assertNotContains(response, "Live & audited")
        self.assertNotContains(response, "View reports →")

    def test_six_statistics_cards_use_saved_counts(self):
        self._case("ABC-00001", case_status="under_treatment")
        self._case("ABC-00002", case_status="completed")
        self._case("ABC-00003")
        schedule = VaccinationSchedule.objects.create(bite_case_id=AnimalBiteCase.objects.get(case_number="ABC-00001").pk)
        VaccineDose.objects.create(
            schedule=schedule, dose_label="1st Dose",
            dose_status="scheduled", scheduled_date=self.today + timedelta(days=2))
        VaccineDose.objects.create(
            schedule=schedule, dose_label="2nd Dose",
            dose_status="missed", scheduled_date=self.today - timedelta(days=2))
        response = self.client.get(reverse("dashboard:index"))
        for label in (
            "Total Cases", "New Today", "Active Cases",
            "Completed", "Upcoming Doses", "Missed Doses",
        ):
            self.assertContains(response, label)
        self.assertEqual(response.context["total_cases"], 3)
        self.assertEqual(response.context["active_cases"], 2)
        self.assertEqual(response.context["completed_cases"], 1)
        self.assertEqual(response.context["upcoming_doses"], 1)
        self.assertEqual(response.context["missed_doses"], 1)

    def test_barangay_and_animal_payloads_match_the_database_and_reports(self):
        self._case("ABC-00001", incident_barangay="San Pedro", animal_type="dog")
        self._case("ABC-00002", incident_barangay="san pedro", animal_type="dog")
        self._case("ABC-00003", incident_barangay="Poblacion", animal_type="cat")
        payload = self._payload()
        cases_qs = AnimalBiteCase.objects.all()
        barangay = barangay_distribution(cases_qs)
        animal = animal_type_distribution(cases_qs)
        self.assertEqual(payload["barangay"]["labels"], [r["name"] for r in barangay["rows"]])
        self.assertEqual(payload["barangay"]["counts"], [2, 1])
        self.assertEqual(payload["barangay"]["total"], 3)
        self.assertEqual(payload["animal"]["labels"], [r["name"] for r in animal["rows"]])
        self.assertEqual(payload["animal"]["counts"], [r["count"] for r in animal["rows"]])
        self.assertEqual(payload["animal"]["percents"], [r["pct"] for r in animal["rows"]])
        self.assertEqual(payload["animal"]["total"], 3)

    def test_monthly_line_buckets_by_incident_month_and_zero_fills(self):
        two_ago = _months_ago(self.today, 2)
        self._case("ABC-00001", date_time_bite=timezone.make_aware(
            datetime.combine(two_ago, time.min)))
        self._case("ABC-00002")
        payload = self._payload()["monthly"]
        self.assertEqual(len(payload["labels"]), 6)
        self.assertEqual(len(payload["counts"]), 6)
        self.assertEqual(payload["labels"][-1], self.today.strftime("%b %Y"))
        self.assertEqual(payload["labels"][3], two_ago.strftime("%b %Y"))
        self.assertEqual(payload["counts"][3], 1)
        self.assertEqual(payload["counts"][-1], 1)
        self.assertEqual(sum(payload["counts"]), 2)
        self.assertIn(0, payload["counts"])

    def test_treatment_donut_is_workflow_aware_like_the_reports(self):
        done_case = self._case("ABC-00001", case_status="under_treatment")
        self._case("ABC-00002", case_status="new")
        schedule = VaccinationSchedule.objects.create(bite_case=done_case)
        VaccineDose.objects.create(
            schedule=schedule, dose_label="1st Dose",
            dose_status="completed", actual_date=self.today)
        VaccineDose.objects.create(
            schedule=schedule, dose_label="2nd Dose",
            dose_status="completed", actual_date=self.today)
        treatment = self._payload()["treatment"]
        self.assertEqual(treatment["total"], 2)
        self.assertEqual(dict(zip(treatment["labels"], treatment["counts"])), {
            "New Case": 1, "Completed": 1,
        })

    def test_inventory_excludes_expired_batches_and_keeps_categories(self):
        future = self.today + timedelta(days=180)
        past = self.today - timedelta(days=1)
        VaccineInventory.objects.create(
            vaccine_name="Anti-Rabies Vaccine", brand="Verorab", batch_number="B1",
            expiration_date=future, quantity_available=10, reorder_level=5)
        VaccineInventory.objects.create(
            vaccine_name="Anti-Rabies Vaccine", brand="Verorab", batch_number="B0",
            expiration_date=past, quantity_available=99, reorder_level=5)
        VaccineInventory.objects.create(
            vaccine_name="RIG (Rabies Immunoglobulin)", brand="Imogam", batch_number="R1",
            expiration_date=future, quantity_available=3, reorder_level=5)
        VaccineInventory.objects.create(
            vaccine_name="Tetanus Toxoid", brand="TT", batch_number="T1",
            expiration_date=future, quantity_available=20, reorder_level=5)
        MedicineInventory.objects.create(
            medicine_name="Amoxicillin", strength="500 mg", dosage_form="capsule",
            unit="capsule", batch_number="M1", expiration_date=future,
            quantity_available=40, reorder_level=10)
        rows = self._payload()["inventory"]["rows"]
        by_name = {r["name"]: r for r in rows}
        vaccine = by_name["Anti-Rabies Vaccine Verorab"]
        self.assertEqual(vaccine["quantity"], 10)
        self.assertEqual(vaccine["unit"], "vials")
        self.assertEqual(vaccine["category"], "Vaccine")
        self.assertEqual(vaccine["status"], "Available")
        self.assertEqual(by_name["RIG (Rabies Immunoglobulin) Imogam"]["category"], "RIG")
        self.assertEqual(by_name["RIG (Rabies Immunoglobulin) Imogam"]["status"], "Low Stock")
        self.assertEqual(by_name["RIG (Rabies Immunoglobulin) Imogam"]["threshold"], 5)
        self.assertEqual(by_name["Tetanus Toxoid TT"]["category"], "Biologic")
        medicine = by_name["Amoxicillin 500 mg"]
        self.assertEqual(medicine["category"], "Medicine")
        self.assertEqual(medicine["unit"], "capsule")
        self.assertEqual(medicine["quantity"], 40)

    def test_dose_chart_buckets_statuses_and_separates_pending(self):
        case = self._case("ABC-00001")
        schedule = VaccinationSchedule.objects.create(bite_case=case)
        last_month = _months_ago(self.today, 1)
        VaccineDose.objects.create(
            schedule=schedule, dose_label="1st Dose", dose_status="completed",
            scheduled_date=last_month, actual_date=self.today)
        VaccineDose.objects.create(
            schedule=schedule, dose_label="2nd Dose", dose_status="scheduled",
            scheduled_date=self.today + timedelta(days=3))
        VaccineDose.objects.create(
            schedule=schedule, dose_label="3rd Dose", dose_status="missed",
            scheduled_date=last_month)
        VaccineDose.objects.create(
            schedule=schedule, dose_label="4th Dose", dose_status="pending")
        VaccineDose.objects.create(
            schedule=schedule, dose_label="5th Dose", dose_status="cancelled",
            scheduled_date=self.today)
        VaccineDose.objects.create(
            schedule=schedule, dose_label="6th Dose", dose_status="rescheduled",
            scheduled_date=self.today)
        doses = self._payload()["doses"]
        series = {s["label"]: s["data"] for s in doses["series"]}
        self.assertEqual(doses["labels"][-1], "Pending")
        self.assertEqual(series["Completed"][-2], 1)
        self.assertEqual(series["Scheduled"][-2], 1)
        self.assertEqual(series["Missed"][-3], 1)
        self.assertEqual(series["Pending Scheduling"][-1], 1)
        self.assertEqual(series["Pending Scheduling"][-2], 0)
        self.assertEqual(sum(series["Completed"]), 1)
        self.assertEqual(sum(series["Scheduled"]), 1)
        self.assertEqual(sum(series["Missed"]), 1)

    def test_empty_database_shows_empty_states_and_zero_totals(self):
        response = self.client.get(reverse("dashboard:index"))
        payload = response.context["chart_data"]
        self.assertEqual(payload["barangay"]["total"], 0)
        self.assertEqual(payload["animal"]["total"], 0)
        self.assertEqual(payload["treatment"]["total"], 0)
        self.assertEqual(payload["inventory"]["rows"], [])
        self.assertContains(response, "No bite cases recorded yet.")
        self.assertContains(response, "No inventory products recorded yet.")
        self.assertEqual(response.context["total_cases"], 0)

    def test_recent_cases_section_is_preserved(self):
        self._case("ABC-00001")
        response = self.client.get(reverse("dashboard:index"))
        self.assertContains(response, "Recent Cases")
        self.assertContains(response, "ABC-00001")
        self.assertContains(response, reverse("bite_cases:case_list"))

    def test_encoder_keeps_the_legacy_dashboard(self):
        encoder = self.User.objects.create_user(
            "encoder.charts", password="pw12345678", role="encoder")
        self.client.force_login(encoder)
        response = self.client.get(reverse("dashboard:index"))
        self.assertTemplateUsed(response, "dashboard/index.html")
        self.assertNotContains(response, 'id="chart-barangay"')
        self.assertNotContains(response, 'id="dashboard-chart-data"')

    def test_doctor_and_nurse_still_redirect_to_their_portals(self):
        doctor = self.User.objects.create_user(
            "doctor.charts", password="pw12345678", role="doctor")
        nurse = self.User.objects.create_user(
            "nurse.charts", password="pw12345678", role="nurse")
        self.client.force_login(doctor)
        self.assertRedirects(self.client.get(reverse("dashboard:index")),
                             reverse("doctor:dashboard"), fetch_redirect_response=False)
        self.client.force_login(nurse)
        self.assertRedirects(self.client.get(reverse("dashboard:index")),
                             reverse("nurse:dashboard"), fetch_redirect_response=False)