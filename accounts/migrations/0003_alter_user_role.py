# Generated to split Nurse out of the Doctor role into its own Nurse Portal role.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_alter_user_role"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="role",
            field=models.CharField(
                choices=[
                    ("admin", "Admin"),
                    ("health_worker", "Health Worker / ABTC Staff"),
                    ("doctor", "Doctor"),
                    ("nurse", "Nurse"),
                    ("encoder", "Encoder"),
                    ("patient", "Patient"),
                ],
                default="encoder",
                max_length=20,
            ),
        ),
    ]
