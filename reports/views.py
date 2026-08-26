from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from datetime import date, timedelta, datetime
import csv
import io
from patients.models import Patient
from bite_cases.models import AnimalBiteCase
from vaccination.models import VaccineDose, FollowUpRecord
from inventory.models import VaccineInventory

# ---------- helpers ----------
def _parse_dates(request):
    """Return start_date, end_date, report_type, preset label for filtering."""
    report_type = request.GET.get('type', 'custom')
    start_str = request.GET.get('start_date', '')
    end_str = request.GET.get('end_date', '')
    today = date.today()
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
                label = "All time"
        except ValueError:
            label = "All time"
    return start, end, report_type, label

def _filter_cases(qs, request, start, end):
    barangay = request.GET.get('barangay', '')
    animal = request.GET.get('animal', '')
    status = request.GET.get('status', '')
    exposure = request.GET.get('exposure', '')
    q = request.GET.get('q', '').strip()
    if start and end:
        qs = qs.filter(created_at__date__gte=start, created_at__date__lte=end)
    elif start:
        qs = qs.filter(created_at__date__gte=start)
    if barangay:
        qs = qs.filter(incident_barangay__icontains=barangay)
    if animal:
        qs = qs.filter(animal_type=animal)
    if status:
        qs = qs.filter(case_status=status)
    if exposure:
        qs = qs.filter(exposure_type=exposure)
    if q:
        qs = qs.filter(Q(case_number__icontains=q) | Q(patient__first_name__icontains=q) | Q(patient__last_name__icontains=q))
    return qs

def _pdf_response(pdf_bytes, filename, inline=False):
    resp = HttpResponse(pdf_bytes, content_type='application/pdf')
    disp = 'inline' if inline else 'attachment'
    resp['Content-Disposition'] = f'{disp}; filename="{filename}"'
    # allow iframe preview on same origin (fixes 127.0.0.1 refused to connect)
    resp['X-Frame-Options'] = 'SAMEORIGIN'
    # CSP frame-ancestors if present
    return resp

