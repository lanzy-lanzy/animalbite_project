from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from datetime import date, timedelta, datetime
import csv
import io
from bite_cases.models import AnimalBiteCase
from vaccination.models import VaccineDose, FollowUpRecord
from inventory.models import VaccineInventory
from inventory.notifications import get_expiry_warning_days

from .pdf import build_official_pdf
from .services import (
    animal_type_distribution, barangay_distribution, completed_schedule_case_ids,
    effective_status, grouped_medicine_products, grouped_totals,
    grouped_vaccine_products, inventory_summary, patient_treatment_summary,
    stock_alert_sections,
)

REPORTS_ROLES = {'admin', 'nurse', 'health_worker', 'encoder'}


def reports_access(view_func):
    """Reports are for authorized ABTC staff, never patients or doctors."""
    @login_required
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if getattr(request.user, 'role', '') not in REPORTS_ROLES:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)
    return _wrapped


# ---------- helpers ----------
SIMPLIFIED_PERIODS = ('daily', 'weekly', 'monthly', 'last7', 'last30', 'annual')


def _parse_dates(request, simplified=False):
    """Return start_date, end_date, report_type, preset label for filtering."""
    report_type = request.GET.get('type', 'custom')
    if simplified and report_type not in SIMPLIFIED_PERIODS:
        # Hidden filters (custom dates, stale values) never apply here.
        report_type = 'all'
    start_str = request.GET.get('start_date', '')
    end_str = request.GET.get('end_date', '')
    today = timezone.localdate()
    start = end = None
    label = ''
    if report_type == 'daily':
        start = end = today
        label = f"Daily — {today.strftime('%b %d, %Y')}"
    elif report_type == 'weekly':
        start = today - timedelta(days=today.weekday())
        end = today
        label = f"Weekly — {start.strftime('%b %d')} to {end.strftime('%b %d, %Y')}"
    elif report_type == 'monthly':
        start = today.replace(day=1)
        end = today
        label = f"Monthly — {today.strftime('%B %Y')}"
    elif report_type == 'annual':
        start = today.replace(month=1, day=1)
        end = today
        label = f"Annual — {today.year}"
    elif report_type == 'last7':
        start = today - timedelta(days=6)
        end = today
        label = f"Last 7 days — {start.strftime('%b %d')} to {end.strftime('%b %d, %Y')}"
    elif report_type == 'last30':
        start = today - timedelta(days=29)
        end = today
        label = f"Last 30 days — {start.strftime('%b %d')} to {end.strftime('%b %d, %Y')}"
    elif report_type == 'all':
        label = "All time"
    else:  # custom
        report_type = 'custom'
        try:
            if start_str and end_str:
                start = datetime.strptime(start_str, '%Y-%m-%d').date()
                end = datetime.strptime(end_str, '%Y-%m-%d').date()
                label = f"Custom — {start.strftime('%b %d, %Y')} to {end.strftime('%b %d, %Y')}"
            elif start_str:
                start = datetime.strptime(start_str, '%Y-%m-%d').date()
                end = today
                label = f"From {start.strftime('%b %d, %Y')} to {today.strftime('%b %d, %Y')}"
            else:
                report_type = 'all'
                label = "All time"
        except ValueError:
            report_type = 'all'
            label = "All time"
    return start, end, report_type, label

def _filter_cases(qs, request, start, end, extra_filters=True):
    q = request.GET.get('q', '').strip()
    if extra_filters:
        barangay = request.GET.get('barangay', '')
        animal = request.GET.get('animal', '')
        status = request.GET.get('status', '')
        exposure = request.GET.get('exposure', '')
    else:
        # Simplified Reports Center pages only honor the period and search.
        barangay = animal = status = exposure = ''
    # Case reports are period reports on the recorded bite incident date.
    # Use aware day bounds so the filter does not depend on DB timezone tables.
    tz = timezone.get_current_timezone()
    if start:
        qs = qs.filter(date_time_bite__gte=timezone.make_aware(
            datetime.combine(start, datetime.min.time()), tz))
    if end:
        qs = qs.filter(date_time_bite__lte=timezone.make_aware(
            datetime.combine(end, datetime.max.time()), tz))
    if barangay:
        qs = qs.filter(incident_barangay__icontains=barangay)
    if animal:
        qs = qs.filter(animal_type=animal)
    if status:
        qs = qs.filter(case_status=status)
    if exposure:
        qs = qs.filter(exposure_type=exposure)
    if q:
        qs = qs.filter(
            Q(case_number__icontains=q)
            | Q(patient__patient_number__icontains=q)
            | Q(patient__first_name__icontains=q)
            | Q(patient__last_name__icontains=q)
        )
    return qs


