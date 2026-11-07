import datetime
import re
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from bite_cases.models import AnimalBiteCase
from doctor.models import ClinicalAssessment, Prescription
from patients.models import Patient
from settings_app.models import SystemSetting
from vaccination.models import VaccinationSchedule, VaccineDose

from .models import (
    MedicineInventory, MedicineStockMovement, VaccineInventory,
    VaccineStockMovement,
)
from .notifications import (
    EXPIRY_SETTING_KEY, get_inventory_alerts, medicine_alerts, vaccine_alerts,
)

U = get_user_model()


class InventoryTestCase(TestCase):

    def setUp(self):
        self.admin = U.objects.create_user("med.admin", password="pw12345678", role="admin")
        self.nurse = U.objects.create_user("med.nurse", password="pw12345678", role="nurse")
        self.doctor = U.objects.create_user("med.doc", password="pw12345678", role="doctor")
        self.patient_user = U.objects.create_user("med.patient", password="pw12345678", role="patient")
        self.patient = Patient.objects.create(
            patient_number="PAT-M0001", first_name="Mia", last_name="Stock",
            birthdate=datetime.date(1995, 5, 5), contact_number="09170000002",
        )
        self.case = AnimalBiteCase.objects.create(
            case_number="ABC-M0001", patient=self.patient, created_by=self.nurse,
            animal_type="dog", animal_ownership="owned", animal_vax_status="unknown",
            animal_condition="under_observation", exposure_type="bite",
            body_part_affected="left hand", number_of_wounds=1,
            date_time_bite=datetime.datetime(2026, 10, 30, 9, 0),
        )
        self.assessment = ClinicalAssessment.objects.create(
            patient=self.patient, bite_case=self.case, assessed_by=self.doctor,
            chief_complaint="Bite wound", category_confirmed="category_ii",
            status="final", is_finalized=True,
        )
        self.medicine = MedicineInventory.objects.create(
            medicine_name="Amoxicillin", generic_name="Amoxicillin trihydrate",
            strength="500 mg", dosage_form="capsule", unit="capsule",
            batch_number="MED-1", expiration_date=datetime.date(2027, 6, 1),
            quantity_received=10, quantity_available=10, reorder_level=3,
        )

    def _prescription(self, quantity=3, medicine=None, status="pending"):
        return Prescription.objects.create(
            assessment=self.assessment, patient=self.patient, bite_case=self.case,
            medicine=medicine or self.medicine, dose="1 capsule",
            quantity=quantity, status=status,
        )


