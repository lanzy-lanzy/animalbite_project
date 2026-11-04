"""Tests for admin-only bite case deletion.

A case with clinical or treatment history must be archived so assessments,
schedules, doses and prescriptions survive; only empty cases may be deleted.
"""
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from doctor.models import ClinicalAssessment
from patients.models import Patient
from vaccination.models import VaccinationSchedule, VaccineDose

from .models import AnimalBiteCase

U = get_user_model()


class BiteCaseDeleteTestCase(TestCase):

    def setUp(self):
        self.admin = U.objects.create_user("admin.cases", password="pw12345678", role="admin")
        self.doctor = U.objects.create_user("doc.cases", password="pw12345678", role="doctor")
        self.nurse = U.objects.create_user("nurse.cases", password="pw12345678", role="nurse")
        self.patient = self._make_patient("PAT-QACASE-01", "Casey")
        self.other_patient = self._make_patient("PAT-QACASE-02", "Other")
        self.case = self._make_case("ABC-QACASE-01", self.patient)
        self.other_case = self._make_case("ABC-QACASE-02", self.other_patient)

    def _make_patient(self, number, first_name):
        return Patient.objects.create(
            patient_number=number, first_name=first_name, last_name="Fixture",
            birthdate=datetime.date(1990, 1, 1), contact_number="09170000000",
            sex="female",
        )

    def _make_case(self, number, patient, **overrides):
        data = {
            "case_number": number,
            "patient": patient,
            "created_by": self.nurse,
            "animal_type": "dog",
            "animal_ownership": "owned",
            "animal_vax_status": "unknown",
            "animal_condition": "under_observation",
            "exposure_type": "bite",
            "body_part_affected": "right hand",
            "number_of_wounds": 1,
            "date_time_bite": timezone.make_aware(datetime.datetime(2026, 10, 30, 9, 0)),
            "date_time_consultation": timezone.make_aware(datetime.datetime(2026, 10, 30, 10, 0)),
        }
        data.update(overrides)
        return AnimalBiteCase.objects.create(**data)

    def _delete(self, user=None, case=None, follow=True, **post):
        self.client.force_login(user or self.admin)
        return self.client.post(
            reverse("bite_cases:case_delete", args=[(case or self.case).pk]),
            post, follow=follow,
        )

    def _messages(self, response):
        return [str(message) for message in response.context["messages"]]


