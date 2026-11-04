from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("patients", "0003_add_vitalsign"),
    ]

    operations = [
        migrations.AddField(
            model_name="patient",
            name="profile_image",
            field=models.ImageField(
                blank=True, null=True, upload_to="patients/profile/"
            ),
        ),
    ]