class MedicineInventoryTests(InventoryTestCase):

    def test_dose_unit_follows_the_dosage_form(self):
        tablet = MedicineInventory(medicine_name="A", dosage_form="tablet", unit="tablet")
        capsule = MedicineInventory(medicine_name="B", dosage_form="capsule", unit="capsule")
        syrup = MedicineInventory(medicine_name="C", dosage_form="syrup", unit="mL")
        injectable = MedicineInventory(medicine_name="D", dosage_form="injectable", unit="vial")
        self.assertEqual(tablet.dose_unit(), "tablet(s) per dose")
        self.assertEqual(capsule.dose_unit(), "capsule(s) per dose")
        self.assertEqual(syrup.dose_unit(), "mL per dose")
        self.assertEqual(injectable.dose_unit(), "vial per dose")

    def test_stock_status_reflects_expiry_and_levels(self):
        self.assertEqual(self.medicine.stock_status(), "Available")
        self.medicine.quantity_available = 3
        self.assertEqual(self.medicine.stock_status(), "Low Stock")
        self.medicine.quantity_available = 0
        self.assertEqual(self.medicine.stock_status(), "Out of Stock")
        self.medicine.quantity_available = 50
        self.medicine.expiration_date = timezone.localdate() - datetime.timedelta(days=1)
        self.assertEqual(self.medicine.stock_status(), "Expired")

    def test_stock_in_records_available_quantity_and_movement(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("inventory:medicine_create"), {
            "medicine_name": "Paracetamol",
            "generic_name": "Acetaminophen",
            "strength": "500 mg",
            "dosage_form": "tablet",
            "unit": "tablet",
            "batch_number": "MED-9",
            "expiration_date": "2027-08-01",
            "quantity_received": "40",
            "reorder_level": "5",
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        item = MedicineInventory.objects.get(batch_number="MED-9")
        self.assertEqual(item.quantity_available, 40)
        self.assertEqual(item.quantity_dispensed, 0)
        self.assertEqual(item.created_by, self.admin)
        movement = MedicineStockMovement.objects.get(medicine=item)
        self.assertEqual(movement.movement_type, "stock_in")
        self.assertEqual(movement.quantity, 40)

    def test_list_shows_stock_status_and_search(self):
        self.medicine.quantity_available = 1
        self.medicine.save(update_fields=["quantity_available"])
        self.client.force_login(self.nurse)
        response = self.client.get(reverse("inventory:medicine_list"))
        self.assertContains(response, "Amoxicillin")
        self.assertContains(response, "Low Stock")
        response = self.client.get(reverse("inventory:medicine_list"), {"q": "trihydrate"})
        self.assertContains(response, "Amoxicillin")
        response = self.client.get(reverse("inventory:medicine_list"), {"q": "nothing"})
        self.assertContains(response, "No medicine inventory found.")

    def test_nurse_search_section_hides_stock_filter_and_reset(self):
        self.client.force_login(self.nurse)
        content = self.client.get(reverse("inventory:medicine_list")).content.decode()
        start = content.index('<form method="GET"')
        form = content[start:content.index("</form>", start)]
        self.assertIn('name="q"', form)
        self.assertIn("Search medicine, generic, strength, batch...", form)
        self.assertIn(">Search</button>", form)
        self.assertNotIn('name="status"', form)
        self.assertNotIn("All stock", form)
        self.assertNotIn("Reset", form)

    def test_nurse_search_section_keeps_the_full_table(self):
        self.client.force_login(self.nurse)
        response = self.client.get(reverse("inventory:medicine_list"))
        for header in ("Medicine", "Form", "Unit", "Batches", "Available",
                       "Expiry", "Status", "Actions"):
            self.assertContains(response, header)
        self.assertContains(response, "Amoxicillin")
        self.assertContains(
            response, reverse("inventory:medicine_detail", args=[self.medicine.pk]))

    def test_nurse_clearing_the_search_lists_all_medicines(self):
        self.client.force_login(self.nurse)
        filtered = self.client.get(reverse("inventory:medicine_list"), {"q": "Amoxicillin"})
        self.assertContains(filtered, "Amoxicillin")
        cleared = self.client.get(reverse("inventory:medicine_list"), {"q": ""})
        self.assertContains(cleared, "Amoxicillin")

    def test_admin_keeps_stock_filter_and_reset(self):
        self.client.force_login(self.admin)
        content = self.client.get(reverse("inventory:medicine_list")).content.decode()
        start = content.index('<form method="GET"')
        form = content[start:content.index("</form>", start)]
        self.assertIn('name="status"', form)
        self.assertIn("All stock", form)
        self.assertIn("Reset", form)

    def test_patients_cannot_open_medicine_inventory(self):
        self.client.force_login(self.patient_user)
        response = self.client.get(reverse("inventory:medicine_list"))
        self.assertRedirects(response, reverse("accounts:patient_portal"))


class VaccineInventoryViewButtonTests(InventoryTestCase):
    """Nurses get the larger emerald View button; other roles keep the original."""

    def setUp(self):
        super().setUp()
        self.vaccine = VaccineInventory.objects.create(
            vaccine_name="Rabies Vaccine", brand="Verorab",
            batch_number="VAC-1", expiration_date=datetime.date(2027, 6, 1),
            quantity_received=10, quantity_available=10,
        )
        self.detail_url = reverse(
            "inventory:vaccine_detail", args=[self.vaccine.pk])

    def test_nurse_sees_the_larger_emerald_view_button(self):
        self.client.force_login(self.nurse)
        content = self.client.get(reverse("inventory:vaccine_list")).content.decode()
        self.assertIn(
            f'<a href="{self.detail_url}" class="btn-view-vaccine">View <svg', content)
        self.assertIn("M5 12h14M13 6l6 6-6 6", content)
        for rule in (
            "background-color: #157A61",
            "color: #FFFFFF",
            "font-size: 14px",
            "font-weight: 600",
            "padding: 10px 22px",
            "min-height: 42px",
            "border-radius: 25px",
            "background-color: #0D5B48",
            "box-shadow:",
        ):
            self.assertIn(rule, content)

    def test_nurse_table_content_and_headers_stay_intact(self):
        self.client.force_login(self.nurse)
        response = self.client.get(reverse("inventory:vaccine_list"))
        for header in ("Vaccine", "Brand", "Batches", "Available",
                       "Used", "Expiry", "Status", "Actions"):
            self.assertContains(response, header)
        for value in ("Rabies Vaccine", "Verorab", "1 batch"):
            self.assertContains(response, value)
        detail = self.client.get(self.detail_url)
        self.assertContains(detail, "Rabies Vaccine")
        self.assertContains(detail, "Verorab")

    def test_admin_keeps_the_original_view_button(self):
        self.client.force_login(self.admin)
        content = self.client.get(reverse("inventory:vaccine_list")).content.decode()
        self.assertIn(
            f'<a href="{self.detail_url}" class="btn-secondary btn-xs">View</a>',
            content)
        self.assertNotIn("btn-view-vaccine", content)

    def test_nurse_view_button_still_opens_the_detail_page(self):
        self.client.force_login(self.nurse)
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Rabies Vaccine")


class VaccineProductGroupingTests(InventoryTestCase):
    """Nurse listing shows one row per vaccine product, not per batch."""

    def setUp(self):
        super().setUp()
        self.batch1 = VaccineInventory.objects.create(
            vaccine_name="Imovax", brand="Sanofi", batch_number="Batch 1",
            expiration_date=datetime.date(2027, 10, 15),
            quantity_received=10, quantity_available=10, quantity_used=0,
        )
        self.batch2 = VaccineInventory.objects.create(
            vaccine_name="Imovax", brand="Sanofi", batch_number="Batch 2",
            expiration_date=datetime.date(2027, 11, 5),
            quantity_received=100, quantity_available=90, quantity_used=10,
        )
        self.batch3 = VaccineInventory.objects.create(
            vaccine_name="Imovax", brand="Sanofi", batch_number="Batch 3",
            expiration_date=datetime.date(2027, 11, 3),
            quantity_received=20, quantity_available=20, quantity_used=0,
        )
        self.other_brand = VaccineInventory.objects.create(
            vaccine_name="Imovax", brand="Other Brand", batch_number="Batch X",
            expiration_date=datetime.date(2027, 12, 1),
            quantity_received=7, quantity_available=7,
        )
        self.other_product = VaccineInventory.objects.create(
            vaccine_name="Verorab", brand="Sanofi", batch_number="V1",
            expiration_date=datetime.date(2027, 5, 1),
            quantity_received=50, quantity_available=50,
        )
        self.list_url = reverse("inventory:vaccine_list")

    def _group_row(self, content, name, brand=None):
        row = next(
            r for r in re.findall(r"<tr[^>]*>(.*?)</tr>", content, re.S)
            if f'>{name}<' in r
            and (brand is None or f'>{brand}<' in r))
        return [
            re.sub(r'<[^>]+>', '', cell).strip()
            for cell in re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)
        ]

    def test_nurse_list_shows_one_row_per_vaccine_product(self):
        self.client.force_login(self.nurse)
        content = self.client.get(self.list_url).content.decode()
        self.assertEqual(content.count(">Imovax<"), 2)
        self.assertEqual(content.count(">Verorab<"), 1)
        self.assertNotIn(">Batch 1<", content)
        self.assertNotIn(">Batch 2<", content)
        self.assertIn("3 batches", content)
        self.assertIn("1 batch", content)

    def test_product_group_sums_quantities_and_labels_multiple_expiries(self):
        self.client.force_login(self.nurse)
        content = self.client.get(self.list_url).content.decode()
        cells = self._group_row(content, "Imovax", "Sanofi")
        self.assertEqual(cells[2], "3 batches")
        self.assertEqual(cells[3], "120")
        self.assertEqual(cells[4], "10")
        self.assertEqual(cells[5], "Multiple Expiries")
        self.assertEqual(cells[6], "Available")
        self.assertIn("Batch 3:", content)
        self.assertNotIn("Batch Batch", content)

    def test_single_expiry_product_shows_its_date(self):
        VaccineInventory.objects.filter(
            vaccine_name="Imovax", brand="Sanofi").exclude(
            pk=self.batch1.pk).delete()
        self.client.force_login(self.nurse)
        content = self.client.get(self.list_url).content.decode()
        cells = self._group_row(content, "Imovax", "Sanofi")
        self.assertEqual(cells[2], "1 batch")
        self.assertEqual(cells[5], "Oct 15, 2027")

    def test_expired_batches_are_excluded_from_available_stock(self):
        VaccineInventory.objects.filter(pk=self.batch1.pk).update(
            expiration_date=datetime.date(2025, 1, 1))
        self.client.force_login(self.nurse)
        content = self.client.get(self.list_url).content.decode()
        cells = self._group_row(content, "Imovax", "Sanofi")
        self.assertEqual(cells[3], "110")
        self.assertEqual(cells[6], "Available")

    def test_fully_expired_product_is_marked_expired(self):
        VaccineInventory.objects.filter(
            vaccine_name="Imovax", brand="Sanofi").update(
            expiration_date=datetime.date(2025, 1, 1))
        self.client.force_login(self.nurse)
        content = self.client.get(self.list_url).content.decode()
        cells = self._group_row(content, "Imovax", "Sanofi")
        self.assertEqual(cells[3], "0")
        self.assertEqual(cells[5], "Expired")
        self.assertEqual(cells[6], "Expired")

    def test_search_by_batch_number_returns_the_grouped_product(self):
        self.client.force_login(self.nurse)
        content = self.client.get(self.list_url, {"q": "Batch 2"}).content.decode()
        self.assertEqual(content.count(">Imovax<"), 1)
        self.assertIn("3 batches", content)
        self.assertNotIn(">Verorab<", content)
        self.assertNotIn("Other Brand", content)

    def test_add_stock_adds_a_batch_without_duplicating_the_product(self):
        self.client.force_login(self.nurse)
        response = self.client.post(
            reverse("inventory:vaccine_add_stock", args=[self.batch1.pk]),
            {
                "batch_number": "Batch 4",
                "quantity_received": "30",
                "source_supplier": "ABC Pharma",
                "date_received": "2026-11-03",
                "time_received": "09:30",
                "expiration_date": "2027-12-31",
                "remarks": "New batch",
                "submission_token": str(uuid.uuid4()),
            },
            follow=True,
        )
        self.assertContains(response, "Stock added successfully.")
        self.assertEqual(
            VaccineInventory.objects.filter(
                vaccine_name="Imovax", brand="Sanofi").count(), 4)
        self.assertEqual(
            VaccineInventory.objects.filter(
                vaccine_name="Imovax", brand="Other Brand").count(), 1)

        content = self.client.get(self.list_url).content.decode()
        self.assertEqual(content.count(">Imovax<"), 2)
        cells = self._group_row(content, "Imovax", "Sanofi")
        self.assertEqual(cells[2], "4 batches")
        self.assertEqual(cells[3], "150")

        detail = self.client.get(
            reverse("inventory:vaccine_detail", args=[self.batch3.pk]))
        self.assertContains(detail, "Batch 4")
        self.assertContains(detail, "09:30 AM")
        self.assertContains(detail, "150")

    def test_group_actions_open_the_product_detail_and_add_stock(self):
        self.client.force_login(self.nurse)
        content = self.client.get(self.list_url).content.decode()
        self.assertIn(
            reverse("inventory:vaccine_detail", args=[self.batch1.pk]), content)
        self.assertIn(
            reverse("inventory:vaccine_add_stock", args=[self.batch1.pk]), content)

    def test_admin_list_still_shows_each_batch(self):
        self.client.force_login(self.admin)
        content = self.client.get(self.list_url).content.decode()
        self.assertEqual(content.count(">Imovax<"), 4)
        for number in ("Batch 1", "Batch 2", "Batch 3"):
            self.assertIn(number, content)


class VaccineInventoryDetailRedesignTests(InventoryTestCase):
    """Nurse detail page: four compact sections, real stock and dose records."""

    def setUp(self):
        super().setUp()
        self.vaccine = VaccineInventory.objects.create(
            vaccine_name="Rabies Vaccine", brand="Verorab",
            batch_number="VAC-1", expiration_date=datetime.date(2027, 6, 1),
            quantity_received=10, quantity_available=4, quantity_used=6,
            source_supplier="DOH Region IX", remarks="Keep refrigerated.",
        )
        VaccineStockMovement.objects.create(
            vaccine=self.vaccine, movement_type="stock_in", quantity=10,
            reference="PO-2026-01", moved_by=self.nurse,
        )
        self.schedule = VaccinationSchedule.objects.create(
            bite_case=self.case, created_by=self.nurse)
        self.dose = VaccineDose.objects.create(
            schedule=self.schedule, dose_label="1st Dose",
            batch_number="VAC-1", vaccine_brand="Verorab",
            dose_status="completed", actual_date=datetime.date(2026, 11, 2),
            actual_time=datetime.time(11, 2), administered_by=self.nurse,
        )
        self.url = reverse("inventory:vaccine_detail", args=[self.vaccine.pk])

    def test_nurse_detail_uses_the_four_section_layout(self):
        self.client.force_login(self.nurse)
        response = self.client.get(self.url)
        self.assertContains(response, "Vaccine Inventory Details")
        content = response.content.decode()
        headings = (
            "1. Vaccine Information",
            "2. Stock Summary",
            "3. Stock Transaction History",
            "4. Vaccination Administration History",
        )
        positions = [content.index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions))
        self.assertNotContains(response, "Stock Movement History")

    def test_nurse_detail_shows_real_inventory_and_transaction_data(self):
        self.client.force_login(self.nurse)
        response = self.client.get(self.url)
        for value in ("Rabies Vaccine", "Verorab", "VAC-1", "DOH Region IX",
                      "Stock In"):
            self.assertContains(response, value)
        for header in ("Date Received", "Time", "Type", "Supplier",
                       "Batch Number", "Expiration Date", "Quantity"):
            self.assertContains(response, header)

    def test_stock_history_columns_are_ordered_with_type_last(self):
        self.client.force_login(self.nurse)
        content = self.client.get(self.url).content.decode()
        history = content[
            content.index("3. Stock Transaction History"):
            content.index("4. Vaccination Administration History")
        ]
        headers = re.findall(
            r'<th class="table-header">([^<]+)</th>', history)
        self.assertEqual(headers, [
            "Date Received", "Time", "Supplier", "Batch Number",
            "Expiration Date", "Quantity", "Type",
        ])
        for width in ("width: 16%", "width: 11%", "width: 17%",
                      "width: 16%", "width: 18%", "width: 10%",
                      "width: 12%"):
            self.assertIn(width, content)
        for rule in ("padding: 17px 15px", "vertical-align: middle",
                     "text-align: right", "text-align: center"):
            self.assertIn(rule, content)

    def test_stock_history_shows_date_received_and_transaction_time_separately(self):
        self.vaccine.date_received = datetime.date(2026, 11, 2)
        self.vaccine.save(update_fields=['date_received'])
        movement = VaccineStockMovement.objects.get(vaccine=self.vaccine)
        movement.time_received = datetime.time(9, 30)
        movement.save(update_fields=['time_received'])

        self.client.force_login(self.nurse)
        content = self.client.get(self.url).content.decode()
        history = content[
            content.index("3. Stock Transaction History"):
            content.index("4. Vaccination Administration History")
        ]
        row = next(
            r for r in re.findall(r"<tr[^>]*>(.*?)</tr>", history, re.S)
            if "Stock In" in r)
        cells = [
            re.sub(r'<[^>]+>', '', cell).strip()
            for cell in re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)
        ]
        self.assertEqual(cells[0], "Nov 02, 2026")
        self.assertEqual(cells[1], "09:30 AM")

    def test_historical_stock_in_without_receiving_time_shows_a_dash(self):
        self.client.force_login(self.nurse)
        content = self.client.get(self.url).content.decode()
        history = content[
            content.index("3. Stock Transaction History"):
            content.index("4. Vaccination Administration History")
        ]
        row = next(
            r for r in re.findall(r"<tr[^>]*>(.*?)</tr>", history, re.S)
            if "Stock In" in r)
        cells = [
            re.sub(r'<[^>]+>', '', cell).strip()
            for cell in re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)
        ]
        self.assertIsNone(
            VaccineStockMovement.objects.get(vaccine=self.vaccine).time_received)
        self.assertEqual(cells[1], "—")

    def test_stock_history_hides_reference_and_recorded_by_columns(self):
        self.client.force_login(self.nurse)
        content = self.client.get(self.url).content.decode()
        history = content[
            content.index("3. Stock Transaction History"):
            content.index("4. Vaccination Administration History")
        ]
        for hidden in ("Reference", "Recorded By", "PO-2026-01", "med.nurse"):
            self.assertNotIn(hidden, history)
        movement = VaccineStockMovement.objects.get(vaccine=self.vaccine)
        self.assertEqual(movement.reference, "PO-2026-01")
        self.assertEqual(movement.moved_by, self.nurse)

    def test_vaccine_information_shows_only_name_and_brand(self):
        self.client.force_login(self.nurse)
        content = self.client.get(self.url).content.decode()
        info = content[
            content.index("1. Vaccine Information"):
            content.index("2. Stock Summary")
        ]
        self.assertIn("Vaccine Name", info)
        self.assertIn("Brand", info)
        for hidden in ("Batch Number", "Supplier", "Date Received",
                       "Expiration Date", "Remarks", "Keep refrigerated."):
            self.assertNotIn(hidden, info)

    def test_stock_summary_totals_every_batch_of_the_product(self):
        VaccineInventory.objects.create(
            vaccine_name="Rabies Vaccine", brand="Verorab",
            batch_number="VAC-2", expiration_date=datetime.date(2027, 9, 1),
            quantity_received=15, quantity_available=12, quantity_used=3,
            source_supplier="ABC Pharma",
        )
        VaccineInventory.objects.create(
            vaccine_name="Rabies Vaccine", brand="Rabipur",
            batch_number="VAC-OTHER", expiration_date=datetime.date(2027, 9, 1),
            quantity_received=100, quantity_available=100,
        )
        self.client.force_login(self.nurse)
        content = self.client.get(self.url).content.decode()
        summary = content[
            content.index("2. Stock Summary"):
            content.index("3. Stock Transaction History")
        ]
        values = re.findall(r'vd-stock-value[^>]*>\s*(\d+)\s*<', summary)
        self.assertEqual(values, ["25", "9", "16"])
        self.assertNotIn("100", summary)

    def test_stock_history_combines_batch_deliveries_with_their_own_details(self):
        second = VaccineInventory.objects.create(
            vaccine_name="Rabies Vaccine", brand="Verorab",
            batch_number="VAC-2", expiration_date=datetime.date(2027, 9, 1),
            quantity_received=15, quantity_available=12, quantity_used=3,
            source_supplier="ABC Pharma",
        )
        VaccineStockMovement.objects.create(
            vaccine=second, movement_type="stock_in", quantity=15,
            supplier="ABC Pharma", date_received=datetime.date(2026, 10, 20),
            batch_number="VAC-2", expiration_date=datetime.date(2027, 9, 1),
            reference="PO-2026-02", moved_by=self.nurse,
        )
        self.client.force_login(self.nurse)
        content = self.client.get(self.url).content.decode()
        history = content[
            content.index("3. Stock Transaction History"):
            content.index("4. Vaccination Administration History")
        ]
        for value in ("DOH Region IX", "ABC Pharma", "VAC-1", "VAC-2",
                      "Jun 01, 2027", "Sep 01, 2027"):
            self.assertIn(value, history)
        self.assertLess(history.index("VAC-2"), history.index("VAC-1"))

    def test_dispensing_history_includes_doses_from_every_batch(self):
        VaccineInventory.objects.create(
            vaccine_name="Rabies Vaccine", brand="Verorab",
            batch_number="VAC-2", expiration_date=datetime.date(2027, 9, 1),
            quantity_received=15, quantity_available=15,
        )
        VaccineDose.objects.create(
            schedule=self.schedule, dose_label="2nd Dose",
            batch_number="VAC-2", vaccine_brand="Verorab",
            dose_status="completed", actual_date=datetime.date(2026, 11, 3),
            actual_time=datetime.time(9, 30), administered_by=self.nurse,
        )
        self.client.force_login(self.nurse)
        content = self.client.get(self.url).content.decode()
        dispensing = content[
            content.index("4. Vaccination Administration History"):]
        self.assertIn("1st Dose", dispensing)
        self.assertIn("2nd Dose", dispensing)

    def test_nurse_detail_shows_actual_administration_history(self):
        self.client.force_login(self.nurse)
        response = self.client.get(self.url)
        content = response.content.decode()
        dispensing = content[
            content.index("4. Vaccination Administration History"):]
        for value in ("Mia Stock", "ABC-M0001", "1st Dose", "1 vial",
                      "Nov 02, 2026", "11:02 AM"):
            self.assertIn(value, dispensing)
        for header in ("Patient", "Case", "Dose", "Qty",
                       "Date Administered", "Time"):
            self.assertIn(header, dispensing)
        for width in ("width: 21%", "width: 13%", "width: 27%",
                      "width: 9%", "width: 19%"):
            self.assertIn(width, content)
        self.assertNotIn("med.nurse", dispensing)
        self.assertNotIn("Administered By", dispensing)
        self.assertNotIn("Dispensed By", dispensing)

        row = next(
            r for r in re.findall(r"<tr[^>]*>(.*?)</tr>", dispensing, re.S)
            if "1st Dose" in r)
        cells = [
            re.sub(r'<[^>]+>', '', cell).strip()
            for cell in re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)
        ]
        self.assertEqual(cells[4], "Nov 02, 2026")
        self.assertEqual(cells[5], "11:02 AM")

    def test_unadministered_dose_is_shown_as_pending(self):
        VaccineDose.objects.create(
            schedule=self.schedule, dose_label="2nd Dose",
            batch_number="VAC-1", dose_status="scheduled")
        self.client.force_login(self.nurse)
        response = self.client.get(self.url)
        self.assertContains(response, "2nd Dose")
        self.assertContains(response, "Pending")

    def test_nurse_detail_shows_empty_states_when_no_records_exist(self):
        empty = VaccineInventory.objects.create(
            vaccine_name="Empty Vaccine", brand="None",
            batch_number="VAC-EMPTY", expiration_date=datetime.date(2027, 1, 1),
            quantity_received=0, quantity_available=0,
        )
        self.client.force_login(self.nurse)
        response = self.client.get(
            reverse("inventory:vaccine_detail", args=[empty.pk]))
        self.assertContains(response, "No stock receiving history recorded yet.")
        self.assertContains(
            response, "No vaccination administration history recorded yet.")

    def test_nurse_detail_has_no_edit_or_delete_controls(self):
        self.client.force_login(self.nurse)
        response = self.client.get(self.url)
        self.assertNotContains(response, "Delete")
        self.assertNotContains(response, reverse("inventory:vaccine_create"))

    def test_admin_detail_keeps_the_existing_layout(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.url)
        self.assertContains(response, "Stock Movement History")
        self.assertNotContains(response, "Vaccine Inventory Details")
        self.assertNotContains(response, "4. Vaccination Administration History")


