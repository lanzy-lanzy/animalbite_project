from django.db import migrations


SETTING_KEY = 'inventory_expiry_warning_days'
DEFAULT_DAYS = '30'


def seed_expiry_setting(apps, schema_editor):
    SystemSetting = apps.get_model('settings_app', 'SystemSetting')
    SystemSetting.objects.get_or_create(
        key=SETTING_KEY,
        defaults={
            'value': DEFAULT_DAYS,
            'description': (
                'Days before expiration when vaccine and medicine batches '
                'are flagged as Expiring Soon in inventory alerts.'
            ),
        },
    )


def remove_expiry_setting(apps, schema_editor):
    SystemSetting = apps.get_model('settings_app', 'SystemSetting')
    SystemSetting.objects.filter(key=SETTING_KEY).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0007_vaccineinventory_reorder_level'),
        ('settings_app', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_expiry_setting, remove_expiry_setting),
    ]
