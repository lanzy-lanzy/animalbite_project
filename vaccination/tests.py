"""
Tests for the nurse-triggered vaccination schedule workflow.

Schedules are deliberately NOT created automatically when a bite case is
created or converted. A nurse (or admin) triggers them explicitly once the
case has been assessed.
"""
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from audit.models import AuditLog
from bite_cases.models import AnimalBiteCase
from patients.models import Patient
from settings_app.models import SystemSetting

from .models import VaccinationSchedule, VaccineDose
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
        self.assertEqual(VaccineDose.objects.filter(schedule=sched).count(), 5)
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
        self.assertEqual(schedule_base_date(case), datetime.date.today())

        case.date_time_consultation = datetime.datetime(2026, 9, 30, 10, 0)
        case.save()
        self.assertEqual(schedule_base_date(case), datetime.date(2026, 9, 30))

    def test_ensure_schedule_is_idempotent_and_honours_settings(self):
        SystemSetting.objects.all().delete()
        SystemSetting.objects.create(key="vaccination_days", value="0,7,21")
        case = self._case()
        case.date_time_consultation = datetime.datetime(2026, 9, 30, 10, 0)
        case.save()

        sched, created = ensure_vaccination_schedule(case, created_by=self.nurse)
        self.assertTrue(created)
        self.assertEqual(VaccineDose.objects.filter(schedule=sched).count(), 3)
        self.assertEqual(
            sorted(VaccineDose.objects.filter(schedule=sched).values_list("scheduled_date", flat=True)),
            [datetime.date(2026, 9, 30), datetime.date(2026, 10, 7), datetime.date(2026, 10, 21)],
        )

        sched2, created2 = ensure_vaccination_schedule(case, created_by=self.nurse)
        self.assertFalse(created2)
        self.assertEqual(sched2.pk, sched.pk)
        self.assertEqual(VaccineDose.objects.filter(schedule=sched).count(), 3)


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