class DispensingTests(InventoryTestCase):

    def test_pending_prescription_does_not_touch_stock(self):
        prescription = self._prescription(quantity=3)
        self.medicine.refresh_from_db()
        self.assertEqual(self.medicine.quantity_available, 10)
        self.assertEqual(prescription.status, "pending")
        self.assertFalse(MedicineStockMovement.objects.filter(movement_type="dispense").exists())

    def test_nurse_confirmation_deducts_stock_once(self):
        prescription = self._prescription(quantity=3)
        self.client.force_login(self.nurse)
        response = self.client.post(
            reverse("inventory:prescription_dispense", args=[prescription.pk]), follow=True)
        self.assertEqual(response.status_code, 200)

        self.medicine.refresh_from_db()
        prescription.refresh_from_db()
        self.assertEqual(self.medicine.quantity_available, 7)
        self.assertEqual(self.medicine.quantity_dispensed, 3)
        self.assertEqual(prescription.status, "dispensed")
        self.assertEqual(prescription.dispensed_by, self.nurse)
        self.assertIsNotNone(prescription.dispensed_at)

        movement = MedicineStockMovement.objects.get(movement_type="dispense")
        self.assertEqual(movement.medicine, self.medicine)
        self.assertEqual(movement.quantity, 3)
        self.assertEqual(movement.reference, self.case.case_number)
        self.assertEqual(movement.moved_by, self.nurse)

        # A second confirmation must not deduct again.
        self.client.post(
            reverse("inventory:prescription_dispense", args=[prescription.pk]), follow=True)
        self.medicine.refresh_from_db()
        self.assertEqual(self.medicine.quantity_available, 7)
        self.assertEqual(MedicineStockMovement.objects.filter(movement_type="dispense").count(), 1)

    def test_cannot_dispense_more_than_available(self):
        prescription = self._prescription(quantity=20)
        self.client.force_login(self.nurse)
        response = self.client.post(
            reverse("inventory:prescription_dispense", args=[prescription.pk]), follow=True)
        self.assertContains(response, "left in stock")
        self.medicine.refresh_from_db()
        prescription.refresh_from_db()
        self.assertEqual(self.medicine.quantity_available, 10)
        self.assertEqual(prescription.status, "pending")
        self.assertFalse(MedicineStockMovement.objects.filter(movement_type="dispense").exists())

    def test_expired_lot_is_blocked(self):
        self.medicine.expiration_date = timezone.localdate() - datetime.timedelta(days=1)
        self.medicine.save(update_fields=["expiration_date"])
        prescription = self._prescription(quantity=1)
        self.client.force_login(self.nurse)
        response = self.client.post(
            reverse("inventory:prescription_dispense", args=[prescription.pk]), follow=True)
        self.assertContains(response, "expired")
        prescription.refresh_from_db()
        self.assertEqual(prescription.status, "pending")

    def test_doctor_can_view_but_not_dispense(self):
        prescription = self._prescription(quantity=1)
        self.client.force_login(self.doctor)
        response = self.client.get(reverse("inventory:prescription_list"))
        self.assertContains(response, "Pending Dispensing")
        self.assertNotContains(response, "Confirm dispensing")
        self.client.post(
            reverse("inventory:prescription_dispense", args=[prescription.pk]), follow=True)
        prescription.refresh_from_db()
        self.assertEqual(prescription.status, "pending")
        self.medicine.refresh_from_db()
        self.assertEqual(self.medicine.quantity_available, 10)

    def test_dispensing_queue_lists_pending_and_history(self):
        pending = self._prescription(quantity=2)
        done = self._prescription(quantity=4, status="dispensed")
        self.client.force_login(self.nurse)
        response = self.client.get(reverse("inventory:prescription_list"))
        self.assertContains(response, "Confirm dispensing")
        self.assertContains(response, "Pending Dispensing")
        response = self.client.get(reverse("inventory:prescription_list"), {"status": "all"})
        self.assertContains(response, "Mia Stock")
        self.assertContains(response, self.case.case_number)

    def test_patients_cannot_open_the_dispensing_queue(self):
        self.client.force_login(self.patient_user)
        response = self.client.get(reverse("inventory:prescription_list"))
        self.assertRedirects(response, reverse("accounts:patient_portal"))


