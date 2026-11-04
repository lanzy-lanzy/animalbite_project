from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pre_registrations", "0004_preregistration_rejected_at_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="preregistration",
            name="medical_history",
            field=models.TextField(default=""),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="preregistration",
            name="allergy_info",
            field=models.TextField(default=""),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="preregistration",
            name="previous_rabies_vax",
            field=models.TextField(default=""),
            preserve_default=False,
        ),
    ]
