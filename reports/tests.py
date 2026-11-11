import datetime
import csv
import re

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from bite_cases.models import AnimalBiteCase, ExposureClassification
from inventory.models import MedicineInventory, VaccineInventory
from patients.models import Patient
from vaccination.models import VaccinationSchedule, VaccineDose

from .services import (
    animal_type_distribution, barangay_distribution, completed_schedule_case_ids,
    grouped_medicine_products, grouped_vaccine_products, inventory_summary,
    patient_treatment_summary,
)

U = get_user_model()

ALL_REPORT_URLS = (
    'reports_index',
    'cases_report',
    'patient_treatment_report',
    'vaccine_report',
    'vaccine_inventory_report',
    'medicine_inventory_report',
    'stock_alerts_report',
    'barangay_report',
    'animal_type_report',
    'missed_appointments',
)


def make_user(username, role):
    return U.objects.create_user(username, password='pw12345678', role=role)


def make_patient(number, first, last):
    return Patient.objects.create(
        patient_number=number, first_name=first, last_name=last,
        birthdate=datetime.date(1995, 5, 5), sex='female',
        contact_number='09170000000', address='Test address')


def make_case(number, patient, bite_date, barangay='Poblacion',
              animal='dog', status='under_treatment', animal_other='',
              created_by=None):
    return AnimalBiteCase.objects.create(
        case_number=number, patient=patient, created_by=created_by,
        date_time_bite=bite_date,
        place_of_incident=f'{barangay} road', incident_barangay=barangay,
        animal_type=animal, animal_other=animal_other,
        animal_ownership='owned', animal_vax_status='unknown',
        animal_condition='under_observation', exposure_type='bite',
        body_part_affected='left hand', number_of_wounds=1,
        case_status=status)


class ReportAccessTests(TestCase):

    def setUp(self):
        self.staff = {
            role: make_user(f'role.{role}', role)
            for role in ('admin', 'nurse', 'health_worker', 'encoder')
        }
        self.doctor = make_user('role.doctor', 'doctor')
        self.patient_user = make_user('role.patient', 'patient')

    def test_authorized_staff_can_open_every_report(self):
        for role, user in self.staff.items():
            self.client.force_login(user)
            for name in ALL_REPORT_URLS:
                with self.subTest(role=role, url=name):
                    self.assertEqual(
                        self.client.get(reverse(f'reports:{name}')).status_code,
                        200)

    def test_doctors_and_patients_are_denied(self):
        self.client.force_login(self.doctor)
        for name in ALL_REPORT_URLS:
            with self.subTest(role='doctor', url=name):
                self.assertEqual(
                    self.client.get(reverse(f'reports:{name}')).status_code, 403)

        # Patient accounts are redirected to their portal by middleware.
        self.client.force_login(self.patient_user)
        for name in ALL_REPORT_URLS:
            with self.subTest(role='patient', url=name):
                self.assertIn(
                    self.client.get(reverse(f'reports:{name}')).status_code,
                    (302, 403))

    def test_anonymous_users_are_redirected_to_login(self):
        response = self.client.get(reverse('reports:reports_index'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])