class VaccineAddStockTests(InventoryTestCase):
    """Add Stock records one delivery for the selected vaccine batch."""

    def setUp(self):
        super().setUp()
        self.vaccine = VaccineInventory.objects.create(
            vaccine_name="Imovax", brand="Sanofi", batch_number="Batch 1",
            expiration_date=datetime.date(2027, 10, 15),
            quantity_received=10, quantity_available=10,
            source_supplier="Lenrose",
            date_received=datetime.date(2026, 10, 15),
            created_by=self.nurse,
        )
        VaccineStockMovement.objects.create(
            vaccine=self.vaccine, movement_type="stock_in", quantity=10,
            supplier="Lenrose", date_received=datetime.date(2026, 10, 15),
            batch_number="Batch 1",
            expiration_date=datetime.date(2027, 10, 15),
            moved_by=self.nurse,
        )
        self.url = reverse(
            "inventory:vaccine_add_stock", args=[self.vaccine.pk])
        self.payload = {
            "batch_number": "IMX-2026-002",
            "quantity_received": "25",
            "source_supplier": "ABC Pharma",
            "date_received": "2026-10-28",
            "time_received": "09:30",
            "expiration_date": "2028-12-15",
            "remarks": "New vaccine stock delivery.",
            "submission_token": str(uuid.uuid4()),
        }

    def test_nurse_actions_show_view_and_add_stock_side_by_side(self):
        self.client.force_login(self.nurse)
        response = self.client.get(reverse("inventory:vaccine_list"))
        content = response.content.decode()
        self.assertIn(self.url, content)
        self.assertIn("btn-add-stock-vaccine", content)
        self.assertIn("M12 5v14M5 12h14", content)
        self.assertIn(
            f'<a href="{reverse("inventory:vaccine_detail", args=[self.vaccine.pk])}" class="btn-view-vaccine">',
            content,
        )
        for rule in (
            "background-color: #FFFDF7",
            "border: 1.5px solid #157A61",
            "color: #0D5B48",
            "min-height: 42px",
            "border-radius: 25px",
        ):
            self.assertIn(rule, content)
        self.assertContains(response, "+ Stock In")
        self.assertContains(response, 'name="q"')

    def test_admin_actions_keep_the_existing_view_button(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("inventory:vaccine_list"))
        self.assertNotContains(response, self.url)
        self.assertContains(
            response,
            f'<a href="{reverse("inventory:vaccine_detail", args=[self.vaccine.pk])}" '
            'class="btn-secondary btn-xs">View</a>',
        )

    def test_form_identifies_the_selected_vaccine(self):
        self.client.force_login(self.nurse)
        response = self.client.get(self.url)
        self.assertContains(response, "Add New Stock")
        self.assertContains(response, "Record newly received vaccine stock.")
        self.assertContains(response, "Imovax")
        self.assertContains(response, "Sanofi")
        for label in (
            "Vaccine Name", "Brand", "Batch Number", "Quantity Received",
            "Supplier", "Date Received", "Time Received", "Expiration Date",
            "Notes / Remarks",
        ):
            self.assertContains(response, label)
        self.assertNotContains(response, 'name="vaccine_name"')
        self.assertNotContains(response, 'name="brand"')
        self.assertContains(response, 'name="submission_token"')
        self.assertContains(response, 'name="time_received"')
        self.assertContains(response, 'type="time"')
        self.assertContains(response, 'id="save-stock-button"')
        self.assertContains(
            response, f'value="{timezone.localdate().isoformat()}"')

    def test_new_batch_creates_a_separate_record(self):
        self.client.force_login(self.nurse)
        response = self.client.post(self.url, self.payload, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Stock added successfully.")

        self.assertEqual(VaccineInventory.objects.count(), 2)
        self.vaccine.refresh_from_db()
        self.assertEqual(self.vaccine.quantity_received, 10)
        self.assertEqual(self.vaccine.quantity_available, 10)
        self.assertEqual(self.vaccine.source_supplier, "Lenrose")

        batch = VaccineInventory.objects.get(batch_number="IMX-2026-002")
        self.assertEqual(batch.vaccine_name, "Imovax")
        self.assertEqual(batch.brand, "Sanofi")
        self.assertEqual(batch.quantity_received, 25)
        self.assertEqual(batch.quantity_available, 25)
        self.assertEqual(batch.quantity_used, 0)
        self.assertEqual(batch.source_supplier, "ABC Pharma")
        self.assertEqual(batch.date_received, datetime.date(2026, 10, 28))
        self.assertEqual(batch.expiration_date, datetime.date(2028, 12, 15))
        self.assertEqual(batch.remarks, "New vaccine stock delivery.")
        self.assertEqual(batch.created_by, self.nurse)

        movement = batch.movements.get()
        self.assertEqual(movement.movement_type, "stock_in")
        self.assertEqual(movement.quantity, 25)
        self.assertEqual(movement.supplier, "ABC Pharma")
        self.assertEqual(movement.date_received, datetime.date(2026, 10, 28))
        self.assertEqual(movement.time_received, datetime.time(9, 30))
        self.assertEqual(movement.batch_number, "IMX-2026-002")
        self.assertEqual(movement.expiration_date, datetime.date(2028, 12, 15))
        self.assertEqual(movement.moved_by, self.nurse)

        listing = self.client.get(reverse("inventory:vaccine_list"))
        self.assertContains(listing, "2 batches")
        self.assertNotIn(">IMX-2026-002<", listing.content.decode())
        self.assertEqual(listing.content.decode().count(">Imovax<"), 1)

        detail = self.client.get(
            reverse("inventory:vaccine_detail", args=[batch.pk]))
        for value in (
            "ABC Pharma", "IMX-2026-002", "Dec 15, 2028",
            "Oct 28, 2026", "09:30 AM", "25",
        ):
            self.assertContains(detail, value)
        for header in (
            "Date Received", "Supplier", "Batch Number", "Expiration Date",
        ):
            self.assertContains(detail, header)
        self.assertNotContains(detail, "Recorded By")

    def test_existing_batch_is_topped_up_without_losing_delivery_history(self):
        payload = dict(
            self.payload, batch_number="Batch 1",
            quantity_received="5", date_received="2026-11-02",
            expiration_date="2027-10-15", source_supplier="ABC Pharma",
        )
        self.client.force_login(self.nurse)
        response = self.client.post(self.url, payload, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Stock added successfully.")

        self.assertEqual(VaccineInventory.objects.count(), 1)
        self.vaccine.refresh_from_db()
        self.assertEqual(self.vaccine.quantity_received, 15)
        self.assertEqual(self.vaccine.quantity_available, 15)
        self.assertEqual(self.vaccine.source_supplier, "Lenrose")
        self.assertEqual(
            self.vaccine.date_received, datetime.date(2026, 10, 15))

        movements = self.vaccine.movements.order_by("id")
        self.assertEqual(movements.count(), 2)
        self.assertEqual(movements[0].supplier, "Lenrose")
        self.assertEqual(movements[0].quantity, 10)
        latest = movements.last()
        self.assertEqual(latest.quantity, 5)
        self.assertEqual(latest.supplier, "ABC Pharma")
        self.assertEqual(latest.date_received, datetime.date(2026, 11, 2))
        self.assertEqual(latest.time_received, datetime.time(9, 30))
        self.assertEqual(latest.batch_number, "Batch 1")
        self.assertIsNone(movements[0].time_received)
        self.assertEqual(latest.moved_by, self.nurse)

    def test_time_received_is_required(self):
        payload = dict(self.payload)
        payload.pop("time_received")
        self.client.force_login(self.nurse)
        response = self.client.post(self.url, payload)
        self.assertContains(response, "This field is required.")
        self.assertEqual(VaccineInventory.objects.count(), 1)
        self.assertEqual(self.vaccine.movements.count(), 1)

    def test_conflicting_expiration_date_blocks_the_delivery(self):
        payload = dict(
            self.payload, batch_number="Batch 1",
            quantity_received="5", expiration_date="2028-01-01",
        )
        self.client.force_login(self.nurse)
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "different expiration date")

        self.assertEqual(VaccineInventory.objects.count(), 1)
        self.vaccine.refresh_from_db()
        self.assertEqual(self.vaccine.quantity_received, 10)
        self.assertEqual(self.vaccine.quantity_available, 10)
        self.assertEqual(self.vaccine.movements.count(), 1)

    def test_quantity_must_be_greater_than_zero(self):
        payload = dict(self.payload, quantity_received="0")
        self.client.force_login(self.nurse)
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "greater than or equal to 1")
        self.assertEqual(VaccineInventory.objects.count(), 1)
        self.assertEqual(self.vaccine.movements.count(), 1)

    def test_top_up_preserves_used_quantity(self):
        self.vaccine.quantity_used = 3
        self.vaccine.quantity_available = 7
        self.vaccine.save(
            update_fields=["quantity_used", "quantity_available"])
        payload = dict(
            self.payload, batch_number="Batch 1", quantity_received="5",
            date_received="2026-11-02", expiration_date="2027-10-15",
            source_supplier="Second Supplier",
        )
        self.client.force_login(self.nurse)
        self.client.post(self.url, payload, follow=True)

        self.vaccine.refresh_from_db()
        self.assertEqual(self.vaccine.quantity_received, 15)
        self.assertEqual(self.vaccine.quantity_available, 12)
        self.assertEqual(self.vaccine.quantity_used, 3)

    def test_duplicate_submission_does_not_increase_inventory_twice(self):
        self.client.force_login(self.nurse)
        self.client.post(self.url, self.payload, follow=True)
        second = self.client.post(self.url, self.payload, follow=True)
        self.assertContains(second, "already recorded")

        self.assertEqual(VaccineInventory.objects.count(), 2)
        batch = VaccineInventory.objects.get(batch_number="IMX-2026-002")
        self.assertEqual(batch.quantity_received, 25)
        self.assertEqual(batch.movements.count(), 1)
        self.assertEqual(
            VaccineStockMovement.objects.filter(
                submission_token=self.payload["submission_token"]).count(),
            1,
        )

    def test_expired_stock_cannot_be_added(self):
        today = timezone.localdate()
        payload = dict(
            self.payload,
            date_received=(today - datetime.timedelta(days=10)).isoformat(),
            expiration_date=(today - datetime.timedelta(days=1)).isoformat(),
        )
        self.client.force_login(self.nurse)
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Expired stock cannot be added")
        self.assertEqual(VaccineInventory.objects.count(), 1)
        self.assertEqual(self.vaccine.movements.count(), 1)

    def test_expiration_must_not_precede_date_received(self):
        payload = dict(
            self.payload,
            date_received="2026-10-28", expiration_date="2026-10-01",
        )
        self.client.force_login(self.nurse)
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cannot be before the date received")
        self.assertEqual(VaccineInventory.objects.count(), 1)

    def test_add_stock_updates_only_the_selected_vaccine(self):
        other = VaccineInventory.objects.create(
            vaccine_name="Other Vaccine", brand="Other Brand",
            batch_number="OTHER-1",
            expiration_date=datetime.date(2028, 1, 1),
            quantity_received=7, quantity_available=7,
            source_supplier="Other Supplier",
            date_received=datetime.date(2026, 9, 1),
        )
        self.client.force_login(self.nurse)
        self.client.post(self.url, self.payload, follow=True)

        other.refresh_from_db()
        self.assertEqual(other.quantity_received, 7)
        self.assertEqual(other.quantity_available, 7)
        self.assertEqual(other.movements.count(), 0)
        self.assertEqual(VaccineInventory.objects.count(), 3)

    def test_expired_batches_are_not_selectable_for_administration(self):
        from doctor.forms import ClinicalAssessmentForm

        expired = VaccineInventory.objects.create(
            vaccine_name="Expired Vaccine", brand="Expired Brand",
            batch_number="EXP-1",
            expiration_date=(
                timezone.localdate() - datetime.timedelta(days=1)),
            quantity_received=5, quantity_available=5,
            source_supplier="Old Supplier",
            date_received=datetime.date(2026, 1, 1),
        )
        form = ClinicalAssessmentForm()
        selectable = form.fields["vaccine_inventory"].queryset
        self.assertIn(self.vaccine, selectable)
        self.assertNotIn(expired, selectable)

    def test_stock_out_rows_are_hidden_from_stock_transaction_history(self):
        stock_out = VaccineStockMovement.objects.create(
            vaccine=self.vaccine, movement_type="stock_out", quantity=2,
            reference=self.case.case_number, moved_by=self.nurse,
        )
        self.client.force_login(self.nurse)
        content = self.client.get(
            reverse("inventory:vaccine_detail", args=[self.vaccine.pk])
        ).content.decode()
        history = content[
            content.index("3. Stock Transaction History"):
            content.index("4. Vaccination Administration History")
        ]
        self.assertIn("Stock In", history)
        self.assertNotIn("Stock Out", history)
        self.assertTrue(
            VaccineStockMovement.objects.filter(pk=stock_out.pk).exists())

    def test_doctors_cannot_record_vaccine_stock(self):
        self.client.force_login(self.doctor)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard:index"))