def _animal_label(bite_case):
    label = bite_case.get_animal_type_display()
    if bite_case.animal_type == 'other' and bite_case.animal_other:
        return f'{label} ({bite_case.animal_other})'
    return label


def _barangay_label(value):
    return (value or '').strip() or 'Unspecified'


def _csv_response(filename, headers, rows):
    resp = HttpResponse(content_type='text/csv')
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    writer = csv.writer(resp)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    return resp


def _case_filter_summary(request, label, extra_filters=True):
    lines = [("Period", label)]
    keys = (('q', 'Search'),)
    if extra_filters:
        keys = (
            ('barangay', 'Barangay'),
            ('animal', 'Animal'),
            ('status', 'Status'),
            ('exposure', 'Exposure'),
        ) + keys
    for key, title in keys:
        value = request.GET.get(key, '').strip()
        if value:
            lines.append((title, value))
    if len(lines) == 1:
        lines.append(("Filters", "None"))
    return lines


def _export_period(start, end):
    return (
        start.isoformat() if start else 'all',
        end.isoformat() if end else 'all',
    )


def _pdf_response(pdf_bytes, filename, inline=False):
    resp = HttpResponse(pdf_bytes, content_type='application/pdf')
    disp = 'inline' if inline else 'attachment'
    resp['Content-Disposition'] = f'{disp}; filename="{filename}"'
    # allow iframe preview on same origin (fixes 127.0.0.1 refused to connect)
    resp['X-Frame-Options'] = 'SAMEORIGIN'
    # CSP frame-ancestors if present
    return resp

def _build_pdf(title, subtitle, headers, rows, summary_lines=None, landscape=False):
    """Build every report PDF with the shared official A4 portrait template.

    `landscape` is accepted for backwards compatibility and intentionally ignored:
    all ABTC documents print portrait.
    """
    return build_official_pdf(title, subtitle, headers, rows, summary_lines)


def _render_report_print(request, title, meta, headers, rows, summary_lines=None):
    """Render the official HTML print document for a report (browser Print)."""
    return render(request, 'reports/print_official.html', {
        'document_title': title,
        'document_meta': meta,
        'summary_lines': summary_lines or [],
        'headers': headers,
        'rows': rows,
        'printed_at': timezone.localtime(),
    })

@reports_access
def reports_index(request):
    start, end, rtype, label = _parse_dates(request)
    cases = _filter_cases(
        AnimalBiteCase.objects.all(), request, start, end, extra_filters=False)
    completed_ids = completed_schedule_case_ids()
    summary = patient_treatment_summary(cases, completed_ids)
    inventory = inventory_summary()
    context = {
        'summary': summary,
        'inventory': inventory,
        'report_type': rtype, 'label': label,
        'q': request.GET.get('q', ''),
        'today': timezone.localdate(),
    }
    return render(request, 'reports/index.html', context)

