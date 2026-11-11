import os
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse


class LoginPageTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.user = self.User.objects.create_user(
            "clinic.encoder", password="Gate-Pass-2026!", role="encoder",
            first_name="Clinic", last_name="Encoder")

    def test_page_offers_the_login_elements(self):
        response = self.client.get(reverse("accounts:login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Welcome Back!")
        self.assertContains(response, 'name="username"')
        self.assertContains(response, 'name="password"')
        self.assertContains(response, 'id="pw-toggle"')
        self.assertContains(response, "Forgot Password?")
        self.assertContains(response, "Register Online")
        self.assertContains(response, reverse("pre_registrations:public_create"))
        self.assertContains(response, 'class="back-home"')
        self.assertContains(response, 'href="/"')
        self.assertContains(response, "Back to Home")

    def test_invalid_credentials_shows_the_error_and_stays_on_login(self):
        response = self.client.post(
            reverse("accounts:login"),
            {"username": "clinic.encoder", "password": "wrong"},
            follow=True)
        self.assertContains(response, "Invalid username or password.")
        self.assertEqual(response.request["PATH_INFO"], reverse("accounts:login"))

    def test_valid_credentials_sign_the_user_in(self):
        response = self.client.post(
            reverse("accounts:login"),
            {"username": "clinic.encoder", "password": "Gate-Pass-2026!"})
        self.assertRedirects(response, reverse("dashboard:index"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)


class CreateRenderAdminCommandTests(TestCase):
    def setUp(self):
        self.User = get_user_model()

    def test_skips_when_credentials_are_not_configured(self):
        output = StringIO()

        with patch.dict(os.environ, {}, clear=True):
            call_command("create_render_admin", stdout=output)

        self.assertFalse(self.User.objects.exists())
        self.assertIn("not configured", output.getvalue())

    def test_creates_admin_with_application_and_django_privileges(self):
        credentials = {
            "RENDER_ADMIN_USERNAME": "clinic-admin",
            "RENDER_ADMIN_EMAIL": "admin@example.com",
            "RENDER_ADMIN_PASSWORD": "Kite-Bamboo-Clinic-2026!",
        }

        with patch.dict(os.environ, credentials, clear=True):
            call_command("create_render_admin", stdout=StringIO())

        user = self.User.objects.get(username="clinic-admin")
        self.assertEqual(user.email, "admin@example.com")
        self.assertEqual(user.role, "admin")
        self.assertTrue(user.is_active)
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.check_password(credentials["RENDER_ADMIN_PASSWORD"]))

    def test_existing_admin_is_not_reset_on_later_deploys(self):
        user = self.User.objects.create_user(
            username="clinic-admin",
            password="Original-Clinic-Password-2026!",
            role="encoder",
        )
        credentials = {
            "RENDER_ADMIN_USERNAME": "clinic-admin",
            "RENDER_ADMIN_EMAIL": "admin@example.com",
            "RENDER_ADMIN_PASSWORD": "Different-Password-That-Is-Not-Applied!",
        }

        with patch.dict(os.environ, credentials, clear=True):
            call_command("create_render_admin", stdout=StringIO())

        user.refresh_from_db()
        self.assertEqual(user.role, "admin")
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.check_password("Original-Clinic-Password-2026!"))

    def test_rejects_a_weak_password(self):
        credentials = {
            "RENDER_ADMIN_USERNAME": "admin",
            "RENDER_ADMIN_PASSWORD": "admin123",
        }

        with patch.dict(os.environ, credentials, clear=True):
            with self.assertRaisesMessage(CommandError, "failed Django validation"):
                call_command("create_render_admin", stdout=StringIO())

        self.assertFalse(self.User.objects.exists())


class AdminSidebarTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.admin = self.User.objects.create_user(
            "admin.nav", password="pw12345678", role="admin")

    def _nav(self):
        self.client.force_login(self.admin)
        html = self.client.get(reverse("dashboard:index")).content.decode()
        start = html.index("<nav")
        return html[start:html.index("</nav>", start)]

    def test_inventory_section_hides_dispense_prescriptions(self):
        nav = self._nav()
        self.assertIn("Vaccine Inventory", nav)
        self.assertIn("Medicine Inventory", nav)
        self.assertNotIn("Dispense Prescriptions", nav)
        self.assertNotIn(reverse("inventory:prescription_list"), nav)
        self.assertLess(nav.index("Vaccine Inventory"), nav.index("Medicine Inventory"))

    def test_inventory_is_a_collapsible_parent_menu(self):
        self.client.force_login(self.admin)
        html = self.client.get(reverse("dashboard:index")).content.decode()
        nav_start = html.index("<nav")
        nav = html[nav_start:html.index("</nav>", nav_start)]
        self.assertIn('class="sidebar-group"', nav)
        self.assertIn('@click="inventoryOpen = !inventoryOpen"', nav)
        self.assertIn(':aria-expanded="inventoryOpen.toString()"', nav)
        self.assertIn('aria-controls="admin-inventory-submenu"', nav)
        self.assertIn('x-show="inventoryOpen"', nav)
        self.assertIn("sidebar-chevron", nav)
        self.assertIn(
            ".sidebar-shell--admin nav .sidebar-link.active {",
            html,
        )
        self.assertIn("background: #0D382C;", html)

    def test_inventory_group_stays_open_and_highlights_the_current_page(self):
        for route_name, active_route, inactive_route in (
            ("inventory:vaccine_list", "inventory:vaccine_list", "inventory:medicine_list"),
            ("inventory:medicine_list", "inventory:medicine_list", "inventory:vaccine_list"),
        ):
            with self.subTest(page=route_name):
                self.client.force_login(self.admin)
                html = self.client.get(reverse(route_name)).content.decode()
                nav_start = html.index("<nav")
                nav = html[nav_start:html.index("</nav>", nav_start)]
                self.assertIn('x-data="{ inventoryOpen: true }"', nav)
                self.assertIn(
                    f'href="{reverse(active_route)}" class="sidebar-link active"', nav
                )
                self.assertIn(
                    f'href="{reverse(inactive_route)}" class="sidebar-link "', nav
                )

    LABELS = [
        "Dashboard",
        "Register Patient",
        "Online Registration",
        "Patients",
        "Bite Cases",
        "Vaccination Records",
        "SMS Reminders",
        "Inventory",
        "Vaccine Inventory",
        "Medicine Inventory",
        "Reports",
        "Users",
        "Settings",
        "Audit Trail",
    ]

    ROUTES = {
        "Dashboard": "dashboard:index",
        "Register Patient": "patients:patient_create",
        "Online Registration": "pre_registrations:staff_list",
        "Patients": "patients:patient_list",
        "Bite Cases": "bite_cases:case_list",
        "Vaccination Records": "vaccination:patient_schedule_list",
        "SMS Reminders": "vaccination:sms_log_list",
        "Vaccine Inventory": "inventory:vaccine_list",
        "Medicine Inventory": "inventory:medicine_list",
        "Reports": "reports:reports_index",
        "Users": "accounts:user_list",
        "Settings": "settings_app:settings_list",
        "Audit Trail": "audit:audit_list",
    }

    def test_links_appear_in_the_required_order(self):
        nav = self._nav()
        positions = [nav.index(label) for label in self.LABELS]
        self.assertEqual(positions, sorted(positions))

    def test_links_point_at_the_existing_routes(self):
        nav = self._nav()
        for label, route in self.ROUTES.items():
            self.assertIn(f'href="{reverse(route)}"', nav, label)

    def test_category_headings_are_removed(self):
        nav = self._nav()
        for heading in ("Patient Management", "Case Management", "Administration"):
            self.assertNotIn(heading, nav)
        self.assertNotIn("text-gold-700 uppercase tracking-widest", nav)

    def test_logout_stays_below_the_navigation(self):
        self.client.force_login(self.admin)
        html = self.client.get(reverse("dashboard:index")).content.decode()
        self.assertGreater(html.index("Logout"), html.index("</nav>"))

    def test_every_admin_link_opens(self):
        self.client.force_login(self.admin)
        for route in self.ROUTES.values():
            with self.subTest(url=route):
                response = self.client.get(reverse(route))
                self.assertEqual(response.status_code, 200)

    def test_active_state_follows_the_open_page(self):
        self.client.force_login(self.admin)
        for label, route in self.ROUTES.items():
            if label == "Dashboard":
                continue
            with self.subTest(page=route):
                html = self.client.get(reverse(route)).content.decode()
                start = html.index("<nav")
                nav = html[start:html.index("</nav>", start)]
                self.assertIn(
                    f'href="{reverse(route)}" class="sidebar-link active"', nav, label)
                active_count = nav.count('class="sidebar-link active"')
                self.assertEqual(active_count, 1, label)

    def test_active_state_on_detail_pages_highlights_the_parent(self):
        from datetime import date

        from django.utils import timezone

        from bite_cases.models import AnimalBiteCase
        from patients.models import Patient
        from pre_registrations.models import PreRegistration

        patient = Patient.objects.create(
            patient_number="PAT-NAV01", first_name="Nena", last_name="Nav",
            birthdate=date(1990, 1, 1), sex="female",
            contact_number="09175550001", address="Poblacion",
        )
        case = AnimalBiteCase.objects.create(
            case_number="ABC-NAV01", patient=patient, created_by=self.admin,
            animal_type="dog", exposure_type="bite",
            date_time_bite=timezone.now(),
        )
        registration = PreRegistration.objects.create(
            first_name="Nena", last_name="Nav", contact_number="09175550001",
            birthdate=date(1990, 1, 1), bite_datetime=timezone.now(),
        )
        self.client.force_login(self.admin)
        for route, label in (
            (reverse("patients:patient_detail", args=[patient.pk]), "Patients"),
            (reverse("bite_cases:case_detail", args=[case.pk]), "Bite Cases"),
            (reverse("pre_registrations:staff_detail", args=[registration.pk]), "Online Registration"),
        ):
            with self.subTest(page=label):
                html = self.client.get(route).content.decode()
                start = html.index("<nav")
                nav = html[start:html.index("</nav>", start)]
                self.assertIn(
                    f'href="{reverse(self.ROUTES[label])}" class="sidebar-link active"',
                    nav, label)
                self.assertEqual(nav.count('class="sidebar-link active"'), 1, label)

    @staticmethod
    def _aside_tag(html):
        start = html.index("<aside")
        return html[start:html.index(">", start)]

    def test_admin_chrome_and_menu_are_admin_only(self):
        self.client.force_login(self.admin)
        html = self.client.get(reverse("dashboard:index")).content.decode()
        self.assertIn("sidebar-shell--admin", self._aside_tag(html))

        nurse = self.User.objects.create_user("nurse.nav.iso", password="pw12345678", role="nurse")
        doctor = self.User.objects.create_user("doctor.nav.iso", password="pw12345678", role="doctor")
        encoder = self.User.objects.create_user("encoder.nav.iso", password="pw12345678", role="encoder")
        patient = self.User.objects.create_user("patient.nav.iso", password="pw12345678", role="patient")
        for user, route in (
            (nurse, "nurse:dashboard"),
            (doctor, "doctor:dashboard"),
            (encoder, "dashboard:index"),
            (patient, "accounts:patient_portal"),
        ):
            with self.subTest(role=user.role):
                self.client.force_login(user)
                html = self.client.get(reverse(route)).content.decode()
                self.assertNotIn("sidebar-shell--admin", self._aside_tag(html))
                start = html.index("<nav")
                nav = html[start:html.index("</nav>", start)]
                self.assertNotIn("Audit Trail", nav)

    def test_encoder_sidebar_keeps_the_legacy_grouped_menu(self):
        encoder = self.User.objects.create_user("encoder.nav.legacy", password="pw12345678", role="encoder")
        self.client.force_login(encoder)
        html = self.client.get(reverse("dashboard:index")).content.decode()
        start = html.index("<nav")
        nav = html[start:html.index("</nav>", start)]
        self.assertIn("Patient Management", nav)
        self.assertIn("Case Management", nav)
        self.assertIn("Online Registrations", nav)
        self.assertNotIn("Register Patient", nav)
        self.assertNotIn("Users", nav)
        self.assertNotIn("Settings", nav)
        self.assertNotIn("Audit Trail", nav)


