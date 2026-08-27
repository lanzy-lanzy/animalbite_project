import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from django.db import transaction


class Command(BaseCommand):
    help = "Create the production administrator from temporary Render environment variables."

    @transaction.atomic
    def handle(self, *args, **options):
        username = os.environ.get("RENDER_ADMIN_USERNAME", "").strip()
        email = os.environ.get("RENDER_ADMIN_EMAIL", "").strip()
        password = os.environ.get("RENDER_ADMIN_PASSWORD", "")

        if not username or not password:
            self.stdout.write("Render administrator credentials not configured; skipping.")
            return

        User = get_user_model()
        user = User.objects.filter(username=username).first()

        if user:
            changed_fields = []
            required_values = {
                "role": "admin",
                "is_active": True,
                "is_staff": True,
                "is_superuser": True,
            }
            if email:
                required_values["email"] = email

            for field, value in required_values.items():
                if getattr(user, field) != value:
                    setattr(user, field, value)
                    changed_fields.append(field)

            if changed_fields:
                user.save(update_fields=changed_fields)

            self.stdout.write(self.style.SUCCESS("Existing administrator privileges verified."))
            return

        user = User(username=username, email=email, role="admin")
        try:
            validate_password(password, user=user)
        except ValidationError as exc:
            raise CommandError("Administrator password failed Django validation.") from exc

        User.objects.create_superuser(
            username=username,
            email=email,
            password=password,
            role="admin",
        )
        self.stdout.write(self.style.SUCCESS("Production administrator created."))