@reports_access
def cases_report(request):
    start, end, report_type, label = _parse_dates(request, simplified=True)
    title = label if label != "All time" else "All Cases"
    qs = _filter_cases(
        AnimalBiteCase.objects.select_related('patient'), request, start, end,
        extra_filters=False)

    # summary
    total = qs.count()
    by_status = qs.values('case_status').annotate(c=Count('id'))
    by_animal = qs.values('animal_type').annotate(c=Count('id'))
    by_barangay = qs.values('incident_barangay').annotate(c=Count('id')).order_by('-c')[:5]

    export = request.GET.get('export', '')
    preview = request.GET.get('preview', '')

    # CSV
    if export == 'csv':
        resp = HttpResponse(content_type='text/csv')
        resp['Content-Disposition'] = f'attachment; filename="cases_report_{date.today().isoformat()}.csv"'
        w = csv.writer(resp)
        w.writerow(['Case #', 'Patient', 'Date of Bite', 'Consultation', 'Animal', 'Exposure', 'Barangay', 'Status'])
        for c in qs.order_by('-created_at')[:5000]:
            w.writerow([c.case_number, c.patient.full_name(), c.date_time_bite.date() if c.date_time_bite else '', c.created_at.date(), c.get_animal_type_display(), c.get_exposure_type_display(), c.incident_barangay, c.get_case_status_display()])
        return resp

    # PDF (preview inline or download) and official browser print document
    if export in ('pdf', 'print'):
        headers = ["Case #", "Patient", "Bite Date", "Animal", "Exposure", "Barangay", "Status"]
        rows = []
        for c in qs.select_related('patient').order_by('-created_at')[:2000]:
            rows.append([c.case_number, c.patient.full_name(), c.date_time_bite.strftime('%Y-%m-%d') if c.date_time_bite else '', c.get_animal_type_display(), c.get_exposure_type_display(), c.incident_barangay, c.get_case_status_display()])
        subtitle = label + (f" · {qs.count()} record(s)" if qs.count() else "")
        summary = [("Total cases", str(total))]
        summary += _case_filter_summary(request, label, extra_filters=False)
        if export == 'print':
            return _render_report_print(request, title, subtitle, headers, rows, summary)
        pdf = _build_pdf(title, subtitle, headers, rows, summary_lines=summary)
        inline = preview == '1' or request.GET.get('inline') == '1'
        filename = f"ABTC_Cases_{start.isoformat() if start else 'all'}_{end.isoformat() if end else 'all'}.pdf"
        return _pdf_response(pdf, filename, inline=inline)

    # HTML preview (paginated)
    cases = qs.order_by('-created_at')
    # simple pagination via slice? Use Django paginator
    from django.core.paginator import Paginator
    paginator = Paginator(cases, 20)
    page = request.GET.get('page')
    page_obj = paginator.get_page(page)

    context = {
        'cases': page_obj, 'page_obj': page_obj,
        'title': title, 'label': label,
        'total': total, 'by_status': by_status, 'by_animal': by_animal, 'by_barangay': by_barangay,
        'report_type': report_type,
        'q': request.GET.get('q',''),
    }
    return render(request, 'reports/cases_report.html', context)

@reports_access
def vaccine_report(request):
    start, end, report_type, label = _parse_dates(request)
    # For vaccine, filter by date_received if date range given
    items = VaccineInventory.objects.all().order_by('vaccine_name')
    if start and end:
        items = items.filter(date_received__gte=start, date_received__lte=end)
    elif start:
        items = items.filter(date_received__gte=start)

    # allow vaccine name filter?
    q = request.GET.get('q','').strip()
    if q:
        items = items.filter(Q(vaccine_name__icontains=q) | Q(brand__icontains=q) | Q(batch_number__icontains=q))

    total_received = items.aggregate(total=Sum('quantity_received'))['total'] or 0
    total_used = items.aggregate(total=Sum('quantity_used'))['total'] or 0
    total_available = items.aggregate(total=Sum('quantity_available'))['total'] or 0

    export = request.GET.get('export','')
    preview = request.GET.get('preview','')

    if export == 'csv':
        resp = HttpResponse(content_type='text/csv')
        resp['Content-Disposition'] = f'attachment; filename="vaccine_report_{date.today().isoformat()}.csv"'
        w = csv.writer(resp)
        w.writerow(['Vaccine', 'Brand', 'Batch', 'Received', 'Used', 'Available', 'Expiry', 'Date Received'])
        for i in items:
            w.writerow([i.vaccine_name, i.brand, i.batch_number, i.quantity_received, i.quantity_used, i.quantity_available, i.expiration_date, i.date_received])
        return resp

    if export in ('pdf', 'print'):
        headers = ["Vaccine", "Brand", "Batch", "Received", "Used", "Available", "Expiry"]
        rows = [[i.vaccine_name, i.brand, i.batch_number, str(i.quantity_received), str(i.quantity_used), str(i.quantity_available), i.expiration_date.strftime('%Y-%m-%d') if i.expiration_date else ''] for i in items[:1500]]
        subtitle = label + f" · {items.count()} lot(s) · Received {total_received} · Used {total_used} · Available {total_available}"
        summary = [("Total lots", str(items.count())), ("Date range", label), ("Total received", str(total_received)), ("Total used", str(total_used)), ("Available", str(total_available))]
        if export == 'print':
            return _render_report_print(
                request, "Vaccine Inventory Report", subtitle, headers, rows, summary)
        pdf = _build_pdf("Vaccine Inventory Report", subtitle, headers, rows, summary_lines=summary)
        inline = preview=='1' or request.GET.get('inline')=='1'
        filename = f"ABTC_Vaccine_{start.isoformat() if start else 'all'}_{end.isoformat() if end else 'all'}.pdf"
        return _pdf_response(pdf, filename, inline=inline)

    from django.core.paginator import Paginator
    paginator = Paginator(items, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'reports/vaccine_report.html', {
        'items': page_obj, 'page_obj': page_obj,
        'total_received': total_received, 'total_used': total_used, 'total_available': total_available,
        'label': label, 'report_type': report_type,
        'start_date': start.strftime('%Y-%m-%d') if start else '',
        'end_date': end.strftime('%Y-%m-%d') if end else '',
        'q': q, 'has_filters': bool(start or end or q),
    })