class MedicineAddStockTests(InventoryTestCase):
    """Nurse medicine rows get vaccine-style actions plus delivery recording."""

    def setUp(self):
        super().setUp()
        self.add_url = reverse(
            "inventory:medicine_add_stock", args=[self.medicine.pk])
        self.detail_url = reverse(
            "inventory:medicine_detail", args=[self.medicine.pk])

    def _payload(self, **overrides):
        data = {
            "batch_number": "MED-2",
            "quantity_received": "25",
            "source_supplier": "Supplier B",
            "date_received": "2026-11-01",
            "time_received": "09:30",
            "expiration_date": "2027-12-31",
            "remarks": "New delivery",
            "submission_token": str(uuid.uuid4()),
        }
        data.update(overrides)
        return data

    def _messages(self, response):
        return [str(message) for message in response.context["messages"]]

    def test_nurse_sees_view_and_add_stock_buttons(self):
        self.client.force_login(self.nurse)
        content = self.client.get(reverse("inventory:medicine_list")).content.decode()
        self.assertIn(
            f'<a href="{self.detail_url}" class="btn-view-medicine">View <svg', content)
        self.assertIn("M5 12h14M13 6l6 6-6 6", content)
        self.assertIn(
            f'<a href="{self.add_url}" class="btn-add-stock-medicine">', content)
        self.assertIn("M12 5v14M5 12h14", content)
        self.assertIn("background-color: #157A61", content)
        self.assertIn("border: 1.5px solid #157A61", content)
        self.assertIn('class="medicine-actions"', content)

    def test_admin_keeps_the_legacy_view_without_add_stock(self):
        self.client.force_login(self.admin)
        content = self.client.get(reverse("inventory:medicine_list")).content.decode()
        self.assertIn(
            f'<a href="{self.detail_url}" class="btn-secondary btn-xs">View</a>',
            content)
        self.assertNotIn("btn-view-medicine", content)
        self.assertNotIn("btn-add-stock-medicine", content)
        self.assertNotIn(self.add_url, content)

    def test_add_stock_page_shows_selected_medicine_details(self):
        self.client.force_login(self.nurse)
        response = self.client.get(self.add_url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        for text in ("Back to Medicine Inventory", "Add New Stock",
                     "Record newly received medicine stock.",
                     "Selected Medicine Information"):
            self.assertIn(text, content)
        self.assertEqual(content.count('class="stock-readonly-card"'), 2)
        self.assertIn('<p class="stock-field-label">Medicine Name</p>', content)
        self.assertIn('<p class="stock-field-label">Generic Name</p>', content)
        self.assertIn("Amoxicillin 500 mg", content)
        self.assertIn("Amoxicillin trihydrate", content)
        self.assertIn("Capsule · Unit: capsule", content)
        for removed in ("Strength", "Dosage Form", "Unit"):
            self.assertNotIn(
                f'<p class="stock-field-label">{removed}</p>', content)
        for field in ("batch_number", "quantity_received", "source_supplier",
                      "date_received", "time_received", "expiration_date",
                      "remarks", "submission_token"):
            self.assertIn(f'name="{field}"', content)
        self.assertNotIn('name="medicine_name"', content)
        self.assertNotIn('name="dosage_form"', content)
        self.assertIn("Save Stock", content)
        self.assertIn("Cancel", content)

    def test_admin_can_open_the_add_stock_form(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.add_url).status_code, 200)

    def test_new_batch_creates_a_separate_stock_record(self):
        self.client.force_login(self.nurse)
        response = self.client.post(self.add_url, self._payload(), follow=True)
        self.assertIn("Stock added successfully.", self._messages(response))
        self.assertEqual(response.status_code, 200)

        self.medicine.refresh_from_db()
        self.assertEqual(self.medicine.quantity_received, 10)
        self.assertEqual(self.medicine.quantity_available, 10)

        batch = MedicineInventory.objects.get(
            medicine_name="Amoxicillin", batch_number="MED-2")
        self.assertEqual(batch.generic_name, "Amoxicillin trihydrate")
        self.assertEqual(batch.strength, "500 mg")
        self.assertEqual(batch.dosage_form, "capsule")
        self.assertEqual(batch.unit, "capsule")
        self.assertEqual(batch.reorder_level, 3)
        self.assertEqual(batch.quantity_received, 25)
        self.assertEqual(batch.quantity_available, 25)
        self.assertEqual(batch.quantity_dispensed, 0)
        self.assertEqual(batch.source_supplier, "Supplier B")
        self.assertEqual(batch.date_received, datetime.date(2026, 11, 1))
        self.assertEqual(batch.expiration_date, datetime.date(2027, 12, 31))

        movement = MedicineStockMovement.objects.get(medicine=batch)
        self.assertEqual(movement.movement_type, "stock_in")
        self.assertEqual(movement.quantity, 25)
        self.assertEqual(movement.supplier, "Supplier B")
        self.assertEqual(movement.date_received, datetime.date(2026, 11, 1))
        self.assertEqual(movement.time_received, datetime.time(9, 30))
        self.assertEqual(movement.batch_number, "MED-2")
        self.assertEqual(movement.expiration_date, datetime.date(2027, 12, 31))
        self.assertEqual(movement.notes, "New delivery")
        self.assertIsNotNone(movement.submission_token)

    def test_same_batch_tops_up_and_keeps_the_original_delivery(self):
        self.medicine.source_supplier = "Original Supplier"
        self.medicine.date_received = datetime.date(2026, 10, 1)
        self.medicine.quantity_dispensed = 2
        self.medicine.quantity_available = 8
        self.medicine.save()

        self.client.force_login(self.nurse)
        response = self.client.post(self.add_url, self._payload(
            batch_number="MED-1",
            quantity_received="5",
            expiration_date="2027-06-01",
            source_supplier="Second Supplier",
        ), follow=True)
        self.assertIn("Stock added successfully.", self._messages(response))

        self.medicine.refresh_from_db()
        self.assertEqual(self.medicine.quantity_received, 15)
        self.assertEqual(self.medicine.quantity_available, 13)
        self.assertEqual(self.medicine.quantity_dispensed, 2)
        self.assertEqual(self.medicine.source_supplier, "Original Supplier")
        self.assertEqual(self.medicine.date_received, datetime.date(2026, 10, 1))
        self.assertEqual(
            MedicineInventory.objects.filter(batch_number="MED-1").count(), 1)

        movement = MedicineStockMovement.objects.get(medicine=self.medicine)
        self.assertEqual(movement.quantity, 5)
        self.assertEqual(movement.supplier, "Second Supplier")
        self.assertEqual(movement.date_received, datetime.date(2026, 11, 1))
        self.assertEqual(movement.time_received, datetime.time(9, 30))
        self.assertEqual(movement.batch_number, "MED-1")
        self.assertEqual(movement.expiration_date, datetime.date(2027, 6, 1))

    def test_time_received_is_required(self):
        payload = self._payload()
        payload.pop("time_received")
        self.client.force_login(self.nurse)
        response = self.client.post(self.add_url, payload)
        self.assertContains(response, "This field is required.")
        self.assertEqual(MedicineInventory.objects.count(), 1)
        self.assertFalse(
            MedicineStockMovement.objects.filter(medicine=self.medicine).exists())

    def test_same_batch_number_for_a_different_dosage_form_stays_separate(self):
        other_form = MedicineInventory.objects.create(
            medicine_name="Amoxicillin", generic_name="Amoxicillin trihydrate",
            strength="500 mg", dosage_form="tablet", unit="tablet",
            batch_number="MED-1", expiration_date=datetime.date(2027, 6, 1),
            quantity_received=8, quantity_available=8, reorder_level=3,
        )
        self.client.force_login(self.nurse)
        response = self.client.post(self.add_url, self._payload(
            batch_number="MED-1", quantity_received="5",
            expiration_date="2027-06-01"), follow=True)
        self.assertIn("Stock added successfully.", self._messages(response))

        other_form.refresh_from_db()
        self.assertEqual(other_form.quantity_received, 8)
        self.assertEqual(other_form.quantity_available, 8)
        self.medicine.refresh_from_db()
        self.assertEqual(self.medicine.quantity_received, 15)

    def test_top_up_prefers_the_selected_batch_row(self):
        duplicate = MedicineInventory.objects.create(
            medicine_name="Amoxicillin", generic_name="Amoxicillin trihydrate",
            strength="500 mg", dosage_form="capsule", unit="capsule",
            batch_number="MED-1", expiration_date=datetime.date(2027, 6, 1),
            quantity_received=10, quantity_available=10, reorder_level=3,
        )
        self.client.force_login(self.nurse)
        response = self.client.post(self.add_url, self._payload(
            batch_number="MED-1", quantity_received="5",
            expiration_date="2027-06-01"), follow=True)
        self.assertIn("Stock added successfully.", self._messages(response))

        self.medicine.refresh_from_db()
        duplicate.refresh_from_db()
        self.assertEqual(self.medicine.quantity_received, 15)
        self.assertEqual(self.medicine.quantity_available, 15)
        self.assertEqual(duplicate.quantity_received, 10)
        self.assertEqual(duplicate.quantity_available, 10)

    def test_same_batch_with_a_different_expiry_is_blocked(self):
        self.client.force_login(self.nurse)
        response = self.client.post(self.add_url, self._payload(
            batch_number="MED-1", expiration_date="2027-07-01"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "different expiration date")
        self.medicine.refresh_from_db()
        self.assertEqual(self.medicine.quantity_received, 10)
        self.assertEqual(
            MedicineInventory.objects.filter(batch_number="MED-1").count(), 1)
        self.assertFalse(
            MedicineStockMovement.objects.filter(medicine=self.medicine).exists())

    def test_same_batch_number_for_a_different_strength_stays_separate(self):
        other_strength = MedicineInventory.objects.create(
            medicine_name="Amoxicillin", generic_name="Amoxicillin trihydrate",
            strength="250 mg", dosage_form="capsule", unit="capsule",
            batch_number="MED-1", expiration_date=datetime.date(2027, 6, 1),
            quantity_received=12, quantity_available=12, reorder_level=3,
        )
        self.client.force_login(self.nurse)
        response = self.client.post(self.add_url, self._payload(
            batch_number="MED-1", expiration_date="2027-06-01"), follow=True)
        self.assertIn("Stock added successfully.", self._messages(response))

        other_strength.refresh_from_db()
        self.assertEqual(other_strength.quantity_received, 12)
        self.assertEqual(other_strength.quantity_available, 12)
        self.medicine.refresh_from_db()
        self.assertEqual(self.medicine.quantity_received, 35)
        self.assertEqual(
            MedicineInventory.objects.filter(
                medicine_name="Amoxicillin", batch_number="MED-1").count(), 2)

    def test_expired_or_out_of_order_dates_are_rejected(self):
        self.client.force_login(self.nurse)
        response = self.client.post(self.add_url, self._payload(
            date_received="2027-01-10", expiration_date="2027-01-09"))
        self.assertContains(
            response, "Expiration date cannot be before the date received.")
        response = self.client.post(self.add_url, self._payload(
            date_received="2026-10-01", expiration_date="2026-10-15"))
        self.assertContains(response, "Expired stock cannot be added.")
        self.assertEqual(MedicineInventory.objects.count(), 1)
        self.assertFalse(
            MedicineStockMovement.objects.filter(medicine=self.medicine).exists())

    def test_duplicate_submission_does_not_double_the_stock(self):
        token = str(uuid.uuid4())
        payload = self._payload(submission_token=token)
        self.client.force_login(self.nurse)
        first = self.client.post(self.add_url, payload, follow=True)
        self.assertIn("Stock added successfully.", self._messages(first))
        second = self.client.post(self.add_url, payload, follow=True)
        self.assertIn(
            "This stock delivery was already recorded. Inventory was not changed.",
            self._messages(second))

        batch = MedicineInventory.objects.get(batch_number="MED-2")
        self.assertEqual(batch.quantity_available, 25)
        self.assertEqual(
            MedicineStockMovement.objects.filter(submission_token=token).count(), 1)

    def test_identity_is_copied_from_the_selected_medicine(self):
        self.client.force_login(self.nurse)
        self.client.post(self.add_url, self._payload(
            medicine_name="Forged Medicine", strength="999 mg",
            dosage_form="tablet", unit="box"), follow=True)
        self.assertFalse(
            MedicineInventory.objects.filter(medicine_name="Forged Medicine").exists())
        batch = MedicineInventory.objects.get(batch_number="MED-2")
        self.assertEqual(batch.medicine_name, "Amoxicillin")
        self.assertEqual(batch.strength, "500 mg")
        self.assertEqual(batch.dosage_form, "capsule")
        self.assertEqual(batch.unit, "capsule")

    def test_doctors_and_patients_cannot_record_medicine_stock(self):
        self.client.force_login(self.doctor)
        response = self.client.get(self.add_url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard:index"))
        response = self.client.post(self.add_url, self._payload(), follow=True)
        self.assertIn(
            "Not authorized to add medicine stock.", self._messages(response))

        self.client.force_login(self.patient_user)
        self.assertNotEqual(self.client.get(self.add_url).status_code, 200)
        self.client.post(self.add_url, self._payload())
        self.assertFalse(MedicineInventory.objects.filter(batch_number="MED-2").exists())


class MedicineInventoryDetailRedesignTests(InventoryTestCase):
    """Nurse medicine detail mirrors the vaccine view with real stock records."""

    def setUp(self):
        super().setUp()
        self.medicine.source_supplier = "DOH Region IX"
        self.medicine.date_received = datetime.date(2026, 10, 1)
        self.medicine.remarks = "Store dry."
        self.medicine.save()

        MedicineStockMovement.objects.create(
            medicine=self.medicine, movement_type="stock_in", quantity=10,
            supplier="DOH Region IX",
            date_received=datetime.date(2026, 10, 1),
            batch_number="MED-1",
            expiration_date=datetime.date(2027, 6, 1),
            reference="PO-2026-01", moved_by=self.nurse,
        )

        self.second = MedicineInventory.objects.create(
            medicine_name="Amoxicillin", generic_name="Amoxicillin trihydrate",
            strength="500 mg", dosage_form="capsule", unit="capsule",
            batch_number="MED-2", expiration_date=datetime.date(2027, 9, 1),
            quantity_received=15, quantity_available=12, quantity_dispensed=3,
            reorder_level=3, source_supplier="ABC Pharma",
            date_received=datetime.date(2026, 10, 20),
        )
        MedicineStockMovement.objects.create(
            medicine=self.second, movement_type="stock_in", quantity=15,
            supplier="ABC Pharma",
            date_received=datetime.date(2026, 10, 20),
            batch_number="MED-2",
            expiration_date=datetime.date(2027, 9, 1),
            reference="PO-2026-02", moved_by=self.nurse,
        )

        self.dispensed_rx = self._prescription(
            quantity=3, medicine=self.second, status="dispensed")
        self.dispensed_rx.dispensed_at = timezone.now()
        self.dispensed_rx.dispensed_by = self.nurse
        self.dispensed_rx.save(update_fields=["dispensed_at", "dispensed_by"])
        MedicineStockMovement.objects.create(
            medicine=self.second, movement_type="dispense", quantity=3,
            reference=self.case.case_number, moved_by=self.nurse,
            notes=f"Prescription #{self.dispensed_rx.pk}",
        )

        self.pending_rx = self._prescription(
            quantity=2, medicine=self.medicine, status="pending")

        self.other_strength = MedicineInventory.objects.create(
            medicine_name="Amoxicillin", generic_name="Amoxicillin trihydrate",
            strength="250 mg", dosage_form="capsule", unit="capsule",
            batch_number="MED-250", expiration_date=datetime.date(2027, 9, 1),
            quantity_received=100, quantity_available=100, reorder_level=3,
        )
        self.other_form = MedicineInventory.objects.create(
            medicine_name="Amoxicillin", generic_name="Amoxicillin trihydrate",
            strength="500 mg", dosage_form="tablet", unit="tablet",
            batch_number="MED-TAB", expiration_date=datetime.date(2027, 9, 1),
            quantity_received=100, quantity_available=100, reorder_level=3,
        )
        Prescription.objects.create(
            assessment=self.assessment, patient=self.patient, bite_case=self.case,
            medicine=self.other_strength, dose="Excluded Dose",
            quantity=9, status="pending",
        )

        self.url = reverse("inventory:medicine_detail", args=[self.medicine.pk])

    def test_nurse_detail_uses_the_four_section_layout(self):
        self.client.force_login(self.nurse)
        response = self.client.get(self.url)
        self.assertContains(response, "Medicine Inventory Details")
        self.assertContains(response, "Capsule · Unit: capsule")
        self.assertContains(response, "Amoxicillin 500 mg")
        content = response.content.decode()
        headings = (
            "1. Medicine Information",
            "2. Stock Summary",
            "3. Stock Transaction History",
            "4. Dispensing History",
        )
        positions = [content.index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions))
        self.assertNotContains(response, "Stock Movement History")

    def test_medicine_information_shows_only_name_and_generic_name(self):
        self.client.force_login(self.nurse)
        content = self.client.get(self.url).content.decode()
        info = content[
            content.index("1. Medicine Information"):
            content.index("2. Stock Summary")
        ]
        self.assertIn("Medicine Name", info)
        self.assertIn("Generic / Brand Name", info)
        self.assertIn("Amoxicillin 500 mg", info)
        self.assertIn("Amoxicillin trihydrate", info)
        for hidden in ("Lot / Batch Number", "Batch Number", "Supplier",
                       "Date Received", "Expiration Date", "MED-1",
                       "DOH Region IX", "Store dry."):
            self.assertNotIn(hidden, info)

    def test_stock_summary_totals_only_matching_batches(self):
        self.client.force_login(self.nurse)
        content = self.client.get(self.url).content.decode()
        summary = content[
            content.index("2. Stock Summary"):
            content.index("3. Stock Transaction History")
        ]
        values = re.findall(r'vd-stock-value[^>]*>\s*(\d+)\s*<', summary)
        self.assertEqual(values, ["25", "3", "22"])
        self.assertNotIn("100", summary)

    def test_stock_history_columns_are_ordered_with_type_last(self):
        self.client.force_login(self.nurse)
        content = self.client.get(self.url).content.decode()
        history = content[
            content.index("3. Stock Transaction History"):
            content.index("4. Dispensing History")
        ]
        headers = re.findall(
            r'<th class="table-header">([^<]+)</th>', history)
        self.assertEqual(headers, [
            "Date Received", "Time", "Supplier", "Batch Number",
            "Expiration Date", "Quantity", "Type",
        ])
        for width in ("width: 16%", "width: 11%", "width: 17%",
                      "width: 16%", "width: 18%", "width: 10%",
                      "width: 12%"):
            self.assertIn(width, content)
        for rule in ("padding: 17px 15px", "vertical-align: middle",
                     "text-align: right", "text-align: center"):
            self.assertIn(rule, content)

    def test_stock_history_shows_date_received_and_transaction_time_separately(self):
        movement = MedicineStockMovement.objects.get(
            medicine=self.medicine, movement_type="stock_in")
        movement.time_received = datetime.time(9, 30)
        movement.save(update_fields=['time_received'])

        self.client.force_login(self.nurse)
        content = self.client.get(self.url).content.decode()
        history = content[
            content.index("3. Stock Transaction History"):
            content.index("4. Dispensing History")
        ]
        row = next(
            r for r in re.findall(r"<tr[^>]*>(.*?)</tr>", history, re.S)
            if "Stock In" in r and "MED-1" in r)
        cells = [
            re.sub(r'<[^>]+>', '', cell).strip()
            for cell in re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)
        ]
        self.assertEqual(cells[0], "Oct 01, 2026")
        self.assertEqual(cells[1], "09:30 AM")

    def test_historical_stock_in_without_receiving_time_shows_a_dash(self):
        self.client.force_login(self.nurse)
        content = self.client.get(self.url).content.decode()
        history = content[
            content.index("3. Stock Transaction History"):
            content.index("4. Dispensing History")
        ]
        row = next(
            r for r in re.findall(r"<tr[^>]*>(.*?)</tr>", history, re.S)
            if "Stock In" in r and "MED-1" in r)
        cells = [
            re.sub(r'<[^>]+>', '', cell).strip()
            for cell in re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)
        ]
        self.assertIsNone(MedicineStockMovement.objects.get(
            medicine=self.medicine, movement_type="stock_in").time_received)
        self.assertEqual(cells[1], "—")

    def test_dispensed_rows_are_hidden_from_stock_transaction_history(self):
        dispensed = MedicineStockMovement.objects.get(
            medicine=self.second, movement_type="dispense")
        self.client.force_login(self.nurse)
        content = self.client.get(self.url).content.decode()
        history = content[
            content.index("3. Stock Transaction History"):
            content.index("4. Dispensing History")
        ]
        self.assertIn("Stock In", history)
        self.assertNotIn("Dispensed", history)
        self.assertTrue(
            MedicineStockMovement.objects.filter(pk=dispensed.pk).exists())

    def test_stock_history_shows_only_stock_in_transactions(self):
        self.client.force_login(self.nurse)
        content = self.client.get(self.url).content.decode()
        history = content[
            content.index("3. Stock Transaction History"):
            content.index("4. Dispensing History")
        ]
        rows = [
            row for row in re.findall(r"<tr[^>]*>(.*?)</tr>", history, re.S)
            if "<td" in row
        ]
        self.assertEqual(len(rows), 2)
        stock_in = next(
            row for row in rows if "Stock In" in row and "MED-1" in row)
        for value in ("+10", "DOH Region IX", "MED-1",
                      "Oct 01, 2026", "Jun 01, 2027"):
            self.assertIn(value, stock_in)
        self.assertFalse(any("Dispensed" in row for row in rows))
        self.assertFalse(any("-3" in row for row in rows))

    def test_stock_history_hides_reference_and_recorded_by(self):
        self.client.force_login(self.nurse)
        content = self.client.get(self.url).content.decode()
        history = content[
            content.index("3. Stock Transaction History"):
            content.index("4. Dispensing History")
        ]
        for hidden in ("Reference", "Recorded By", "PO-2026-01", "med.nurse"):
            self.assertNotIn(hidden, history)
        movements = MedicineStockMovement.objects.filter(
            medicine__in=[self.medicine, self.second])
        self.assertEqual(
            movements.filter(reference="PO-2026-01").count(), 1)
        self.assertTrue(all(m.moved_by == self.nurse for m in movements))

    def test_dispensing_history_shows_confirmed_and_pending_records(self):
        self.client.force_login(self.nurse)
        content = self.client.get(self.url).content.decode()
        dispensing = content[content.index("4. Dispensing History"):]
        for value in ("Mia Stock", "ABC-M0001", "1 capsule", "2 capsule",
                      "3 capsule", "Pending"):
            self.assertIn(value, dispensing)
        for header in ("Patient", "Case", "Dosage", "Qty",
                       "Date Dispensed", "Time"):
            self.assertIn(header, dispensing)
        for width in ("width: 21%", "width: 13%", "width: 27%",
                      "width: 9%", "width: 19%"):
            self.assertIn(width, content)
        self.assertNotIn("med.nurse", dispensing)
        self.assertNotIn("Dispensed By", dispensing)
        self.assertNotIn("Excluded Dose", dispensing)

    def test_dispensing_history_splits_date_and_time_and_hides_staff(self):
        dispensed_at = timezone.localtime(self.dispensed_rx.dispensed_at)
        self.client.force_login(self.nurse)
        content = self.client.get(self.url).content.decode()
        dispensing = content[content.index("4. Dispensing History"):]
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", dispensing, re.S)
        row = next(r for r in rows if "3 capsule" in r)
        cells = [
            re.sub(r'<[^>]+>', '', cell).strip()
            for cell in re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)
        ]
        self.assertEqual(cells[4], dispensed_at.strftime('%b %d, %Y'))
        self.assertEqual(cells[5], dispensed_at.strftime('%I:%M %p'))

        pending_row = next(r for r in rows if "2 capsule" in r)
        pending_cells = [
            re.sub(r'<[^>]+>', '', cell).strip()
            for cell in re.findall(r'<td[^>]*>(.*?)</td>', pending_row, re.S)
        ]
        self.assertIn("Pending", pending_cells[4])
        self.assertEqual(pending_cells[5], "—")

    def test_pending_requests_do_not_reduce_stock_or_count_as_dispensed(self):
        self.medicine.refresh_from_db()
        self.assertEqual(self.medicine.quantity_available, 10)
        self.assertEqual(self.medicine.quantity_dispensed, 0)
        self.pending_rx.refresh_from_db()
        self.assertEqual(self.pending_rx.status, "pending")
        self.assertFalse(MedicineStockMovement.objects.filter(
            medicine=self.medicine, movement_type="dispense").exists())

    def test_confirmed_dispensing_deducts_once_and_stays_out_of_stock_history(self):
        self.client.force_login(self.nurse)
        dispense_url = reverse(
            "inventory:prescription_dispense", args=[self.pending_rx.pk])
        self.client.post(dispense_url, follow=True)
        self.client.post(dispense_url, follow=True)

        self.medicine.refresh_from_db()
        self.assertEqual(self.medicine.quantity_available, 8)
        self.assertEqual(self.medicine.quantity_dispensed, 2)
        self.assertEqual(MedicineStockMovement.objects.filter(
            medicine=self.medicine, movement_type="dispense").count(), 1)

        content = self.client.get(self.url).content.decode()
        summary = content[
            content.index("2. Stock Summary"):
            content.index("3. Stock Transaction History")
        ]
        self.assertEqual(
            re.findall(r'vd-stock-value[^>]*>\s*(\d+)\s*<', summary),
            ["25", "5", "20"],
        )
        history = content[
            content.index("3. Stock Transaction History"):
            content.index("4. Dispensing History")
        ]
        rows = [
            row for row in re.findall(r"<tr[^>]*>(.*?)</tr>", history, re.S)
            if "<td" in row
        ]
        self.assertFalse(any("Dispensed" in row for row in rows))

    def test_later_deliveries_keep_their_own_supplier_and_date(self):
        self.client.force_login(self.nurse)
        self.client.post(
            reverse("inventory:medicine_add_stock", args=[self.medicine.pk]),
            {
                "batch_number": "MED-1",
                "quantity_received": "5",
                "source_supplier": "Second Supplier",
                "date_received": "2026-11-02",
                "time_received": "09:30",
                "expiration_date": "2027-06-01",
                "remarks": "Top-up delivery",
                "submission_token": str(uuid.uuid4()),
            },
            follow=True,
        )
        self.medicine.refresh_from_db()
        self.assertEqual(self.medicine.source_supplier, "DOH Region IX")
        self.assertEqual(self.medicine.date_received, datetime.date(2026, 10, 1))

        content = self.client.get(self.url).content.decode()
        history = content[
            content.index("3. Stock Transaction History"):
            content.index("4. Dispensing History")
        ]
        for value in ("DOH Region IX", "Oct 01, 2026",
                      "Second Supplier", "Nov 02, 2026", "09:30 AM", "+5"):
            self.assertIn(value, history)

    def test_empty_states_when_no_records_exist(self):
        empty = MedicineInventory.objects.create(
            medicine_name="Empty Medicine", generic_name="",
            strength="10 mg", dosage_form="tablet", unit="tablet",
            batch_number="MED-EMPTY",
            expiration_date=datetime.date(2027, 1, 1),
            quantity_received=0, quantity_available=0,
        )
        self.client.force_login(self.nurse)
        response = self.client.get(
            reverse("inventory:medicine_detail", args=[empty.pk]))
        self.assertContains(response, "No stock receiving history recorded yet.")
        self.assertContains(response, "No dispensing history recorded yet.")

    def test_nurse_detail_is_read_only(self):
        self.client.force_login(self.nurse)
        response = self.client.get(self.url)
        self.assertNotContains(response, "Delete")
        self.assertNotContains(response, "Add Stock")
        self.assertNotContains(response, "Save Stock")
        self.assertNotContains(response, reverse("inventory:medicine_create"))
        self.assertNotContains(
            response, reverse("inventory:medicine_add_stock", args=[self.medicine.pk]))

    def test_admin_detail_keeps_the_existing_layout(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.url)
        self.assertContains(response, "Stock Transaction History")
        self.assertContains(response, "Medicine Information")
        self.assertNotContains(response, "Medicine Inventory Details")
        self.assertNotContains(response, "4. Dispensing History")