class NurseSidebarTests(TestCase):
    """The Nurse Portal sidebar shows one ordered set of nurse links and hides
    the pages that are not part of the nurse workflow."""

    LABELS = [
        "Dashboard",
        "View Patient Records",
        "View Doctor Findings",
        "Administer Vaccine",
        "SMS Reminders",
        "Inventory",
        "Vaccine Inventory",
        "Medicine Inventory",
        "Reports",
        "My Profile",
    ]

    def setUp(self):
        self.User = get_user_model()
        self.nurse = self.User.objects.create_user(
            "nurse.nav", password="pw12345678", role="nurse")

    def _page(self):
        self.client.force_login(self.nurse)
        return self.client.get(reverse("nurse:dashboard")).content.decode()

    def _nav(self, html):
        start = html.index("<nav")
        return html[start:html.index("</nav>", start)]

    def test_links_appear_in_the_required_order(self):
        nav = self._nav(self._page())
        positions = [nav.index(label) for label in self.LABELS]
        self.assertEqual(positions, sorted(positions))

    def test_links_point_at_the_existing_routes(self):
        nav = self._nav(self._page())
        expected = {
            "Dashboard": reverse("nurse:dashboard"),
            "View Patient Records": reverse("patients:patient_list"),
            "View Doctor Findings": reverse("bite_cases:case_list"),
            "Administer Vaccine": reverse("vaccination:patient_schedule_list"),
            "SMS Reminders": reverse("vaccination:sms_log_list"),
            "Vaccine Inventory": reverse("inventory:vaccine_list"),
            "Medicine Inventory": reverse("inventory:medicine_list"),
            "Reports": reverse("reports:reports_index"),
            "My Profile": reverse("accounts:profile"),
        }
        for label, url in expected.items():
            self.assertIn(f'href="{url}"', nav, label)

    def test_inventory_is_a_collapsible_parent_menu(self):
        nav = self._nav(self._page())
        self.assertIn('class="sidebar-link sidebar-group-toggle"', nav)
        self.assertIn('@click="inventoryOpen = !inventoryOpen"', nav)
        self.assertIn(':aria-expanded="inventoryOpen.toString()"', nav)
        self.assertIn('aria-controls="nurse-inventory-submenu"', nav)
        self.assertIn("sidebar-chevron", nav)
        submenu_start = nav.index('id="nurse-inventory-submenu"')
        self.assertGreater(submenu_start, nav.index("inventoryOpen"))
        self.assertGreater(nav.index(reverse("inventory:vaccine_list")), submenu_start)
        self.assertLess(
            nav.index(reverse("inventory:vaccine_list")),
            nav.index(reverse("inventory:medicine_list")),
        )
        self.assertLess(nav.index(reverse("inventory:medicine_list")), nav.index("Reports"))

    def test_inventory_group_is_collapsed_outside_inventory_pages(self):
        self.assertIn('x-data="{ inventoryOpen: false }"', self._nav(self._page()))

    def test_inventory_group_stays_open_and_highlights_the_current_page(self):
        for name, active, inactive in (
            ("inventory:vaccine_list", "inventory:vaccine_list", "inventory:medicine_list"),
            ("inventory:medicine_list", "inventory:medicine_list", "inventory:vaccine_list"),
        ):
            with self.subTest(page=name):
                self.client.force_login(self.nurse)
                html = self.client.get(reverse(name)).content.decode()
                nav = self._nav(html)
                self.assertIn('x-data="{ inventoryOpen: true }"', nav)
                self.assertIn(
                    f'<a href="{reverse(active)}" class="sidebar-link active"', nav)
                self.assertNotIn(
                    f'<a href="{reverse(inactive)}" class="sidebar-link active"', nav)

    def test_hidden_links_are_not_in_the_sidebar(self):
        nav = self._nav(self._page())
        for label in ("Vaccination Records", "Follow-Ups", "Dispense Prescriptions"):
            self.assertNotIn(label, nav)

    def test_logout_stays_below_the_navigation(self):
        html = self._page()
        self.assertGreater(html.index("Logout"), html.index("</nav>"))

    def test_every_nurse_link_opens(self):
        self.client.force_login(self.nurse)
        for name in (
            "nurse:dashboard",
            "patients:patient_list",
            "bite_cases:case_list",
            "vaccination:patient_schedule_list",
            "vaccination:sms_log_list",
            "inventory:vaccine_list",
            "inventory:medicine_list",
            "reports:reports_index",
            "accounts:profile",
        ):
            with self.subTest(url=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)


