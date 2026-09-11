from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import date, timedelta
from patients.models import Barangay, Patient
from bite_cases.models import AnimalBiteCase, ExposureClassification
from vaccination.models import VaccinationSchedule, VaccineDose, FollowUpRecord
from inventory.models import VaccineInventory, SupplyInventory
from settings_app.models import SystemSetting
from audit.models import AuditLog
import random

User = get_user_model()

class Command(BaseCommand):
    help = 'Seed the database with sample data'

    def handle(self, *args, **kwargs):
        self.stdout.write('Seeding database...')

        if User.objects.filter(username='admin').exists():
            self.stdout.write('Data already seeded. Skipping.')
            return

        admin = User.objects.create_superuser('admin', 'admin@abtc.gov.ph', 'admin123', role='admin', first_name='System', last_name='Admin')
        User.objects.create_user('doctor1', 'doctor@abtc.gov.ph', 'doctor123', role='doctor', first_name='Maria', last_name='Santos')
        User.objects.create_user('nurse1', 'nurse@abtc.gov.ph', 'nurse123', role='nurse', first_name='Juan', last_name='Dela Cruz')
        User.objects.create_user('encoder1', 'encoder@abtc.gov.ph', 'encoder123', role='encoder', first_name='Pedro', last_name='Gonzales')

        barangays = ['Poblacion', 'San Jose', 'San Juan', 'Santo Nino', 'San Isidro', 'San Vicente', 'San Miguel', 'San Roque', 'San Rafael', 'Santa Cruz']
        for name in barangays:
            Barangay.objects.create(name=name)

        SystemSetting.objects.create(key='municipality_name', value='San Juan Municipal Health Office', description='Name of the municipality')
        SystemSetting.objects.create(key='vaccination_days', value='0,3,7,14,28', description='Default vaccination schedule days')
        SystemSetting.objects.create(key='low_stock_threshold', value='5', description='Low stock alert threshold')

        first_names = ['Maria', 'Juan', 'Jose', 'Pedro', 'Ana', 'Luisa', 'Carlos', 'Elena', 'Ricardo', 'Isabel']
        last_names = ['Santos', 'Reyes', 'Cruz', 'Bautista', 'Garcia', 'Mendoza', 'Torres', 'Rivera', 'Flores', 'Gonzales']
        barangay_list = list(Barangay.objects.all())

        for i in range(20):
            fn = random.choice(first_names)
            ln = random.choice(last_names)
            bday = date.today() - timedelta(days=random.randint(365*5, 365*70))
            sex = random.choice(['male', 'female'])
            Patient.objects.create(
                patient_number=f"PAT-{i+1:05d}",
                first_name=fn, last_name=ln, birthdate=bday, sex=sex,
                contact_number=f"09{random.randint(100000000, 999999999)}",
                address=f"{random.randint(1, 999)} {random.choice(['Rizal St', 'Bonifacio St', 'Mabini St', 'Del Pilar St', 'Luna St'])}",
                barangay=random.choice(barangay_list),
                civil_status=random.choice(['single', 'married']),
                created_by=admin,
            )

        patients = list(Patient.objects.all())
        animal_types = ['dog', 'cat', 'monkey', 'bat']

        for i in range(25):
            patient = random.choice(patients)
            bite_date = timezone.now() - timedelta(days=random.randint(0, 60))
            case = AnimalBiteCase.objects.create(
                case_number=f"ABC-{i+1:05d}",
                patient=patient, date_time_bite=bite_date,
                place_of_incident=patient.address, incident_barangay=str(patient.barangay or 'Unknown'),
                animal_type=random.choice(animal_types),
                animal_ownership=random.choice(['owned', 'stray', 'unknown']),
                animal_vax_status=random.choice(['vaccinated', 'not_vaccinated', 'unknown']),
                animal_condition=random.choice(['alive', 'dead', 'missing', 'under_observation']),
                exposure_type=random.choice(['bite', 'scratch', 'lick', 'saliva']),
                body_part_affected=random.choice(['Left arm', 'Right arm', 'Left leg', 'Right leg', 'Face', 'Torso']),
                number_of_wounds=random.randint(1, 5),
                case_status=random.choice(['new', 'under_treatment', 'completed', 'missed']),
                created_by=admin,
            )

            if random.random() > 0.3:
                ExposureClassification.objects.create(
                    bite_case=case,
                    category=random.choice(['category_i', 'category_ii', 'category_iii']),
                    classified_by=User.objects.filter(role='doctor').first(),
                    medical_notes='Patient assessed and classified accordingly.',
                    treatment_plan='Follow standard rabies vaccination protocol.',
                )

            if random.random() > 0.4:
                schedule = VaccinationSchedule.objects.create(bite_case=case, created_by=admin)
                for day in [0, 3, 7, 14, 28]:
                    dose_date = bite_date.date() + timedelta(days=day)
                    status = 'completed' if dose_date < date.today() else ('missed' if dose_date < date.today() - timedelta(days=1) else 'scheduled')
                    VaccineDose.objects.create(
                        schedule=schedule, dose_label=f"Day {day}",
                        scheduled_date=dose_date,
                        actual_date=dose_date if status == 'completed' else None,
                        dose_status=status,
                    )

            if random.random() > 0.5:
                FollowUpRecord.objects.create(
                    bite_case=case,
                    follow_up_date=timezone.now().date() + timedelta(days=random.randint(-10, 10)),
                    status=random.choice(['pending', 'completed', 'missed']),
                )

        VaccineInventory.objects.create(vaccine_name='Rabies Vaccine', brand='Verorab', batch_number='VR-2024-001', expiration_date=date.today() + timedelta(days=180), quantity_received=100, quantity_available=85, quantity_used=15, created_by=admin)
        VaccineInventory.objects.create(vaccine_name='Rabies Vaccine', brand='Rabipur', batch_number='RP-2024-002', expiration_date=date.today() + timedelta(days=365), quantity_received=200, quantity_available=200, quantity_used=0, created_by=admin)
        VaccineInventory.objects.create(vaccine_name='RIG (Rabies Immunoglobulin)', brand='Imogam', batch_number='IG-2024-001', expiration_date=date.today() + timedelta(days=90), quantity_received=50, quantity_available=3, quantity_used=47, created_by=admin, remarks='Low stock - reorder needed')
        VaccineInventory.objects.create(vaccine_name='Tetanus Toxoid', brand='TT-SII', batch_number='TT-2024-001', expiration_date=date.today() - timedelta(days=10), quantity_received=100, quantity_available=50, quantity_used=50, created_by=admin, remarks='EXPIRED')

        SupplyInventory.objects.create(supply_name='3ml Syringe', supply_type='syringe', quantity=500, unit='pieces', reorder_level=100)
        SupplyInventory.objects.create(supply_name='Cotton Balls', supply_type='cotton', quantity=10, unit='boxes', reorder_level=5)
        SupplyInventory.objects.create(supply_name='Isopropyl Alcohol', supply_type='alcohol', quantity=8, unit='bottles', reorder_level=3)
        SupplyInventory.objects.create(supply_name='Latex Gloves', supply_type='gloves', quantity=200, unit='pairs', reorder_level=50)

        AuditLog.objects.create(user=admin, action='SEED_DATA', description='Database seeded with sample data')

        self.stdout.write(self.style.SUCCESS('Database seeded successfully!'))
        self.stdout.write(f'Admin credentials: username=admin, password=admin123')
        self.stdout.write(f'Doctor: username=doctor1, password=doctor123')
        self.stdout.write(f'Nurse: username=nurse1, password=nurse123')
        self.stdout.write(f'Encoder: username=encoder1, password=encoder123')