@reports_access
def missed_appointments_report(request):
    start, end, report_type, label = _parse_dates(request, simplified=True)
    qs = FollowUpRecord.objects.filter(status='missed').select_related('bite_case__patient')
    if start and end:
        qs = qs.filter(follow_up_date__gte=start, follow_up_date__lte=end)
    elif start:
        qs = qs.filter(follow_up_date__gte=start)
    q = request.GET.get('q','').strip()
    if q:
        qs = qs.filter(Q(bite_case__patient__first_name__icontains=q) | Q(bite_case__patient__last_name__icontains=q) | Q(bite_case__case_number__icontains=q))
    qs = qs.order_by('-follow_up_date')

    export = request.GET.get('export','')
    preview = request.GET.get('preview','')
    if export == 'csv':
        resp = HttpResponse(content_type='text/csv')
        resp['Content-Disposition'] = f'attachment; filename="missed_appointments_{date.today().isoformat()}.csv"'
        w = csv.writer(resp)
        w.writerow(['Patient','Case #','Follow-Up Date','Status','Contact'])
        for r in qs[:5000]:
            w.writerow([r.bite_case.patient.full_name(), r.bite_case.case_number, r.follow_up_date, r.get_status_display(), r.bite_case.patient.contact_number])
        return resp
    if export in ('pdf', 'print'):
        headers = ["Patient", "Case #", "Follow-Up Date", "Status", "Contact"]
        rows = [[r.bite_case.patient.full_name(), r.bite_case.case_number, r.follow_up_date.strftime('%Y-%m-%d'), r.get_status_display(), r.bite_case.patient.contact_number] for r in qs[:1500]]
        subtitle = label + f" · {qs.count()} missed"
        summary = [("Date range", label), ("Total missed", str(qs.count()))]
        if q:
            summary.append(("Search", q))
        if export == 'print':
            return _render_report_print(
                request, "Missed Follow-Up Report", subtitle, headers, rows,
                summary)
        pdf = _build_pdf("Missed Follow-Up Report", subtitle, headers, rows, summary_lines=summary)
        inline = preview=='1' or request.GET.get('inline')=='1'
        filename = f"ABTC_Missed_{start.isoformat() if start else 'all'}_{end.isoformat() if end else 'all'}.pdf"
        return _pdf_response(pdf, filename, inline=inline)

    from django.core.paginator import Paginator
    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'reports/missed_appointments.html', {
        'records': page_obj, 'page_obj': page_obj,
        'label': label, 'report_type': report_type, 'q': q,
    })


# ---------- patient and treatment ----------

PATIENT_HEADERS = [
    'Patient Name', 'Patient ID', 'Case Number', 'Barangay', 'Animal Type',
    'Date of Bite Incident', 'Treatment Status',
]


def _patient_row(bite_case, completed_ids):
    return [
        bite_case.patient.full_name(),
        bite_case.patient.patient_number,
        bite_case.case_number,
        _barangay_label(bite_case.incident_barangay),
        _animal_label(bite_case),
        bite_case.date_time_bite.strftime('%Y-%m-%d') if bite_case.date_time_bite else '',
        effective_status(bite_case, completed_ids),
    ]