class MedicineProductGroupingTests(InventoryTestCase):
    """Nurse medicine listing shows one row per distinct medicine product."""

    def setUp(self):
        super().setUp()
        common = {
            "medicine_name": "Amoxicillin",
            "generic_name": "Amoxicillin trihydrate",
            "strength": "500 mg",
            "dosage_form": "capsule",
            "unit": "capsule",
        }
        self.batch2 = MedicineInventory.objects.create(
            **common, batch_number="MED-2",
            expiration_date=datetime.date(2027, 9, 1),
            quantity_received=100, quantity_available=90,
            quantity_dispensed=10, reorder_level=3,
            source_supplier="Fb Pharmacy",
            date_received=datetime.date(2026, 10, 20),
        )
        self.batch3 = MedicineInventory.objects.create(
            **common, batch_number="MED-3",
            expiration_date=datetime.date(2027, 8, 15),
            quantity_received=20, quantity_available=20,
            reorder_level=10,
        )
        self.other_strength = MedicineInventory.objects.create(
            **{**common, "strength": "250 mg"},
            batch_number="MED-250",
            expiration_date=datetime.date(2027, 9, 1),
            quantity_received=50, quantity_available=50,
        )
        self.other_form = MedicineInventory.objects.create(
            **{**common, "dosage_form": "tablet", "unit": "tablet"},
            batch_number="MED-TAB",
            expiration_date=datetime.date(2027, 9, 1),
            quantity_received=30, quantity_available=30,
        )
        self.other_unit = MedicineInventory.objects.create(
            **{**common, "unit": "pack"},
            batch_number="MED-PACK",
            expiration_date=datetime.date(2027, 9, 1),
            quantity_received=5, quantity_available=5,
        )
        self.list_url = reverse("inventory:medicine_list")

    def _rows(self, content):
        rows = []
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", content, re.S):
            cells = [
                re.sub(r'<[^>]+>', '', cell).strip()
                for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
            ]
            if len(cells) == 8:
                cells[0] = ' '.join(cells[0].split())
                rows.append(cells)
        return rows

    def _product_row(self, content, strength, form="Capsule", unit="capsule"):
        for cells in self._rows(content):
            if (cells[0].startswith(f'Amoxicillin {strength}')
                    and cells[1] == form and cells[2] == unit):
                return cells
        raise AssertionError(
            f'No grouped row for Amoxicillin {strength} / {form} / {unit}')

    def test_nurse_list_shows_one_row_per_medicine_product(self):
        self.client.force_login(self.nurse)
        content = self.client.get(self.list_url).content.decode()
        rows = self._rows(content)
        self.assertEqual(len(rows), 4)
        self.assertEqual(
            len([row for row in rows
                 if row[0].startswith('Amoxicillin 500 mg')
                 and row[1] == 'Capsule' and row[2] == 'capsule']), 1)
        self.assertNotIn(">MED-2<", content)
        self.assertNotIn(">MED-3<", content)
        self.assertIn("3 Batches", content)
        self.assertIn("1 Batch", content)

    def test_group_sums_available_and_labels_multiple_expiries(self):
        self.client.force_login(self.nurse)
        content = self.client.get(self.list_url).content.decode()
        cells = self._product_row(content, "500 mg")
        self.assertEqual(cells[3], "3 Batches")
        self.assertEqual(cells[4], "120")
        self.assertEqual(cells[5], "Multiple Expiries")
        self.assertEqual(cells[6], "Available")
        self.assertIn("MED-3:", content)
        self.assertNotIn("Batch Batch", content)

    def test_expired_batches_are_excluded_from_available_stock(self):
        MedicineInventory.objects.filter(pk=self.batch2.pk).update(
            expiration_date=datetime.date(2025, 1, 1))
        self.client.force_login(self.nurse)
        content = self.client.get(self.list_url).content.decode()
        cells = self._product_row(content, "500 mg")
        self.assertEqual(cells[3], "3 Batches")
        self.assertEqual(cells[4], "30")
        self.assertEqual(cells[6], "Available")

    def test_fully_expired_product_is_marked_expired(self):
        MedicineInventory.objects.filter(
            medicine_name="Amoxicillin", strength="500 mg",
            dosage_form="capsule", unit="capsule").update(
            expiration_date=datetime.date(2025, 1, 1))
        self.client.force_login(self.nurse)
        content = self.client.get(self.list_url).content.decode()
        cells = self._product_row(content, "500 mg")
        self.assertEqual(cells[4], "0")
        self.assertEqual(cells[5], "Expired")
        self.assertEqual(cells[6], "Expired")

    def test_product_status_uses_aggregate_stock_levels(self):
        MedicineInventory.objects.filter(
            medicine_name="Amoxicillin", strength="500 mg",
            dosage_form="capsule", unit="capsule").update(
            quantity_available=0)
        self.client.force_login(self.nurse)
        content = self.client.get(self.list_url).content.decode()
        self.assertEqual(self._product_row(content, "500 mg")[6], "Out of Stock")

        MedicineInventory.objects.filter(
            medicine_name="Amoxicillin", strength="500 mg",
            dosage_form="capsule", unit="capsule").update(
            quantity_available=1)
        content = self.client.get(self.list_url).content.decode()
        self.assertEqual(self._product_row(content, "500 mg")[6], "Low Stock")

    def test_strength_form_and_unit_keep_products_separate(self):
        self.client.force_login(self.nurse)
        content = self.client.get(self.list_url).content.decode()
        strength = self._product_row(content, "250 mg")
        tablet = self._product_row(content, "500 mg", "Tablet", "tablet")
        pack = self._product_row(content, "500 mg", "Capsule", "pack")
        for cells in (strength, tablet, pack):
            self.assertEqual(cells[3], "1 Batch")
        self.assertEqual(strength[4], "50")
        self.assertEqual(tablet[4], "30")
        self.assertEqual(pack[4], "5")
        self.assertEqual(len(self._rows(content)), 4)

    def test_search_matches_product_details_and_batch_numbers(self):
        self.client.force_login(self.nurse)
        by_batch = self.client.get(self.list_url, {"q": "MED-3"})
        content = by_batch.content.decode()
        self.assertEqual(len(self._rows(content)), 1)
        self.assertIn("3 Batches", content)
        self.assertNotIn("250 mg", content)

        by_form = self.client.get(self.list_url, {"q": "tablet"})
        rows = self._rows(by_form.content.decode())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "Tablet")

        by_generic = self.client.get(self.list_url, {"q": "trihydrate"})
        self.assertEqual(len(self._rows(by_generic.content.decode())), 4)

    def test_add_stock_adds_a_batch_without_duplicating_the_product(self):
        self.client.force_login(self.nurse)
        response = self.client.post(
            reverse("inventory:medicine_add_stock", args=[self.medicine.pk]),
            {
                "batch_number": "MED-4",
                "quantity_received": "30",
                "source_supplier": "ABC Pharma",
                "date_received": "2026-11-03",
                "time_received": "09:30",
                "expiration_date": "2027-12-31",
                "remarks": "New batch",
                "submission_token": str(uuid.uuid4()),
            },
            follow=True,
        )
        self.assertContains(response, "Stock added successfully.")
        self.assertEqual(
            MedicineInventory.objects.filter(
                medicine_name="Amoxicillin", strength="500 mg",
                dosage_form="capsule", unit="capsule").count(), 4)

        content = self.client.get(self.list_url).content.decode()
        self.assertEqual(len(self._rows(content)), 4)
        cells = self._product_row(content, "500 mg")
        self.assertEqual(cells[3], "4 Batches")
        self.assertEqual(cells[4], "150")

        detail = self.client.get(self.detail_url())
        self.assertContains(detail, "MED-4")
        self.assertContains(detail, "09:30 AM")
        self.assertContains(detail, "160")
        self.assertContains(detail, "150")

    def detail_url(self):
        return reverse("inventory:medicine_detail", args=[self.medicine.pk])

    def test_detail_excludes_expired_batches_from_available(self):
        MedicineInventory.objects.filter(pk=self.batch2.pk).update(
            expiration_date=datetime.date(2025, 1, 1))
        self.client.force_login(self.nurse)
        content = self.client.get(self.detail_url()).content.decode()
        summary = content[
            content.index("2. Stock Summary"):
            content.index("3. Stock Transaction History")
        ]
        self.assertEqual(
            re.findall(r'vd-stock-value[^>]*>\s*(\d+)\s*<', summary),
            ["130", "10", "30"],
        )

    def test_group_actions_open_the_product_detail_and_add_stock(self):
        self.client.force_login(self.nurse)
        content = self.client.get(self.list_url).content.decode()
        self.assertIn(
            reverse("inventory:medicine_detail", args=[self.medicine.pk]),
            content)
        self.assertIn(
            reverse("inventory:medicine_add_stock", args=[self.medicine.pk]),
            content)

    def test_admin_list_still_shows_each_batch(self):
        self.client.force_login(self.admin)
        content = self.client.get(self.list_url).content.decode()
        rows = self._rows(content)
        self.assertEqual(len(rows), 6)
        for number in ("MED-1", "MED-2", "MED-3"):
            self.assertIn(number, content)