class BiteCaseDeletePermissionTests(BiteCaseDeleteTestCase):

    def test_only_admin_sees_the_delete_button_and_modal(self):
        url = reverse("bite_cases:case_list")
        delete_url = reverse("bite_cases:case_delete", args=[self.case.pk])
        for user, visible in ((self.admin, True), (self.doctor, False), (self.nurse, False)):
            self.client.force_login(user)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            html = response.content.decode()
            self.assertEqual(delete_url in html, visible, user.role)
            self.assertEqual("Delete Bite Case Record?" in html, visible, user.role)

    def test_non_admin_cannot_delete(self):
        for user in (self.doctor, self.nurse):
            response = self._delete(user=user)
            self.assertRedirects(response, reverse("bite_cases:case_list"))
            self.case.refresh_from_db()
            self.assertEqual(self.case.case_status, "new")
            self.assertFalse(AuditLog.objects.filter(action__endswith="_BITE_CASE").exists())

    def test_get_request_is_rejected(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("bite_cases:case_delete", args=[self.case.pk]))
        self.assertEqual(response.status_code, 405)
        self.assertTrue(AnimalBiteCase.objects.filter(pk=self.case.pk).exists())

    def test_login_required(self):
        response = self.client.post(
            reverse("bite_cases:case_delete", args=[self.case.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])
        self.assertTrue(AnimalBiteCase.objects.filter(pk=self.case.pk).exists())


class BiteCaseDeleteBehaviourTests(BiteCaseDeleteTestCase):

    def test_case_without_clinical_records_is_deleted_permanently(self):
        response = self._delete(**{"reason": "Duplicate entry"})
        self.assertRedirects(response, reverse("bite_cases:case_list"))
        self.assertFalse(AnimalBiteCase.objects.filter(pk=self.case.pk).exists())

        log = AuditLog.objects.get(action="DELETE_BITE_CASE")
        self.assertEqual(log.user, self.admin)
        self.assertIsNotNone(log.created_at)
        self.assertIn(self.case.case_number, log.description)
        self.assertIn("Duplicate entry", log.description)
        self.assertTrue(any("permanently deleted" in m for m in self._messages(response)))

    def test_patient_and_other_cases_survive_permanent_deletion(self):
        self._delete()
        self.assertTrue(Patient.objects.filter(pk=self.patient.pk).exists())
        self.assertTrue(AnimalBiteCase.objects.filter(pk=self.other_case.pk).exists())

    def test_case_with_clinical_assessment_is_archived(self):
        assessment = ClinicalAssessment.objects.create(
            patient=self.patient, bite_case=self.case, assessed_by=self.doctor,
            chief_complaint="QA fixture", category_confirmed="category_ii", status="draft",
        )
        response = self._delete(**{"reason": "Erroneous duplicate case"})
        self.assertRedirects(response, reverse("bite_cases:case_list"))

        self.case.refresh_from_db()
        self.assertEqual(self.case.case_status, "archived")
        self.assertTrue(ClinicalAssessment.objects.filter(pk=assessment.pk).exists())

        log = AuditLog.objects.get(action="ARCHIVE_BITE_CASE")
        self.assertEqual(log.user, self.admin)
        self.assertIn(self.case.case_number, log.description)
        self.assertIn("clinical assessment", log.description)
        self.assertIn("Erroneous duplicate case", log.description)
        self.assertTrue(any("archived" in m for m in self._messages(response)))

    def test_vaccination_history_is_preserved_when_archiving(self):
        schedule = VaccinationSchedule.objects.create(
            bite_case=self.case, created_by=self.nurse)
        dose = VaccineDose.objects.create(
            schedule=schedule, dose_label="Day 0",
            scheduled_date=datetime.date(2026, 10, 30),
            actual_date=datetime.date(2026, 10, 30), dose_status="completed",
        )

        self._delete()

        self.case.refresh_from_db()
        self.assertEqual(self.case.case_status, "archived")
        self.assertTrue(VaccinationSchedule.objects.filter(pk=schedule.pk).exists())
        self.assertTrue(VaccineDose.objects.filter(pk=dose.pk).exists())
        log = AuditLog.objects.get(action="ARCHIVE_BITE_CASE")
        self.assertIn("vaccination schedule", log.description)
        self.assertIn("administered vaccine doses", log.description)

    def test_archived_case_leaves_the_active_list_and_admin_can_filter_it(self):
        ClinicalAssessment.objects.create(
            patient=self.patient, bite_case=self.case, assessed_by=self.doctor,
            chief_complaint="QA fixture", category_confirmed="category_ii", status="draft",
        )
        self._delete()

        self.client.force_login(self.admin)
        active = self.client.get(reverse("bite_cases:case_list"))
        self.assertNotContains(active, self.case.case_number)
        self.assertContains(active, self.other_case.case_number)

        archived = self.client.get(reverse("bite_cases:case_list"), {"status": "archived"})
        self.assertContains(archived, self.case.case_number)
        self.assertNotContains(archived, self.other_case.case_number)
        self.assertNotContains(
            archived, reverse("bite_cases:case_delete", args=[self.case.pk]))

    def test_non_admin_cannot_list_or_open_archived_cases(self):
        ClinicalAssessment.objects.create(
            patient=self.patient, bite_case=self.case, assessed_by=self.doctor,
            chief_complaint="QA fixture", category_confirmed="category_ii", status="draft",
        )
        self._delete()

        self.client.force_login(self.doctor)
        listing = self.client.get(reverse("bite_cases:case_list"), {"status": "archived"})
        self.assertNotContains(listing, self.case.case_number)
        detail = self.client.get(
            reverse("bite_cases:case_detail", args=[self.case.pk]), follow=True)
        self.assertRedirects(detail, reverse("bite_cases:case_list"))
        self.assertTrue(any("archived" in m for m in self._messages(detail)))

        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.get(reverse("bite_cases:case_detail", args=[self.case.pk])).status_code,
            200,
        )

    def test_archived_case_cannot_be_edited(self):
        ClinicalAssessment.objects.create(
            patient=self.patient, bite_case=self.case, assessed_by=self.doctor,
            chief_complaint="QA fixture", category_confirmed="category_ii", status="draft",
        )
        self._delete()
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("bite_cases:case_edit", args=[self.case.pk]), follow=True)
        self.assertRedirects(
            response, reverse("bite_cases:case_detail", args=[self.case.pk]))
        self.assertTrue(any("cannot be edited" in m for m in self._messages(response)))
        detail = self.client.get(reverse("bite_cases:case_detail", args=[self.case.pk]))
        self.assertNotContains(
            detail, reverse("bite_cases:case_edit", args=[self.case.pk]))

    def test_already_archived_case_is_not_touched_twice(self):
        self.case.case_status = "archived"
        self.case.save(update_fields=["case_status", "updated_at"])
        response = self._delete()
        self.assertRedirects(response, reverse("bite_cases:case_list"))
        self.assertFalse(AuditLog.objects.filter(action__endswith="_BITE_CASE").exists())
        self.assertTrue(any("already archived" in m for m in self._messages(response)))