@reports_access
def patient_treatment_report(request):
    start, end, report_type, label = _parse_dates(request, simplified=True)
    qs = _filter_cases(
        AnimalBiteCase.objects.select_related('patient'), request, start, end,
        extra_filters=False)
    completed_ids = completed_schedule_case_ids()
    summary = patient_treatment_summary(qs, completed_ids)
    cases = qs.order_by('-date_time_bite', '-id')
    export = request.GET.get('export', '')
    preview = request.GET.get('preview', '')
    subtitle = f"{label} · {summary['total_cases']} case(s) · {summary['total_patients']} patient(s)"

    if export == 'csv':
        rows = [_patient_row(c, completed_ids) for c in cases[:5000]]
        return _csv_response(
            f"ABTC_Patient_Treatment_{date.today().isoformat()}.csv",
            PATIENT_HEADERS, rows)

    if export in ('pdf', 'print'):
        rows = [_patient_row(c, completed_ids) for c in cases[:2000]]
        summary_lines = _case_filter_summary(
            request, label, extra_filters=False) + [
            ('Patients', str(summary['total_patients'])),
            ('Bite cases', str(summary['total_cases'])),
            ('Completed', str(summary['completed'])),
            ('Under treatment', str(summary['under_treatment'])),
        ]
        if export == 'print':
            return _render_report_print(
                request, "Patient and Treatment Report", subtitle,
                PATIENT_HEADERS, rows, summary_lines)
        pdf = _build_pdf(
            "Patient and Treatment Report", subtitle, PATIENT_HEADERS, rows,
            summary_lines=summary_lines)
        s, e = _export_period(start, end)
        inline = preview == '1' or request.GET.get('inline') == '1'
        return _pdf_response(
            pdf, f"ABTC_Patient_Treatment_{s}_{e}.pdf", inline=inline)

    paginator = Paginator(cases, 20)
    page_obj = paginator.get_page(request.GET.get('page'))
    for case in page_obj:
        case.report_status = effective_status(case, completed_ids)
        case.report_barangay = _barangay_label(case.incident_barangay)
        case.report_animal = _animal_label(case)
    return render(request, 'reports/patient_treatment.html', {
        'page_obj': page_obj,
        'summary': summary,
        'label': label,
        'report_type': report_type,
        'q': request.GET.get('q', ''),
        'pdf_title': 'Patient and Treatment Report',
    })


# ---------- inventory snapshots ----------

def _search_products(products, q, fields):
    if not q:
        return products
    needle = q.casefold()
    return [
        product for product in products
        if any(needle in str(product.get(field) or '').casefold()
               for field in fields)
    ]


def _inventory_context(request, label, products, totals, snapshot_date):
    return {
        'products': products,
        'totals': totals,
        'snapshot_date': snapshot_date,
        'label': label,
        'q': request.GET.get('q', ''),
        'has_filters': bool(request.GET.get('q', '').strip()),
    }


@reports_access
def vaccine_inventory_report(request):
    q = request.GET.get('q', '').strip()
    products = _search_products(
        grouped_vaccine_products(), q, ('name', 'brand'))
    totals = grouped_totals(products)
    today = timezone.localdate()
    label = f"Current inventory snapshot · {today:%b %d, %Y}"
    export = request.GET.get('export', '')
    preview = request.GET.get('preview', '')
    headers = [
        'Vaccine Name', 'Brand', 'Batches', 'Total Received', 'Total Used',
        'Total Available', 'Nearest Expiration', 'Stock Status',
    ]

    def row(product):
        return [
            product['name'], product['brand'], product['batch_count'],
            product['total_received'], product['total_used'],
            product['total_available'],
            product['nearest_expiration'].strftime('%Y-%m-%d')
            if product['nearest_expiration'] else '',
            product['stock_status'],
        ]

    if export == 'csv':
        return _csv_response(
            f"ABTC_Vaccine_Inventory_{today.isoformat()}.csv",
            headers, [row(product) for product in products])

    if export in ('pdf', 'print'):
        summary_lines = [
            ('Snapshot', f"Current inventory as of {today:%b %d, %Y}"),
            ('Vaccine products', str(totals['products'])),
            ('Batches', str(totals['batches'])),
            ('Total received', str(totals['received'])),
            ('Total used', str(totals['used'])),
            ('Available (non-expired)', str(totals['available'])),
        ]
        rows = [row(product) for product in products]
        if export == 'print':
            return _render_report_print(
                request, "Vaccine Inventory Report", label, headers, rows,
                summary_lines)
        pdf = _build_pdf(
            "Vaccine Inventory Report", label, headers, rows,
            summary_lines=summary_lines)
        inline = preview == '1' or request.GET.get('inline') == '1'
        return _pdf_response(
            pdf, f"ABTC_Vaccine_Inventory_{today.isoformat()}.pdf",
            inline=inline)

    context = _inventory_context(request, label, products, totals, today)
    context['pdf_title'] = 'Vaccine Inventory Report'
    return render(request, 'reports/vaccine_inventory.html', context)