class PatientPortalTests(TestCase):
    """The redesigned patient dashboard and its service pages show only the
    signed-in patient's real records, with patient-specific access control."""

    def setUp(self):
        from datetime import date

        from django.utils import timezone

        from bite_cases.models import AnimalBiteCase
        from patients.models import Patient
        from pre_registrations.models import PreRegistration
        from vaccination.models import VaccinationSchedule, VaccineDose

        User = get_user_model()
        self.staff = User.objects.create_user(
            "portal.staff", password="pw12345678", role="encoder")
        self.account = User.objects.create_user(
            "portal.patient", password="Patient-Pass-2026!", role="patient",
            first_name="Rico", last_name="Bautista")
        self.patient = Patient.objects.create(
            patient_number="P-00001", account=self.account,
            first_name="Rico", last_name="Bautista",
            birthdate=date(1990, 5, 15), sex="male",
            contact_number="09171234567",
            address="123 Rizal St, Caridad, Caridad",
            created_by=self.staff,
        )
        self.case = AnimalBiteCase.objects.create(
            case_number="ABC-00001", patient=self.patient,
            date_time_bite=timezone.now(),
            place_of_incident="Balay", incident_barangay="Caridad",
            animal_type="cat", animal_ownership="owned",
            animal_vax_status="not_vaccinated", animal_condition="alive",
            exposure_type="bite", body_part_affected="Arm",
            created_by=self.staff,
        )
        self.schedule = VaccinationSchedule.objects.create(
            bite_case=self.case, created_by=self.staff)
        self.completed_dose = VaccineDose.objects.create(
            schedule=self.schedule, dose_label="Day 0",
            scheduled_date=date(2026, 11, 1), actual_date=date(2026, 11, 1),
            dose_status="completed", vaccine_brand="Verorab",
            administered_by=self.staff,
        )
        self.scheduled_dose = VaccineDose.objects.create(
            schedule=self.schedule, dose_label="Day 3",
            scheduled_date=date(2026, 11, 4), dose_status="scheduled",
        )
        self.pending_dose = VaccineDose.objects.create(
            schedule=self.schedule, dose_label="Day 7",
            scheduled_date=None, dose_status="pending",
        )
        self.undated_completed_dose = VaccineDose.objects.create(
            schedule=self.schedule, dose_label="Day 28",
            scheduled_date=None, actual_date=None, dose_status="completed",
            vaccine_brand="UnconfirmedBrand",
        )
        self.registration = PreRegistration.objects.create(
            account=self.account,
            first_name="Rico", last_name="Bautista",
            birthdate=date(1990, 5, 15), sex="male",
            contact_number="09171234567", address="123 Rizal St",
            barangay="Caridad", medical_history="None", allergy_info="None",
            previous_rabies_vax="None", bite_datetime=timezone.now(),
            incident_place="Balay", incident_barangay="Caridad",
            animal_type="cat", animal_ownership="owned",
            animal_vaccination_status="not_vaccinated", animal_condition="alive",
            exposure_type="bite", body_part_affected="Arm",
            consent_given=True,
        )

        self.other_account = User.objects.create_user(
            "other.patient", password="Patient-Pass-2026!", role="patient",
            first_name="Other", last_name="Person")
        self.other_patient = Patient.objects.create(
            patient_number="P-99999", account=self.other_account,
            first_name="Other", last_name="Person",
            birthdate=date(1985, 1, 1), sex="female",
            contact_number="09189999999", address="456 Mabini St",
            created_by=self.staff,
        )
        self.other_case = AnimalBiteCase.objects.create(
            case_number="ABC-99999", patient=self.other_patient,
            date_time_bite=timezone.now(),
            place_of_incident="Elsewhere", incident_barangay="Other",
            animal_type="dog", animal_ownership="stray",
            animal_vax_status="unknown", animal_condition="missing",
            exposure_type="scratch", body_part_affected="Leg",
            created_by=self.staff,
        )
        self.other_schedule = VaccinationSchedule.objects.create(
            bite_case=self.other_case, created_by=self.staff)
        VaccineDose.objects.create(
            schedule=self.other_schedule, dose_label="Day 0",
            scheduled_date=date(2026, 10, 1), actual_date=date(2026, 10, 1),
            dose_status="completed", vaccine_brand="OtherBrand",
            administered_by=self.staff,
        )
        self.other_registration = PreRegistration.objects.create(
            account=self.other_account,
            first_name="Other", last_name="Person",
            birthdate=date(1985, 1, 1), sex="female",
            contact_number="09189999999", address="456 Mabini St",
            barangay="Other", medical_history="None", allergy_info="None",
            previous_rabies_vax="None", bite_datetime=timezone.now(),
            incident_place="Elsewhere", incident_barangay="Other",
            animal_type="dog", animal_ownership="stray",
            animal_vaccination_status="unknown", animal_condition="missing",
            exposure_type="scratch", body_part_affected="Leg",
            consent_given=True,
        )

    def _login_patient(self):
        self.client.force_login(self.account)

    def test_dashboard_shows_real_patient_information_and_quick_access(self):
        from django.utils import timezone

        self._login_patient()
        response = self.client.get(reverse("accounts:patient_portal"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "My Dashboard")
        self.assertContains(response, self.patient.full_name())
        self.assertContains(response, "P-00001")
        self.assertContains(response, "Male")
        self.assertContains(response, "May 15, 1990")
        today = timezone.localdate()
        expected_age = today.year - 1990 - ((today.month, today.day) < (5, 15))
        self.assertContains(response, f'<span class="pp-info-value">{expected_age}</span>', html=True)
        self.assertContains(response, "123 Rizal St, Caridad")
        self.assertContains(response, self.patient.contact_display)
        self.assertContains(response, "Your Portal")
        self.assertContains(response, "Quick Access")
        for modifier in ("records", "schedule", "history", "qr", "profile"):
            self.assertContains(response, f"pp-qa-card--{modifier}")
        for name in (
            "accounts:patient_bite_records",
            "accounts:patient_vaccination_schedule",
            "accounts:patient_vaccination_history",
            "accounts:patient_qr",
            "accounts:profile",
            "pre_registrations:patient_report",
        ):
            self.assertContains(response, f'href="{reverse(name)}"')
        self.assertContains(response, "Report another bite")
        self.assertContains(response, "css/patient_portal.css")
        self.assertContains(response, "sidebar-shell--patient")
        self.assertContains(response, "topbar--patient")
        self.assertNotContains(response, "Scheduled Vaccine Doses")
        self.assertNotContains(response, "My Treatment")

    def test_dashboard_sidebar_sections_and_links(self):
        self._login_patient()
        html = self.client.get(reverse("accounts:patient_portal")).content.decode()
        start = html.index("<nav")
        nav = html[start:html.index("</nav>", start)]
        labels = [
            "Patient Dashboard", "Dashboard", "Patient Services", "My Profile",
            "My Bite Records", "Vaccination Schedule", "Vaccination History",
            "My QR Code", "Report Another Bite",
        ]
        positions = [nav.index(label) for label in labels]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('href="{}" class="sidebar-link active"'.format(reverse("accounts:patient_portal")), nav)
        self.assertGreater(html.index("Logout"), html.index("</nav>"))
        for staff_label in ("Patient Records", "Bite Cases", "Inventory", "Reports", "Activity Logs"):
            self.assertNotIn(staff_label, nav)

    def test_dashboard_renders_without_a_linked_patient_record(self):
        from django.contrib.auth import get_user_model
        bare = get_user_model().objects.create_user(
            "bare.patient", password="Patient-Pass-2026!", role="patient")
        self.client.force_login(bare)
        response = self.client.get(reverse("accounts:patient_portal"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "being prepared")

    def test_staff_are_redirected_away_from_patient_pages(self):
        self.client.force_login(self.staff)
        for name in (
            "accounts:patient_portal",
            "accounts:patient_bite_records",
            "accounts:patient_vaccination_schedule",
            "accounts:patient_vaccination_history",
            "accounts:patient_qr",
        ):
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertRedirects(response, reverse("dashboard:index"), fetch_redirect_response=False)

    def test_anonymous_users_are_sent_to_login(self):
        for name in (
            "accounts:patient_portal",
            "accounts:patient_bite_records",
            "accounts:patient_vaccination_schedule",
            "accounts:patient_vaccination_history",
            "accounts:patient_qr",
        ):
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)
                self.assertIn("login", response.url)

    def test_bite_records_show_only_own_cases_and_registrations(self):
        self.registration.converted_case = self.case
        self.registration.converted_patient = self.patient
        self.registration.status = "converted"
        self.registration.save()
        self._login_patient()
        response = self.client.get(reverse("accounts:patient_bite_records"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bite Case History")
        self.assertContains(response, "My Registrations")
        self.assertContains(response, "ABC-00001")
        self.assertContains(response, "Cat")
        self.assertContains(response, "Bite")
        self.assertContains(response, self.registration.pre_registration_number)
        self.assertContains(response, reverse("pre_registrations:public_slip", args=[self.registration.pre_registration_number]))
        self.assertContains(response, "Converted to case ABC-00001")
        self.assertContains(response, f'href="#case-{self.case.pk}"')
        self.assertContains(response, reverse("vaccination:my_schedule_detail", args=[self.schedule.pk]))
        self.assertNotContains(response, "ABC-99999")
        self.assertNotContains(response, "Other Person")
        self.assertNotContains(response, self.other_registration.pre_registration_number)

    def test_schedule_uses_ordinal_labels_and_own_doses_only(self):
        self._login_patient()
        response = self.client.get(reverse("accounts:patient_vaccination_schedule"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1st Dose")
        self.assertContains(response, "2nd Dose")
        self.assertContains(response, "3rd Dose")
        self.assertContains(response, "Nov 01, 2026")
        self.assertContains(response, "Nov 04, 2026")
        self.assertContains(response, "Pending Scheduling")
        self.assertContains(response, "ABC-00001")
        self.assertNotContains(response, "Day 0")
        self.assertNotContains(response, "Day 3")
        self.assertNotContains(response, "Day 7")
        self.assertNotContains(response, "ABC-99999")
        self.assertNotContains(response, 'name="scheduled_date"')
        self.assertNotContains(response, 'name="dose_status"')

    def test_history_lists_only_confirmed_administrations(self):
        self._login_patient()
        response = self.client.get(reverse("accounts:patient_vaccination_history"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1st Dose")
        self.assertContains(response, "Nov 01, 2026")
        self.assertContains(response, "Verorab")
        self.assertContains(response, "Completed")
        self.assertContains(response, "ABC-00001")
        self.assertNotContains(response, "2nd Dose")
        self.assertNotContains(response, "3rd Dose")
        self.assertNotContains(response, "4th Dose")
        self.assertNotContains(response, "UnconfirmedBrand")
        self.assertNotContains(response, "OtherBrand")
        self.assertNotContains(response, "ABC-99999")

    def test_qr_page_uses_the_identification_payload_not_credentials(self):
        from utils.qr import qr_data_uri

        self._login_patient()
        with patch("utils.qr.qr_data_uri", wraps=qr_data_uri) as spy:
            response = self.client.get(reverse("accounts:patient_qr"))
        self.assertEqual(response.status_code, 200)
        spy.assert_called_once_with("ABTC-PATIENT:P-00001")
        self.assertContains(response, self.patient.full_name())
        self.assertContains(response, "P-00001")
        self.assertContains(response, "data:image/png;base64,")
        self.assertContains(response, 'download="ABTC-QR-P-00001.png"')
        self.assertContains(response, "Print")
        self.assertNotContains(response, "Patient-Pass-2026!")
        self.assertNotContains(response, "Temp Password")

    def test_schedule_detail_timeline_uses_ordinal_labels(self):
        self._login_patient()
        response = self.client.get(reverse("vaccination:my_schedule_detail", args=[self.schedule.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1st Dose")
        self.assertContains(response, "2nd Dose")
        self.assertNotContains(response, "Day 0")
        self.assertNotContains(response, "Day 3")
        other = self.client.get(reverse("vaccination:my_schedule_detail", args=[self.other_schedule.pk]))
        self.assertEqual(other.status_code, 404)

    def test_patient_cannot_reach_staff_pages(self):
        self._login_patient()
        for url in (
            reverse("patients:patient_list"),
            reverse("inventory:vaccine_list"),
            reverse("bite_cases:case_detail", args=[self.case.pk]),
            reverse("vaccination:schedule_detail", args=[self.schedule.pk]),
            reverse("dashboard:index"),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertRedirects(response, reverse("accounts:patient_portal"), fetch_redirect_response=False)

    def test_staff_portals_do_not_load_patient_chrome(self):
        User = get_user_model()
        doctor = User.objects.create_user("portal.doctor", password="pw12345678", role="doctor")
        nurse = User.objects.create_user("portal.nurse", password="pw12345678", role="nurse")
        admin = User.objects.create_user("portal.admin", password="pw12345678", role="admin")
        for user, home in (
            (doctor, "doctor:dashboard"),
            (nurse, "nurse:dashboard"),
            (admin, "dashboard:index"),
        ):
            with self.subTest(role=user.role):
                self.client.force_login(user)
                html = self.client.get(reverse(home)).content.decode()
                self.assertNotIn("patient_portal.css", html)
                self.assertNotIn("sidebar-shell--patient", html)
                self.assertNotIn("topbar--patient", html)