class BiteCasePortalAccessVisibilityTests(BiteCaseDeleteTestCase):
    """Portal credentials on the case page are administrator-only. Nurses and
    doctors must receive neither account details nor management actions."""

    def setUp(self):
        super().setUp()
        self.portal = U.objects.create_user(
            "casey.portal", password="pw12345678", role="patient",
            email="casey@example.com")
        self.patient.account = self.portal
        self.patient.save(update_fields=["account"])
        self.reset_url = reverse(
            "patients:patient_reset_password", args=[self.patient.pk])

    def _detail(self, user):
        self.client.force_login(user)
        return self.client.get(
            reverse("bite_cases:case_detail", args=[self.case.pk]))

    def test_admin_sees_portal_details_and_actions(self):
        content = self._detail(self.admin).content.decode()
        self.assertIn(f"Patient Portal: {self.patient.full_name()}", content)
        self.assertIn(self.portal.username, content)
        self.assertIn(self.reset_url, content)

    def test_nurse_and_doctor_do_not_see_portal_details_or_actions(self):
        for user in (self.nurse, self.doctor):
            with self.subTest(role=user.role):
                content = self._detail(user).content.decode()
                self.assertNotIn(self.portal.username, content)
                self.assertNotIn(self.portal.email, content)
                self.assertNotIn(self.reset_url, content)
                self.assertNotIn("Patient Portal:", content)
                self.assertNotIn("No portal login yet", content)

    def test_nurse_one_time_credentials_are_not_rendered_or_retained(self):
        self.client.force_login(self.nurse)
        session = self.client.session
        session[f"temp_creds_{self.patient.pk}"] = {
            "username": "secret.portal", "password": "secret-password",
        }
        session.save()

        content = self.client.get(
            reverse("bite_cases:case_detail", args=[self.case.pk])).content.decode()
        self.assertNotIn("secret.portal", content)
        self.assertNotIn("secret-password", content)
        self.assertNotIn(f"temp_creds_{self.patient.pk}", self.client.session)


class BiteCaseDeleteModalTests(BiteCaseDeleteTestCase):

    def test_modal_copy_and_case_fields_are_rendered(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("bite_cases:case_list"))
        html = response.content.decode()
        self.assertIn("Delete Bite Case Record?", html)
        self.assertIn(
            "Are you sure you want to delete this animal bite case record? "
            "Please confirm before continuing.",
            html,
        )
        for label in ("Case Number", "Patient Name", "Animal Type", "Date of Incident"):
            self.assertIn(label, html)
        self.assertIn("Cancel", html)
        self.assertIn("Confirm Delete", html)
        self.assertIn("csrfmiddlewaretoken", html)