def _build_pdf(title, subtitle, headers, rows, summary_lines=None, landscape=False):
    """ReportLab PDF with emerald header, high-contrast table."""
    from reportlab.lib.pagesizes import A4, landscape as land, LETTER
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_CENTER
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
    from reportlab.lib.colors import HexColor

    buf = io.BytesIO()
    size = land(A4) if landscape else A4
    doc = SimpleDocTemplate(buf, pagesize=size, topMargin=14*mm, bottomMargin=12*mm, leftMargin=12*mm, rightMargin=12*mm,
                            title=title, author="ABTC System")

    emerald_950 = HexColor("#0a1f1a")
    emerald_800 = HexColor("#143d33")
    emerald_700 = HexColor("#1b4d3e")
    sage_line = HexColor("#c8d8c6")
    sage_50 = HexColor("#f2f5f1")
    muted = HexColor("#1e332b")

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle('h1', parent=styles['Heading1'], fontSize=14, leading=16, textColor=emerald_950, spaceAfter=2, fontName='Helvetica-Bold')
    h2 = ParagraphStyle('h2', parent=styles['Normal'], fontSize=8, leading=10, textColor=HexColor("#5a6b63"), spaceAfter=6, fontName='Helvetica')
    th_style = ParagraphStyle('th', parent=styles['Normal'], fontSize=7, leading=9, textColor=colors.white, fontName='Helvetica-Bold', alignment=TA_CENTER)
    td_style = ParagraphStyle('td', parent=styles['Normal'], fontSize=7, leading=9, textColor=muted, fontName='Helvetica', alignment=TA_LEFT)
    td_small = ParagraphStyle('tds', parent=td_style, fontSize=6.5, alignment=TA_CENTER)
    cell = lambda txt, style=td_style: Paragraph(str(txt) if txt is not None else "-", style)

    story = []
    # Header bar
    story.append(Paragraph("ABTC Management System", ParagraphStyle('brand', parent=styles['Normal'], fontSize=7, textColor=emerald_800, fontName='Helvetica-Bold', textTransform='uppercase', leading=9)))
    story.append(Paragraph("Municipal Health Office · Animal Bite Treatment Center", ParagraphStyle('sub', parent=styles['Normal'], fontSize=6, textColor=HexColor("#7a8b83"), leading=8)))
    story.append(HRFlowable(width="100%", thickness=0.6, color=emerald_800, spaceAfter=8, spaceBefore=4))
    story.append(Paragraph(title, h1))
    if subtitle:
        story.append(Paragraph(subtitle, h2))
    if summary_lines:
        for line in summary_lines:
            story.append(Paragraph(f"<b>{line[0]}:</b> {line[1]}", ParagraphStyle('sum', parent=styles['Normal'], fontSize=7, leading=10, textColor=muted)))
        story.append(Spacer(1, 6))
    story.append(Paragraph(f"Generated {datetime.now().strftime('%b %d, %Y %I:%M %p')} · Confidential", ParagraphStyle('meta', parent=styles['Normal'], fontSize=6, textColor=HexColor("#8aa589"), alignment=TA_CENTER, spaceAfter=8)))

    # Table
    col_count = len(headers)
    avail_w = (land(A4)[0] if landscape else A4[0]) - 24*mm
    if col_count <= 7:
        col_widths = [avail_w/col_count]*col_count
    else:
        col_widths = [avail_w*0.13, avail_w*0.18, avail_w*0.14, avail_w*0.12, avail_w*0.14, avail_w*0.15, avail_w*0.14][:col_count]
        if len(col_widths) < col_count:
            col_widths += [avail_w/col_count]*(col_count-len(col_widths))

    data = [[Paragraph(f"<b>{h}</b>", th_style) for h in headers]]
    for r in rows:
        data.append([cell(c, td_small if i>1 else td_style) for i, c in enumerate(r)])

    tbl = Table(data, colWidths=col_widths, repeatRows=1)
    style = TableStyle([
        ('BACKGROUND', (0,0), (-1,0), emerald_950),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('GRID', (0,0), (-1,-1), 0.4, sage_line),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, sage_50]),
        ('FONTSIZE', (0,0), (-1,-1), 7),
        ('LEFTPADDING', (0,0), (-1,-1), 4),
        ('RIGHTPADDING', (0,0), (-1,-1), 4),
        ('TOPPADDING', (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
    ])
    tbl.setStyle(style)
    story.append(tbl)
    story.append(Spacer(1, 12))
    story.append(HRFlowable(width="100%", thickness=0.4, color=sage_line, spaceAfter=6))
    story.append(Paragraph("ABTC · This report is system-generated and audited. For questions, contact the Municipal Health Office.", ParagraphStyle('foot', parent=styles['Normal'], fontSize=6, textColor=HexColor("#9bb5a0"), alignment=TA_CENTER)))

    def _footer(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 6)
        canvas.setFillColor(HexColor("#8aa589"))
        canvas.drawString(12*mm, 10*mm, f"Page {doc.page}")
        canvas.drawRightString(size[0]-12*mm, 10*mm, "ABTC System · Confidential")
        canvas.restoreState()
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()

@login_required
def reports_index(request):
    start, end, rtype, label = _parse_dates(request)
    qs = AnimalBiteCase.objects.all()
    qs = _filter_cases(qs, request, start, end)
    total = qs.count()
    by_status = qs.values('case_status').annotate(c=Count('id'))
    by_animal = qs.values('animal_type').annotate(c=Count('id'))
    total_patients = Patient.objects.filter(is_archived=False).count()
    if start and end:
        total_patients_filtered = Patient.objects.filter(created_at__date__gte=start, created_at__date__lte=end).count()
    else:
        total_patients_filtered = total_patients
    # barangay options
    barangays = AnimalBiteCase.objects.values_list('incident_barangay', flat=True).distinct().order_by('incident_barangay')[:100]
    context = {
        'total': total, 'by_status': by_status, 'by_animal': by_animal,
        'total_patients': total_patients_filtered,
        'barangays': barangays,
        'start_date': start.strftime('%Y-%m-%d') if start else '',
        'end_date': end.strftime('%Y-%m-%d') if end else '',
        'report_type': rtype, 'label': label,
        'selected_barangay': request.GET.get('barangay',''),
        'selected_animal': request.GET.get('animal',''),
        'selected_status': request.GET.get('status',''),
        'q': request.GET.get('q',''),
    }
    # quick stats for hub
    context['today'] = date.today()
    return render(request, 'reports/index.html', context)