class InventoryNotificationTests(InventoryTestCase):
    """Automatic low-stock, expiry and out-of-stock alerts for the nurse portal."""

    def _add_vaccine(self, name="Imovax", brand="Sanofi", batch="VX-1",
                     days=90, qty=20, reorder=5):
        return VaccineInventory.objects.create(
            vaccine_name=name, brand=brand, batch_number=batch,
            expiration_date=timezone.localdate() + datetime.timedelta(days=days),
            quantity_received=qty, quantity_available=qty,
            reorder_level=reorder,
        )

    def _kinds(self, alerts):
        return [alert['kind'] for alert in alerts]

    def test_vaccine_expiring_soon_alert_identifies_the_batch(self):
        batch = self._add_vaccine(days=10, qty=16)
        alerts = [alert for alert in vaccine_alerts()
                  if alert['kind'] == 'expiring']
        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertEqual(alert['module'], 'vaccine')
        self.assertEqual(alert['batch'], batch.batch_number)
        self.assertEqual(alert['days_left'], 10)
        self.assertIn(batch.expiration_date.strftime('%b %d, %Y'),
                      alert['message'])
        self.assertIn('?batch=VX-1', alert['url'])

    def test_medicine_expiring_soon_alert_identifies_the_batch(self):
        self.medicine.expiration_date = (
            timezone.localdate() + datetime.timedelta(days=12))
        self.medicine.save(update_fields=['expiration_date'])
        alerts = [alert for alert in medicine_alerts()
                  if alert['kind'] == 'expiring']
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]['batch'], 'MED-1')
        self.assertEqual(alerts[0]['days_left'], 12)

    def test_expiry_warning_threshold_is_configurable(self):
        self.medicine.expiration_date = (
            timezone.localdate() + datetime.timedelta(days=45))
        self.medicine.save(update_fields=['expiration_date'])
        self.assertNotIn('expiring', self._kinds(medicine_alerts()))

        SystemSetting.objects.filter(key=EXPIRY_SETTING_KEY).update(value='60')
        self.assertIn('expiring', self._kinds(medicine_alerts()))

    def test_low_stock_aggregates_batches_once(self):
        self.medicine.quantity_available = 2
        self.medicine.reorder_level = 5
        self.medicine.save(update_fields=['quantity_available', 'reorder_level'])
        MedicineInventory.objects.create(
            medicine_name="Amoxicillin", generic_name="Amoxicillin trihydrate",
            strength="500 mg", dosage_form="capsule", unit="capsule",
            batch_number="MED-2",
            expiration_date=timezone.localdate() + datetime.timedelta(days=120),
            quantity_received=3, quantity_available=3, reorder_level=5,
        )
        low = [alert for alert in medicine_alerts()
               if alert['kind'] == 'low_stock']
        self.assertEqual(len(low), 1)
        self.assertEqual(low[0]['quantity'], 5)
        self.assertEqual(low[0]['threshold'], 5)
        self.assertEqual(low[0]['batch'], '')

    def test_out_of_stock_ignores_expired_stock(self):
        self.medicine.quantity_available = 0
        self.medicine.save(update_fields=['quantity_available'])
        MedicineInventory.objects.create(
            medicine_name="Amoxicillin", generic_name="Amoxicillin trihydrate",
            strength="500 mg", dosage_form="capsule", unit="capsule",
            batch_number="MED-OLD",
            expiration_date=timezone.localdate() - datetime.timedelta(days=2),
            quantity_received=7, quantity_available=7, reorder_level=3,
        )
        alerts = medicine_alerts()
        kinds = self._kinds(alerts)
        self.assertIn('out_of_stock', kinds)
        self.assertIn('expired', kinds)
        self.assertNotIn('low_stock', kinds)
        out = next(a for a in alerts if a['kind'] == 'out_of_stock')
        self.assertEqual(out['quantity'], 0)

    def test_expired_batch_with_remaining_stock_links_to_the_batch(self):
        batch = self._add_vaccine(batch="OLD-1", days=-4, qty=12)
        VaccineStockMovement.objects.create(
            vaccine=batch, movement_type='stock_in', quantity=12,
            batch_number="OLD-1", expiration_date=batch.expiration_date,
            moved_by=self.nurse,
        )
        alert = next(a for a in vaccine_alerts() if a['kind'] == 'expired')
        self.assertEqual(alert['batch'], 'OLD-1')
        self.assertEqual(alert['quantity'], 12)
        self.assertIn('?batch=OLD-1', alert['url'])

        self.client.force_login(self.nurse)
        response = self.client.get(alert['url'])
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'vd-row-highlight')
        self.assertContains(response, 'OLD-1')

    def test_alerts_clear_when_stock_is_restored(self):
        batch = self._add_vaccine(qty=2, reorder=5)
        self.assertIn('low_stock', self._kinds(vaccine_alerts()))
        batch.quantity_available = 50
        batch.save(update_fields=['quantity_available'])
        self.assertNotIn('low_stock', self._kinds(vaccine_alerts()))

        batch.quantity_available = 0
        batch.save(update_fields=['quantity_available'])
        self.assertIn('out_of_stock', self._kinds(vaccine_alerts()))
        batch.quantity_available = 50
        batch.save(update_fields=['quantity_available'])
        self.assertNotIn('out_of_stock', self._kinds(vaccine_alerts()))

    def test_vaccine_low_stock_uses_the_product_threshold(self):
        batch = self._add_vaccine(qty=8, reorder=10)
        self.assertIn('low_stock', self._kinds(vaccine_alerts()))
        batch.reorder_level = 5
        batch.save(update_fields=['reorder_level'])
        self.assertNotIn('low_stock', self._kinds(vaccine_alerts()))

    def test_bell_combines_modules_and_pages_are_scoped(self):
        self._add_vaccine(qty=2, reorder=5)
        self.medicine.quantity_available = 2
        self.medicine.save(update_fields=['quantity_available'])
        self.client.force_login(self.nurse)

        response = self.client.get(reverse('inventory:vaccine_list'))
        self.assertEqual(response.context['inventory_alert_total'], 2)
        self.assertEqual(
            {alert['module'] for alert in response.context['alerts']},
            {'vaccine'})
        content = response.content.decode()
        self.assertIn('aria-label="Inventory Alerts', content)
        self.assertIn('Inventory Alerts', content)
        self.assertIn('Amoxicillin', content)
        self.assertIn('Imovax', content)

        response = self.client.get(reverse('inventory:medicine_list'))
        self.assertEqual(response.context['inventory_alert_total'], 2)
        self.assertEqual(
            {alert['module'] for alert in response.context['alerts']},
            {'medicine'})
        self.assertIn('Inventory Alerts', response.content.decode())

    def test_bell_is_only_rendered_for_nurses(self):
        self._add_vaccine(qty=2, reorder=5)
        self.client.force_login(self.doctor)
        response = self.client.get(reverse('inventory:vaccine_list'))
        self.assertNotIn('inventory_alerts', response.context)
        self.assertNotIn('Inventory Alerts', response.content.decode())

        self.client.force_login(self.admin)
        response = self.client.get(reverse('inventory:vaccine_list'))
        self.assertNotIn('inventory_alerts', response.context)
        self.assertNotIn('Inventory Alerts', response.content.decode())

    def test_viewing_alerts_does_not_change_stock_or_create_transactions(self):
        self._add_vaccine(qty=2, reorder=5, days=10)
        self.medicine.quantity_available = 0
        self.medicine.save(update_fields=['quantity_available'])
        before_medicine = MedicineInventory.objects.get(pk=self.medicine.pk)
        before_vaccine = list(VaccineInventory.objects.values(
            'pk', 'quantity_available', 'quantity_received', 'quantity_used'))
        movement_counts = (
            MedicineStockMovement.objects.count(),
            VaccineStockMovement.objects.count(),
        )
        get_inventory_alerts()
        get_inventory_alerts(module='vaccine')

        self.client.force_login(self.nurse)
        self.client.get(reverse('inventory:vaccine_list'))
        self.client.get(reverse('inventory:medicine_list'))

        after_medicine = MedicineInventory.objects.get(pk=self.medicine.pk)
        self.assertEqual(
            after_medicine.quantity_available, before_medicine.quantity_available)
        self.assertEqual(
            list(VaccineInventory.objects.values(
                'pk', 'quantity_available', 'quantity_received', 'quantity_used')),
            before_vaccine,
        )
        self.assertEqual(
            movement_counts,
            (MedicineStockMovement.objects.count(),
             VaccineStockMovement.objects.count()),
        )

    def test_confirmed_dispensing_refreshes_the_low_stock_alert(self):
        self.medicine.quantity_available = 5
        self.medicine.reorder_level = 3
        self.medicine.save(update_fields=['quantity_available', 'reorder_level'])
        self.assertNotIn('low_stock', self._kinds(medicine_alerts()))

        prescription = self._prescription(quantity=2)
        self.client.force_login(self.nurse)
        self.client.post(
            reverse('inventory:prescription_dispense', args=[prescription.pk]),
            follow=True,
        )
        self.medicine.refresh_from_db()
        self.assertEqual(self.medicine.quantity_available, 3)
        low = [alert for alert in medicine_alerts()
               if alert['kind'] == 'low_stock']
        self.assertEqual(len(low), 1)
        self.assertEqual(low[0]['quantity'], 3)
