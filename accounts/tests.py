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