@login_required
def cases_report(request):
    start, end, report_type, label = _parse_dates(request)
    title = label if label != "All time" else "All Cases"
    qs = _filter_cases(AnimalBiteCase.objects.select_related('patient'), request, start, end)

    # summary
    total = qs.count()
    by_status = qs.values('case_status').annotate(c=Count('id'))
    by_animal = qs.values('animal_type').annotate(c=Count('id'))
    by_barangay = qs.values('incident_barangay').annotate(c=Count('id')).order_by('-c')[:5]

    # barangay options for filter
    barangays = AnimalBiteCase.objects.values_list('incident_barangay', flat=True).distinct().order_by('incident_barangay')[:100]

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

    # PDF (preview inline or download)
    if export == 'pdf':
        rows = []
        for c in qs.select_related('patient').order_by('-created_at')[:2000]:
            rows.append([c.case_number, c.patient.full_name(), c.date_time_bite.strftime('%Y-%m-%d') if c.date_time_bite else '', c.get_animal_type_display(), c.get_exposure_type_display(), c.incident_barangay, c.get_case_status_display()])
        subtitle = label + (f" · {qs.count()} record(s)" if qs.count() else "")
        filters = []
        if request.GET.get('barangay'): filters.append(("Barangay", request.GET.get('barangay')))
        if request.GET.get('animal'): filters.append(("Animal", request.GET.get('animal')))
        if request.GET.get('status'): filters.append(("Status", request.GET.get('status')))
        summary = [
            ("Total cases", str(total)),
            ("Date range", label),
            ("Filtered", "Yes" if filters else "No"),
        ]
        for k,v in filters:
            summary.append((k, v))
        pdf = _build_pdf(title, subtitle, ["Case #","Patient","Bite Date","Animal","Exposure","Barangay","Status"], rows, summary_lines=summary)
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
        'barangays': barangays,
        'start_date': start.strftime('%Y-%m-%d') if start else '',
        'end_date': end.strftime('%Y-%m-%d') if end else '',
        'report_type': report_type,
        'selected_barangay': request.GET.get('barangay',''),
        'selected_animal': request.GET.get('animal',''),
        'selected_status': request.GET.get('status',''),
        'selected_exposure': request.GET.get('exposure',''),
        'q': request.GET.get('q',''),
        'has_filters': any([start, end, request.GET.get('barangay'), request.GET.get('animal'), request.GET.get('status')]),
    }
    return render(request, 'reports/cases_report.html', context)

@login_required
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

    if export == 'pdf':
        rows = [[i.vaccine_name, i.brand, i.batch_number, str(i.quantity_received), str(i.quantity_used), str(i.quantity_available), i.expiration_date.strftime('%Y-%m-%d') if i.expiration_date else ''] for i in items[:1500]]
        subtitle = label + f" · {items.count()} lot(s) · Received {total_received} · Used {total_used} · Available {total_available}"
        summary = [("Total lots", str(items.count())), ("Date range", label), ("Total received", str(total_received)), ("Total used", str(total_used)), ("Available", str(total_available))]
        pdf = _build_pdf("Vaccine Inventory Report", subtitle, ["Vaccine","Brand","Batch","Received","Used","Available","Expiry"], rows, summary_lines=summary, landscape=True)
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

@login_required
def missed_appointments_report(request):
    start, end, report_type, label = _parse_dates(request)
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
    if export == 'pdf':
        rows = [[r.bite_case.patient.full_name(), r.bite_case.case_number, r.follow_up_date.strftime('%Y-%m-%d'), r.get_status_display(), r.bite_case.patient.contact_number] for r in qs[:1500]]
        subtitle = label + f" · {qs.count()} missed"
        summary = [("Date range", label), ("Total missed", str(qs.count()))]
        pdf = _build_pdf("Missed Follow-Up Report", subtitle, ["Patient","Case #","Follow-Up Date","Status","Contact"], rows, summary_lines=summary)
        inline = preview=='1' or request.GET.get('inline')=='1'
        filename = f"ABTC_Missed_{start.isoformat() if start else 'all'}_{end.isoformat() if end else 'all'}.pdf"
        return _pdf_response(pdf, filename, inline=inline)

    from django.core.paginator import Paginator
    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'reports/missed_appointments.html', {'records': page_obj, 'page_obj': page_obj, 'label': label, 'report_type': report_type, 'start_date': start.strftime('%Y-%m-%d') if start else '', 'end_date': end.strftime('%Y-%m-%d') if end else '', 'q': q, 'has_filters': bool(start or end or q)})