class PatientReportTests(TestCase):

    def setUp(self):
        self.nurse = make_user('rep.nurse', 'nurse')
        self.patient = make_patient('PAT-R0001', 'Maria', 'Santos')
        self.other_patient = make_patient('PAT-R0002', 'Jose', 'Reyes')
        # Same patient, two separate incidents: two cases, one patient.
        self.complete_case = make_case(
            'ABC-R0001', self.patient,
            datetime.datetime(2026, 11, 1, 9, 0),
            barangay='Poblacion', status='under_treatment',
            created_by=self.nurse)
        self.ongoing_case = make_case(
            'ABC-R0002', self.patient,
            datetime.datetime(2026, 11, 3, 10, 0),
            barangay='San Isidro', animal='cat', status='under_treatment',
            created_by=self.nurse)
        self.new_case = make_case(
            'ABC-R0003', self.other_patient,
            datetime.datetime(2026, 10, 20, 8, 0),
            barangay='Poblacion', status='new', created_by=self.nurse)

        # Completed plan: every prescribed dose administered.
        complete_schedule = VaccinationSchedule.objects.create(
            bite_case=self.complete_case, created_by=self.nurse)
        for index in (1, 2):
            VaccineDose.objects.create(
                schedule=complete_schedule, dose_label=f'Dose {index}',
                dose_status='completed', actual_date=datetime.date(2026, 11, index))

        # One dose administered out of two: still under treatment.
        ongoing_schedule = VaccinationSchedule.objects.create(
            bite_case=self.ongoing_case, created_by=self.nurse)
        VaccineDose.objects.create(
            schedule=ongoing_schedule, dose_label='Dose 1',
            dose_status='completed', actual_date=datetime.date(2026, 11, 3))
        VaccineDose.objects.create(
            schedule=ongoing_schedule, dose_label='Dose 2',
            dose_status='scheduled', scheduled_date=datetime.date(2026, 11, 10))

    def _login(self):
        self.client.force_login(self.nurse)

    def test_completed_requires_the_full_dose_plan(self):
        completed = completed_schedule_case_ids()
        self.assertIn(self.complete_case.pk, completed)
        self.assertNotIn(self.ongoing_case.pk, completed)

    def test_summary_counts_unique_patients_and_workflow_status(self):
        summary = patient_treatment_summary(AnimalBiteCase.objects.all())
        self.assertEqual(summary['total_patients'], 2)
        self.assertEqual(summary['total_cases'], 3)
        self.assertEqual(summary['completed'], 1)
        self.assertEqual(summary['under_treatment'], 1)

    def test_reports_center_shows_the_six_summary_cards(self):
        self._login()
        response = self.client.get(reverse('reports:reports_index'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Total Patients')
        self.assertContains(response, 'Total Bite Cases')
        self.assertContains(response, 'Out of Stock')
        self.assertContains(response, 'Expired Batches')
        self.assertContains(response, 'Patient and Treatment')
        self.assertContains(response, 'Animal Bite Statistics')
        self.assertEqual(response.context['summary']['total_patients'], 2)
        self.assertEqual(response.context['summary']['total_cases'], 3)
        self.assertEqual(response.context['summary']['completed'], 1)

    def test_simplified_patient_report_ignores_custom_date_parameters(self):
        self._login()
        url = reverse('reports:patient_treatment_report')
        response = self.client.get(
            url, {'type': 'custom', 'start_date': '2026-11-01',
                  'end_date': '2026-11-02'})
        self.assertEqual(response.context['summary']['total_cases'], 3)
        self.assertContains(response, 'ABC-R0001')
        self.assertContains(response, 'ABC-R0002')
        self.assertContains(response, 'ABC-R0003')

    def test_simplified_filters_show_six_periods(self):
        self._login()
        response = self.client.get(reverse('reports:patient_treatment_report'))
        for label in ('Daily', 'Weekly', 'Monthly', 'Last 7 days',
                      'Last 30 days', 'Annual'):
            self.assertContains(response, label)
        self.assertNotContains(response, 'All Time')

    def test_cases_report_uses_simplified_filters(self):
        self._login()
        response = self.client.get(reverse('reports:cases_report'), {
            'type': 'all', 'barangay': 'San Isidro', 'animal': 'cat',
            'status': 'completed', 'exposure': 'scratch'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total'], 3)
        for text in ('Custom range', 'Start date', 'End date',
                     'name="barangay"', 'name="animal"', 'name="status"',
                     'name="exposure"', '>Clear<', '>Apply Filters<'):
            self.assertNotContains(response, text)
        for label in ('Daily', 'Weekly', 'Monthly', 'Last 7 days',
                      'Last 30 days', 'Annual'):
            self.assertContains(response, label)
        self.assertContains(response, 'Search case number or patient')

    def test_cases_report_exports_respect_search(self):
        self._login()
        url = reverse('reports:cases_report')
        params = {'type': 'all', 'q': 'ABC-R0002'}
        csv_response = self.client.get(url, {**params, 'export': 'csv'})
        body = csv_response.content.decode()
        self.assertIn('ABC-R0002', body)
        self.assertNotIn('ABC-R0003', body)
        pdf_response = self.client.get(url, {**params, 'export': 'pdf'})
        self.assertTrue(pdf_response.content.startswith(b'%PDF'))

    def test_missed_report_uses_simplified_filters(self):
        self._login()
        response = self.client.get(
            reverse('reports:missed_appointments'), {'type': 'all'})
        self.assertEqual(response.status_code, 200)
        for text in ('Start date', 'End date', 'Custom', '>Clear<', '>Apply<'):
            self.assertNotContains(response, text)
        for label in ('Daily', 'Weekly', 'Monthly', 'Last 7 days',
                      'Last 30 days', 'Annual'):
            self.assertContains(response, label)
        self.assertContains(response, 'Search case number or patient')

    def test_patient_report_lists_required_columns_and_statuses(self):
        self._login()
        response = self.client.get(
            reverse('reports:patient_treatment_report'), {'type': 'all'})
        self.assertEqual(response.status_code, 200)
        for header in ('Patient Name', 'Patient ID', 'Case Number', 'Barangay',
                       'Animal Type', 'Date of Bite Incident',
                       'Treatment Status'):
            self.assertContains(response, header)
        statuses = {
            case.case_number: case.report_status
            for case in response.context['page_obj']
        }
        self.assertEqual(statuses['ABC-R0001'], 'Completed')
        self.assertEqual(statuses['ABC-R0002'], 'Under Treatment')
        self.assertEqual(statuses['ABC-R0003'], 'New Case')

    def test_patient_report_exports_match_search(self):
        self._login()
        url = reverse('reports:patient_treatment_report')
        params = {'type': 'all', 'q': 'ABC-R0001', 'export': 'csv'}
        csv_response = self.client.get(url, params)
        body = csv_response.content.decode()
        self.assertIn('text/csv', csv_response['Content-Type'])
        self.assertIn('ABC-R0001', body)
        self.assertNotIn('ABC-R0003', body)

        pdf_response = self.client.get(url, {**params, 'export': 'pdf'})
        self.assertEqual(pdf_response['Content-Type'], 'application/pdf')
        self.assertTrue(pdf_response.content.startswith(b'%PDF'))

    def test_simplified_search_ignores_stale_filter_values(self):
        self._login()
        response = self.client.get(reverse('reports:patient_treatment_report'), {
            'type': 'all', 'barangay': 'San Isidro', 'animal': 'cat',
            'status': 'completed'})
        self.assertEqual(response.context['summary']['total_cases'], 3)

    def test_simplified_search_matches_patient_number_and_case_number(self):
        self._login()
        url = reverse('reports:patient_treatment_report')
        by_patient_number = self.client.get(url, {'type': 'all', 'q': 'PAT-R0002'})
        self.assertEqual(by_patient_number.context['summary']['total_cases'], 1)
        self.assertContains(by_patient_number, 'ABC-R0003')

        by_case_number = self.client.get(url, {'type': 'all', 'q': 'ABC-R0002'})
        self.assertEqual(by_case_number.context['summary']['total_cases'], 1)
        self.assertContains(by_case_number, 'ABC-R0002')

    def test_simplified_interface_hides_removed_controls(self):
        self._login()
        response = self.client.get(
            reverse('reports:patient_treatment_report'), {'type': 'all'})
        for text in ('Custom Range', 'Start date', 'End date',
                     'name="barangay"', 'name="animal"', 'name="status"',
                     '>Clear<', '>Apply<'):
            self.assertNotContains(response, text)
        self.assertContains(response, 'Search case number or patient')
        self.assertContains(response, 'e.g., ABC-00001 or Juan Dela Cruz')

    def test_reports_center_hides_removed_controls(self):
        self._login()
        response = self.client.get(reverse('reports:reports_index'), {
            'q': 'Maria', 'type': 'all'})
        for text in ('Custom Range', 'Start date', 'End date',
                     'name="barangay"', 'name="animal"', 'name="status"',
                     '>Clear<', '>Apply<'):
            self.assertNotContains(response, text)
        self.assertContains(response, 'Reporting Period')
        self.assertContains(response, 'Search All Records')
        self.assertContains(
            response,
            'Search name, barangay, sex, animal, case number...')
        column_headers = (
            b'class="table-header">Patient</th>',
            b'class="table-header">Patient ID</th>',
            b'class="table-header">Sex</th>',
            b'class="table-header">Case Number</th>',
            b'class="table-header">Barangay</th>',
            b'class="table-header">Animal</th>',
            b'class="table-header">Treatment Status</th>',
        )
        header_positions = [response.content.index(header)
                            for header in column_headers]
        self.assertEqual(header_positions, sorted(header_positions))
        self.assertLess(
            header_positions[0],
            response.content.index(b'Reporting Period'))
        self.assertLess(
            response.content.index(b'Search All Records'),
            response.content.index(b'Reporting Period'))
        self.assertLess(
            response.content.index(b'Reporting Period'),
            response.content.index(b'Total Patients'))

    def test_existing_reports_still_open(self):
        self._login()
        for name in ('cases_report', 'vaccine_report', 'missed_appointments'):
            with self.subTest(url=name):
                self.assertEqual(
                    self.client.get(reverse(f'reports:{name}')).status_code, 200)


class GlobalReportSearchTests(TestCase):

    def setUp(self):
        self.nurse = make_user('global.search.nurse', 'nurse')
        self.male = make_patient('PAT-G0001', 'Aiden', 'Cruz')
        self.male.sex = 'male'
        self.male.middle_name = 'Luis'
        self.male.save(update_fields=['sex', 'middle_name'])
        self.male.barangay = self._barangay('San Pedro')
        self.male.save(update_fields=['barangay'])
        self.female = make_patient('PAT-G0002', 'Maria', 'Santos')
        self.female.barangay = self._barangay('San Isidro')
        self.female.save(update_fields=['barangay'])
        today = timezone.localdate()
        self.male_case = make_case(
            'ABC-G0001', self.male,
            datetime.datetime.combine(today, datetime.time(10, 0)),
            barangay='San Pedro', animal='dog', created_by=self.nurse)
        self.female_case = make_case(
            'ABC-G0002', self.female,
            datetime.datetime.combine(today, datetime.time(11, 0)),
            barangay='San Isidro', animal='cat', status='new',
            created_by=self.nurse)
        self.client.force_login(self.nurse)

    @staticmethod
    def _barangay(name):
        from patients.models import Barangay
        barangay, _ = Barangay.objects.get_or_create(name=name)
        return barangay

    def test_sex_search_is_exact_and_case_insensitive(self):
        url = reverse('reports:reports_index')
        for keyword in ('Male', 'male', 'MALE'):
            with self.subTest(keyword=keyword):
                response = self.client.get(url, {'q': keyword, 'type': 'all'})
                self.assertEqual(response.context['summary']['total_patients'], 1)
                self.assertEqual(response.context['summary']['total_cases'], 1)
                self.assertEqual(
                    [case.case_number for case in response.context['case_page']],
                    ['ABC-G0001'])

        response = self.client.get(url, {'q': 'Female', 'type': 'all'})
        self.assertEqual(response.context['summary']['total_patients'], 1)
        self.assertEqual(
            [case.case_number for case in response.context['case_page']],
            ['ABC-G0002'])

    def test_patient_report_export_uses_exact_sex_search(self):
        response = self.client.get(
            reverse('reports:patient_treatment_report'),
            {'q': 'male', 'type': 'all', 'export': 'csv'})
        body = response.content.decode()
        self.assertIn('ABC-G0001', body)
        self.assertNotIn('ABC-G0002', body)

    def test_global_search_matches_names_case_barangay_animal_and_status(self):
        url = reverse('reports:reports_index')
        cases = {
            'Aiden Luis Cruz': 'ABC-G0001',
            'ABC-G0002': 'ABC-G0002',
            'San Pedro': 'ABC-G0001',
            'Dog': 'ABC-G0001',
            'Under Treatment': 'ABC-G0001',
        }
        for query, matching_case in cases.items():
            with self.subTest(query=query):
                response = self.client.get(
                    url, {'q': query, 'type': 'all'})
                self.assertEqual(
                    [case.case_number for case in response.context['case_page']],
                    [matching_case])

    def test_search_field_limits_results_to_the_selected_field(self):
        self.female.first_name = self.male_case.case_number
        self.female.save(update_fields=['first_name'])
        url = reverse('reports:reports_index')

        patient_name = self.client.get(url, {
            'q': self.male_case.case_number,
            'search_field': 'patient_name',
            'type': 'all',
        })
        self.assertEqual(patient_name.context['result_count'], 1)
        self.assertEqual(
            [case.case_number for case in patient_name.context['case_page']],
            [self.female_case.case_number])

        case_number = self.client.get(url, {
            'q': self.male_case.case_number,
            'search_field': 'case_number',
            'type': 'all',
        })
        self.assertEqual(case_number.context['result_count'], 1)
        self.assertEqual(
            [case.case_number for case in case_number.context['case_page']],
            [self.male_case.case_number])

        export = self.client.get(
            reverse('reports:patient_treatment_report'),
            {'q': self.male_case.case_number, 'search_field': 'patient_name',
             'type': 'all', 'export': 'csv'})
        lines = export.content.decode().splitlines()
        self.assertEqual(
            lines[0],
            'Patient,Patient ID,Sex,Case Number,Barangay,Animal,Treatment Status')
        rows = list(csv.reader(lines))
        self.assertEqual(
            [row[3] for row in rows[1:]], [self.female_case.case_number])

    def test_exact_barangay_match_is_case_insensitive_and_not_a_substring(self):
        self.male_case.incident_barangay = 'Balugo'
        self.male_case.save(update_fields=['incident_barangay'])
        self.female_case.incident_barangay = 'Balugo East'
        self.female_case.save(update_fields=['incident_barangay'])
        url = reverse('reports:reports_index')

        for value in ('Balugo', 'balugo', 'BALUGO'):
            with self.subTest(value=value):
                response = self.client.get(url, {
                    'q': value, 'search_field': 'barangay', 'type': 'all'})
                self.assertEqual(response.context['result_count'], 1)
                self.assertEqual(
                    [case.case_number for case in response.context['case_page']],
                    [self.male_case.case_number])
                self.assertContains(response, '<td class="table-cell">Balugo</td>')

        all_fields = self.client.get(url, {'q': 'balugo', 'type': 'all'})
        self.assertEqual(all_fields.context['result_count'], 1)
        self.assertEqual(
            [case.case_number for case in all_fields.context['case_page']],
            [self.male_case.case_number])

    def test_patient_name_returns_each_case_for_that_patient(self):
        make_case(
            'ABC-G0003', self.male,
            datetime.datetime.combine(
                timezone.localdate(), datetime.time(12, 0)),
            barangay='San Pedro', animal='cat', created_by=self.nurse)
        response = self.client.get(reverse('reports:reports_index'), {
            'q': 'aIdEn', 'search_field': 'patient_name', 'type': 'all'})
        self.assertEqual(response.context['result_count'], 2)
        self.assertEqual(
            {case.case_number for case in response.context['case_page']},
            {'ABC-G0001', 'ABC-G0003'})

    def test_clear_search_resets_field_and_keeps_reporting_period(self):
        response = self.client.get(reverse('reports:reports_index'), {
            'q': 'male', 'search_field': 'sex', 'type': 'monthly'})
        self.assertContains(response, 'value="sex" selected')
        self.assertContains(response, 'href="?type=monthly"')
        self.assertContains(response, '?type=weekly')
        self.assertContains(response, 'search_field=sex')

    def test_reporting_period_filters_matching_cases_by_incident_date(self):
        older_date = timezone.localdate() - datetime.timedelta(days=45)
        old_case = make_case(
            'ABC-G0003', self.male, datetime.datetime.combine(
                older_date, datetime.time(9, 0)),
            barangay='San Pedro', created_by=self.nurse)
        response = self.client.get(reverse('reports:reports_index'), {
            'q': 'Male', 'type': 'monthly'})
        self.assertEqual(response.context['summary']['total_cases'], 1)
        self.assertContains(response, self.male_case.case_number)
        self.assertNotContains(response, old_case.case_number)

    def test_category_and_vaccination_search_use_related_records(self):
        ExposureClassification.objects.create(
            bite_case=self.male_case, category='category_iii')
        response = self.client.get(reverse('reports:reports_index'), {
            'q': 'WHO Category III', 'type': 'all'})
        self.assertContains(response, self.male_case.case_number)
        self.assertNotContains(response, self.female_case.case_number)

        older_date = timezone.localdate() - datetime.timedelta(days=45)
        treated_case = make_case(
            'ABC-G0003', self.male, datetime.datetime.combine(
                older_date, datetime.time(9, 0)),
            barangay='San Pedro', created_by=self.nurse)
        schedule = VaccinationSchedule.objects.create(
            bite_case=treated_case, created_by=self.nurse)
        VaccineDose.objects.create(
            schedule=schedule, dose_label='Dose 1', vaccine_brand='Rabivax Z',
            dose_status='scheduled', scheduled_date=timezone.localdate())

        response = self.client.get(reverse('reports:reports_index'), {
            'q': 'Rabivax Z', 'type': 'monthly'})
        self.assertEqual(response.context['summary']['total_cases'], 1)
        self.assertEqual(
            [case.case_number for case in response.context['case_page']],
            [treated_case.case_number])

    def test_inventory_search_returns_actual_batches_without_changing_snapshot(self):
        expiry = timezone.localdate() + datetime.timedelta(days=365)
        vaccine = VaccineInventory.objects.create(
            vaccine_name='Rabies Vaccine', brand='Brand X',
            batch_number='VX-G0001', expiration_date=expiry,
            quantity_received=20, quantity_available=12,
            source_supplier='Acme Supply')
        medicine = MedicineInventory.objects.create(
            medicine_name='Amoxicillin', generic_name='Amoxicillin',
            strength='500 mg', dosage_form='capsule', batch_number='MX-G0001',
            expiration_date=expiry, quantity_received=20,
            quantity_available=12, source_supplier='Acme Supply')
        baseline = self.client.get(reverse('reports:reports_index'), {
            'type': 'all'}).context['inventory']

        response = self.client.get(
            reverse('reports:vaccine_inventory_report'), {'q': 'Rabies Vaccine'})
        self.assertEqual(len(response.context['products']), 1)
        self.assertIn(vaccine, response.context['products'][0]['batches'])

        response = self.client.get(
            reverse('reports:medicine_inventory_report'), {'q': 'Amoxicillin'})
        self.assertEqual(len(response.context['products']), 1)
        self.assertIn(medicine, response.context['products'][0]['batches'])
        current_snapshot = self.client.get(
            reverse('reports:reports_index'), {'type': 'all'}
        ).context['inventory']
        self.assertEqual(current_snapshot, baseline)

    def test_empty_search_results_are_explicit(self):
        response = self.client.get(reverse('reports:reports_index'), {
            'q': 'not-a-real-record', 'type': 'all'})
        self.assertContains(response, 'No matching records found.')
        self.assertContains(
            response,
            'Try another patient name, case number, barangay, animal type, or status.')


class DistributionTests(TestCase):

    def setUp(self):
        self.nurse = make_user('dist.nurse', 'nurse')
        self.patient = make_patient('PAT-D0001', 'Ana', 'Cruz')
        for number, barangay, animal, bite, animal_other in (
            ('ABC-D0001', 'Poblacion', 'dog', datetime.datetime(2026, 11, 1, 9), ''),
            ('ABC-D0002', 'Poblacion', 'dog', datetime.datetime(2026, 11, 2, 9), ''),
            ('ABC-D0003', 'San Isidro', 'cat', datetime.datetime(2026, 11, 3, 9), ''),
            ('ABC-D0004', '', 'other', datetime.datetime(2026, 11, 4, 9), 'Bat'),
        ):
            make_case(number, self.patient, bite, barangay=barangay,
                      animal=animal, animal_other=animal_other,
                      status='new', created_by=self.nurse)

    def test_barangay_distribution_is_ranked_and_handles_blanks(self):
        result = barangay_distribution(AnimalBiteCase.objects.all())
        self.assertEqual(result['rows'][0]['name'], 'Poblacion')
        self.assertEqual(result['rows'][0]['count'], 2)
        self.assertEqual(result['rows'][0]['pct'], 50.0)
        names = [row['name'] for row in result['rows']]
        self.assertIn('Unspecified', names)
        self.assertEqual(result['total'], 4)

    def test_animal_distribution_uses_saved_types(self):
        result = animal_type_distribution(AnimalBiteCase.objects.all())
        self.assertEqual(result['rows'][0]['name'], 'Dog')
        self.assertEqual(result['rows'][0]['count'], 2)
        names = [row['name'] for row in result['rows']]
        self.assertIn('Cat', names)
        self.assertIn('Other', names)
        self.assertEqual(result['total'], 4)

    def test_barangay_and_animal_pages_render(self):
        self.client.force_login(self.nurse)
        for name in ('barangay_report', 'animal_type_report'):
            with self.subTest(url=name):
                response = self.client.get(reverse(f'reports:{name}'))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'Distribution — horizontal bars')

    def test_distribution_reports_ignore_custom_date_parameters(self):
        self.client.force_login(self.nurse)
        response = self.client.get(
            reverse('reports:barangay_report'),
            {'type': 'custom', 'start_date': '2026-11-01',
             'end_date': '2026-11-02'})
        self.assertEqual(response.context['total'], 4)
        self.assertEqual(response.context['top']['name'], 'Poblacion')

    def test_distribution_reports_ignore_stale_filters(self):
        self.client.force_login(self.nurse)
        response = self.client.get(
            reverse('reports:barangay_report'),
            {'type': 'all', 'animal': 'cat', 'barangay': 'San Isidro'})
        self.assertEqual(response.context['total'], 4)


class PeriodParsingTests(TestCase):

    def test_simplified_periods_resolve_to_expected_ranges(self):
        from .views import _parse_dates

        factory = RequestFactory()
        expected = {
            'daily': (datetime.date(2026, 11, 5), datetime.date(2026, 11, 5)),
            'weekly': (datetime.date(2026, 11, 2), datetime.date(2026, 11, 5)),
            'monthly': (datetime.date(2026, 11, 1), datetime.date(2026, 11, 5)),
            'last7': (datetime.date(2026, 10, 30), datetime.date(2026, 11, 5)),
            'last30': (datetime.date(2026, 10, 7), datetime.date(2026, 11, 5)),
            'annual': (datetime.date(2026, 1, 1), datetime.date(2026, 11, 5)),
        }
        with patch('reports.views.timezone.localdate',
                   return_value=datetime.date(2026, 11, 5)):
            for period, (start, end) in expected.items():
                with self.subTest(period=period):
                    request = factory.get('/reports/', {'type': period})
                    parsed = _parse_dates(request, simplified=True)
                    self.assertEqual(parsed[:3], (start, end, period))

            request = factory.get('/reports/', {
                'type': 'custom', 'start_date': '2026-11-01',
                'end_date': '2026-11-02'})
            start, end, report_type, _label = _parse_dates(
                request, simplified=True)
            self.assertIsNone(start)
            self.assertIsNone(end)
            self.assertEqual(report_type, 'all')


class InventoryReportTests(TestCase):

    def setUp(self):
        self.nurse = make_user('inv.rep.nurse', 'nurse')
        today = timezone.localdate()
        self.future = today + datetime.timedelta(days=180)
        self.past = today - datetime.timedelta(days=10)
        self.live_date = today + datetime.timedelta(days=365)

        # Two batches of the same product: one product row.
        self.v1 = VaccineInventory.objects.create(
            vaccine_name='Verorab', brand='Sanofi', batch_number='V-1',
            expiration_date=self.future, quantity_received=10,
            quantity_available=8, quantity_used=2, reorder_level=5)
        self.v2 = VaccineInventory.objects.create(
            vaccine_name='Verorab', brand='Sanofi', batch_number='V-2',
            expiration_date=self.live_date, quantity_received=5,
            quantity_available=5, quantity_used=0, reorder_level=5)
        self.expired = VaccineInventory.objects.create(
            vaccine_name='Speeda', brand='Other', batch_number='V-3',
            expiration_date=self.past, quantity_received=4,
            quantity_available=4, quantity_used=0, reorder_level=2)
        self.low = VaccineInventory.objects.create(
            vaccine_name='Rabipur', brand='GSK', batch_number='V-4',
            expiration_date=self.future, quantity_received=6,
            quantity_available=2, quantity_used=4, reorder_level=5)

        # Medicines keep different strengths apart and count dispensed units.
        self.med_a = MedicineInventory.objects.create(
            medicine_name='Amoxicillin', generic_name='Amoxicillin',
            strength='500 mg', dosage_form='capsule', unit='capsule',
            batch_number='M-1', expiration_date=self.future,
            quantity_received=20, quantity_available=12,
            quantity_dispensed=8, reorder_level=5)
        self.med_b = MedicineInventory.objects.create(
            medicine_name='Amoxicillin', generic_name='Amoxicillin',
            strength='250 mg', dosage_form='capsule', unit='capsule',
            batch_number='M-2', expiration_date=self.future,
            quantity_received=10, quantity_available=10,
            quantity_dispensed=0, reorder_level=5)
        self.med_empty = MedicineInventory.objects.create(
            medicine_name='Paracetamol', generic_name='Paracetamol',
            strength='500 mg', dosage_form='tablet', unit='tablet',
            batch_number='M-3', expiration_date=self.future,
            quantity_received=10, quantity_available=0,
            quantity_dispensed=10, reorder_level=5)

    def test_vaccine_products_group_batches_and_exclude_expired_availability(self):
        products = grouped_vaccine_products()
        verorab = next(p for p in products if p['name'] == 'Verorab')
        self.assertEqual(verorab['batch_count'], 2)
        self.assertEqual(verorab['total_received'], 15)
        self.assertEqual(verorab['total_used'], 2)
        self.assertEqual(verorab['total_available'], 13)
        self.assertEqual(verorab['stock_status'], 'Available')

        speeda = next(p for p in products if p['name'] == 'Speeda')
        self.assertEqual(speeda['total_available'], 0)
        self.assertEqual(speeda['stock_status'], 'Expired')

        rabipur = next(p for p in products if p['name'] == 'Rabipur')
        self.assertEqual(rabipur['stock_status'], 'Low Stock')

    def test_medicine_products_keep_strengths_separate(self):
        products = grouped_medicine_products()
        amoxicillin = [p for p in products if p['name'] == 'Amoxicillin']
        self.assertEqual(len(amoxicillin), 2)
        strengths = {p['strength'] for p in amoxicillin}
        self.assertEqual(strengths, {'500 mg', '250 mg'})
        dose_500 = next(p for p in amoxicillin if p['strength'] == '500 mg')
        self.assertEqual(dose_500['total_dispensed'], 8)
        paracetamol = next(p for p in products if p['name'] == 'Paracetamol')
        self.assertEqual(paracetamol['stock_status'], 'Out of Stock')

    def test_inventory_summary_counts_out_of_stock_and_expired_batches(self):
        summary = inventory_summary()
        self.assertEqual(summary['expired_batches'], 1)
        self.assertEqual(summary['out_of_stock'], 2)
        self.assertEqual(summary['low_stock'], 1)

    def test_inventory_pages_render_one_row_per_product(self):
        self.client.force_login(self.nurse)
        response = self.client.get(reverse('reports:vaccine_inventory_report'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Verorab')
        self.assertContains(response, 'Batch detail')
        self.assertEqual(
            [p['name'] for p in response.context['products']].count('Verorab'), 1)
        self.assertEqual(response.context['totals']['products'], 3)
        self.assertEqual(response.context['totals']['received'], 25)

        medicine = self.client.get(reverse('reports:medicine_inventory_report'))
        self.assertEqual(medicine.status_code, 200)
        self.assertEqual(medicine.context['totals']['products'], 3)
        self.assertEqual(medicine.context['totals']['dispensed'], 18)

    def test_stock_alert_report_groups_without_changing_stock(self):
        before = list(VaccineInventory.objects.values_list(
            'pk', 'quantity_available', 'quantity_used'))
        self.client.force_login(self.nurse)
        response = self.client.get(reverse('reports:stock_alerts_report'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Low Stock')
        self.assertContains(response, 'Out of Stock')
        self.assertContains(response, 'Expiring Soon')
        self.assertContains(response, 'Expired')
        sections = {s['key']: s for s in response.context['sections']}
        self.assertEqual(len(sections['expired']['rows']), 1)
        self.assertEqual(sections['expired']['rows'][0]['batch'], 'V-3')
        after = list(VaccineInventory.objects.values_list(
            'pk', 'quantity_available', 'quantity_used'))
        self.assertEqual(before, after)

    def test_inventory_exports_are_available(self):
        self.client.force_login(self.nurse)
        for name in ('vaccine_inventory_report', 'medicine_inventory_report',
                     'stock_alerts_report'):
            with self.subTest(url=name):
                csv_response = self.client.get(
                    reverse(f'reports:{name}'), {'export': 'csv'})
                self.assertIn('text/csv', csv_response['Content-Type'])
                pdf_response = self.client.get(
                    reverse(f'reports:{name}'), {'export': 'pdf'})
                self.assertTrue(pdf_response.content.startswith(b'%PDF'))


class OfficialPrintTests(TestCase):
    """Every report Print action and PDF must use the shared ABTC template."""

    def setUp(self):
        self.nurse = make_user('print.nurse', 'nurse')
        self.doctor = make_user('print.doctor', 'doctor')
        self.patient_user = make_user('print.patient', 'patient')
        self.client.force_login(self.nurse)

    def test_every_report_print_route_renders_the_official_document(self):
        for name in ALL_REPORT_URLS[1:]:
            with self.subTest(url=name):
                response = self.client.get(
                    reverse(f'reports:{name}'), {'export': 'print'})
                self.assertEqual(response.status_code, 200)
                content = response.content.decode()
                for marker in (
                    'Animal Bite Treatment Center',
                    'Municipal Health Office',
                    'print_official.css',
                    'lgu_dumingag_logo.png',
                    'abtc-letterhead',
                    'abtc-footer',
                    'System-Generated Document',
                    'Report Details',
                ):
                    self.assertIn(marker, content)
                self.assertNotIn('<aside', content)

    def test_every_report_pdf_is_a4_portrait(self):
        for name in ALL_REPORT_URLS[1:]:
            with self.subTest(url=name):
                response = self.client.get(
                    reverse(f'reports:{name}'), {'export': 'pdf'})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.content.startswith(b'%PDF'))
                match = re.search(
                    rb'/MediaBox\s*\[\s*0\s+0\s+([0-9.]+)\s+([0-9.]+)',
                    response.content)
                self.assertIsNotNone(match)
                width, height = (float(value) for value in match.groups())
                self.assertAlmostEqual(width, 595.28, delta=1)
                self.assertAlmostEqual(height, 841.89, delta=1)
                self.assertLess(width, height)

    def test_print_routes_keep_report_access_control(self):
        url = reverse('reports:cases_report')
        self.client.force_login(self.doctor)
        self.assertEqual(
            self.client.get(url, {'export': 'print'}).status_code, 403)
        self.client.force_login(self.patient_user)
        self.assertIn(
            self.client.get(url, {'export': 'print'}).status_code, (302, 403))
        self.client.logout()
        self.assertEqual(
            self.client.get(url, {'export': 'print'}).status_code, 302)
