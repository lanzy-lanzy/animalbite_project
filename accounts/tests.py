import os
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase


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