@reports_access
def medicine_inventory_report(request):
    q = request.GET.get('q', '').strip()
    products = _search_products(
        grouped_medicine_products(), q,
        ('name', 'generic_name', 'strength', 'dosage_form'))
    totals = grouped_totals(products)
    today = timezone.localdate()
    label = f"Current inventory snapshot · {today:%b %d, %Y}"
    export = request.GET.get('export', '')
    preview = request.GET.get('preview', '')
    headers = [
        'Medicine Name', 'Generic / Brand', 'Dosage Form', 'Unit', 'Batches',
        'Total Received', 'Total Dispensed', 'Total Available',
        'Nearest Expiration', 'Stock Status',
    ]

    def row(product):
        return [
            product['display_name'], product['generic_name'] or '—',
            product['dosage_form'], product['unit'], product['batch_count'],
            product['total_received'], product['total_dispensed'],
            product['total_available'],
            product['nearest_expiration'].strftime('%Y-%m-%d')
            if product['nearest_expiration'] else '',
            product['stock_status'],
        ]

    if export == 'csv':
        return _csv_response(
            f"ABTC_Medicine_Inventory_{today.isoformat()}.csv",
            headers, [row(product) for product in products])

    if export in ('pdf', 'print'):
        summary_lines = [
            ('Snapshot', f"Current inventory as of {today:%b %d, %Y}"),
            ('Medicine products', str(totals['products'])),
            ('Batches', str(totals['batches'])),
            ('Total received', str(totals['received'])),
            ('Total dispensed', str(totals['dispensed'])),
            ('Available (non-expired)', str(totals['available'])),
        ]
        rows = [row(product) for product in products]
        if export == 'print':
            return _render_report_print(
                request, "Medicine Inventory Report", label, headers, rows,
                summary_lines)
        pdf = _build_pdf(
            "Medicine Inventory Report", label, headers, rows,
            summary_lines=summary_lines)
        inline = preview == '1' or request.GET.get('inline') == '1'
        return _pdf_response(
            pdf, f"ABTC_Medicine_Inventory_{today.isoformat()}.pdf",
            inline=inline)

    context = _inventory_context(request, label, products, totals, today)
    context['pdf_title'] = 'Medicine Inventory Report'
    return render(request, 'reports/medicine_inventory.html', context)


@reports_access
def stock_alerts_report(request):
    sections = stock_alert_sections()
    total = sum(len(section['rows']) for section in sections)
    today = timezone.localdate()
    label = f"Current inventory snapshot · {today:%b %d, %Y}"
    export = request.GET.get('export', '')
    preview = request.GET.get('preview', '')
    headers = [
        'Inventory Type', 'Product Name', 'Batch Number',
        'Available / Affected Quantity', 'Expiration Date', 'Stock Status',
    ]
    rows = [
        [
            row['module_label'], row['product'], row['batch'],
            row['quantity'],
            row['expiration'].strftime('%Y-%m-%d') if row['expiration'] else '',
            row['status'],
        ]
        for section in sections for row in section['rows']
    ]

    if export == 'csv':
        return _csv_response(
            f"ABTC_Stock_Alerts_{today.isoformat()}.csv", headers, rows)

    if export in ('pdf', 'print'):
        summary_lines = [('Snapshot', f"Current inventory as of {today:%b %d, %Y}")]
        summary_lines += [
            (section['title'], str(len(section['rows']))) for section in sections
        ]
        if export == 'print':
            return _render_report_print(
                request, "Stock Alerts Report", label, headers, rows,
                summary_lines)
        pdf = _build_pdf(
            "Stock Alerts Report", label, headers, rows,
            summary_lines=summary_lines)
        inline = preview == '1' or request.GET.get('inline') == '1'
        return _pdf_response(
            pdf, f"ABTC_Stock_Alerts_{today.isoformat()}.pdf", inline=inline)

    return render(request, 'reports/stock_alerts.html', {
        'sections': sections,
        'total': total,
        'snapshot_date': today,
        'label': label,
        'pdf_title': 'Stock Alerts Report',
    })


# ---------- animal bite statistics ----------

def _distribution_context(request, distribution, label, report_type):
    return {
        'rows': distribution['rows'],
        'total': distribution['total'],
        'top': distribution['top'],
        'label': label,
        'report_type': report_type,
        'q': request.GET.get('q', ''),
    }


@reports_access
def barangay_report(request):
    start, end, report_type, label = _parse_dates(request, simplified=True)
    qs = _filter_cases(
        AnimalBiteCase.objects.all(), request, start, end, extra_filters=False)
    distribution = barangay_distribution(qs)
    export = request.GET.get('export', '')
    preview = request.GET.get('preview', '')
    headers = ['Barangay', 'Cases', 'Percentage']
    rows = [
        [row['name'], row['count'], f"{row['pct']}%"]
        for row in distribution['rows']
    ]

    if export == 'csv':
        return _csv_response(
            f"ABTC_Barangay_{date.today().isoformat()}.csv", headers, rows)

    if export in ('pdf', 'print'):
        summary_lines = _case_filter_summary(
            request, label, extra_filters=False) + [
            ('Total cases', str(distribution['total'])),
            ('Barangays', str(len(distribution['rows']))),
        ]
        if distribution['top']:
            summary_lines.append((
                'Highest',
                f"{distribution['top']['name']} ({distribution['top']['count']})",
            ))
        if export == 'print':
            return _render_report_print(
                request, "Animal Bite Cases by Barangay", label, headers, rows,
                summary_lines)
        pdf = _build_pdf(
            "Animal Bite Cases by Barangay", label, headers, rows,
            summary_lines=summary_lines)
        s, e = _export_period(start, end)
        inline = preview == '1' or request.GET.get('inline') == '1'
        return _pdf_response(
            pdf, f"ABTC_Barangay_{s}_{e}.pdf", inline=inline)

    context = _distribution_context(
        request, distribution, label, report_type)
    context['pdf_title'] = 'Animal Bite Cases by Barangay'
    return render(request, 'reports/barangay_report.html', context)


@reports_access
def animal_type_report(request):
    start, end, report_type, label = _parse_dates(request, simplified=True)
    qs = _filter_cases(
        AnimalBiteCase.objects.all(), request, start, end, extra_filters=False)
    distribution = animal_type_distribution(qs)
    export = request.GET.get('export', '')
    preview = request.GET.get('preview', '')
    headers = ['Animal Type', 'Cases', 'Percentage']
    rows = [
        [row['name'], row['count'], f"{row['pct']}%"]
        for row in distribution['rows']
    ]

    if export == 'csv':
        return _csv_response(
            f"ABTC_Animal_Type_{date.today().isoformat()}.csv", headers, rows)

    if export in ('pdf', 'print'):
        summary_lines = _case_filter_summary(
            request, label, extra_filters=False) + [
            ('Total cases', str(distribution['total'])),
            ('Animal types', str(len(distribution['rows']))),
        ]
        if distribution['top']:
            summary_lines.append((
                'Highest',
                f"{distribution['top']['name']} ({distribution['top']['count']})",
            ))
        if export == 'print':
            return _render_report_print(
                request, "Animal Bite Cases by Animal Type", label, headers,
                rows, summary_lines)
        pdf = _build_pdf(
            "Animal Bite Cases by Animal Type", label, headers, rows,
            summary_lines=summary_lines)
        s, e = _export_period(start, end)
        inline = preview == '1' or request.GET.get('inline') == '1'
        return _pdf_response(
            pdf, f"ABTC_Animal_Type_{s}_{e}.pdf", inline=inline)

    context = _distribution_context(
        request, distribution, label, report_type)
    context['pdf_title'] = 'Animal Bite Cases by Animal Type'
    return render(request, 'reports/animal_type_report.html', context)
