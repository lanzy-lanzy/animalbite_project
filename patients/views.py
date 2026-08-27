from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.urls import reverse
from datetime import datetime
import io
from .models import Patient, Barangay, VitalSign
from .forms import PatientForm, VitalSignForm
from audit.models import AuditLog

def generate_patient_number():
    last = Patient.objects.filter(patient_number__startswith='PAT-').order_by('-id').first()
    if last:
        num = int(last.patient_number.split('-')[1]) + 1
    else:
        num = 1
    return f"PAT-{num:05d}"

@login_required
def patient_list(request):
    query = request.GET.get('q', '')
    barangay = request.GET.get('barangay', '')
    sex = request.GET.get('sex', '')
    patients_qs = Patient.objects.filter(is_archived=False)

    if query:
        patients_qs = patients_qs.filter(
            Q(first_name__icontains=query) | Q(last_name__icontains=query) |
            Q(patient_number__icontains=query) | Q(contact_number__icontains=query)
        )
    if barangay:
        patients_qs = patients_qs.filter(barangay_id=barangay)
    if sex:
        patients_qs = patients_qs.filter(sex=sex)

    paginator = Paginator(patients_qs.order_by('-created_at'), 20)
    page = paginator.get_page(request.GET.get('page'))
    barangays = Barangay.objects.filter(is_active=True)

    return render(request, 'patients/patient_list.html', {
        'patients': page, 'barangays': barangays, 'query': query,
        'selected_barangay': barangay, 'selected_sex': sex,
    })

@login_required
def patient_create(request):
    if getattr(request.user, 'role', '') == 'patient':
        messages.error(request, 'Not authorized to register patients.')
        return redirect('patients:patient_list')
    if request.method == 'POST':
        form = PatientForm(request.POST)
        if form.is_valid():
            patient = form.save(commit=False)
            patient.patient_number = generate_patient_number()
            patient.created_by = request.user
            patient.save()
            AuditLog.objects.create(user=request.user, action='CREATE_PATIENT', description=f'Created patient {patient.patient_number} - {patient.full_name()}')
            # Auto-create patient login credentials
            temp_password = None
            username = None
            try:
                from utils.credentials import create_patient_user
                user, temp_password, created = create_patient_user(patient, created_by=request.user)
                username = user.username
                if created:
                    # Store in session for one-time display on detail page
                    request.session[f'temp_creds_{patient.pk}'] = {
                        'username': username,
                        'password': temp_password,
                        'created': True,
                    }
                    # Also try to send SMS with credentials via Semaphore (RHUDumingag)
                    try:
                        from vaccination.sms import send_semaphore_sms
                        from django.conf import settings
                        login_url = request.build_absolute_uri(reverse('accounts:login'))
                        sms_msg = (
                            f"ABTC Dumingag: Hello {patient.first_name}, your patient portal is ready. "
                            f"Username: {username} Temp Password: {temp_password}. "
                            f"Login at {login_url} and change password via Profile > Change Password. -RHUDumingag"
                        )
                        # Ensure not starting with TEST
                        sms_res = send_semaphore_sms(patient.contact_number, sms_msg)
                        if sms_res.get('success'):
                            messages.info(request, f'SMS with login sent to {patient.contact_number} via RHUDumingag.')
                        else:
                            # Fallback still show credentials; SMS failure is not critical
                            if not settings.SEMAPHORE_ENABLED:
                                messages.info(request, f'[DRY-RUN] SMS would be sent to {patient.contact_number} (SEMAPHORE_ENABLED=False).')
                    except Exception:
                        pass
                    messages.success(request, f'Patient {patient.patient_number} created. Login: {username} / Temp: {temp_password} — patient should change password after first login.')
                else:
                    messages.success(request, f'Patient {patient.patient_number} created successfully (login already exists: {username}).')
            except Exception as e:
                messages.success(request, f'Patient {patient.patient_number} created successfully.')
                messages.warning(request, f'Auto-login creation failed: {e}. Create manually via Users.')
            return redirect('patients:patient_detail', pk=patient.pk)
    else:
        form = PatientForm()
    return render(request, 'patients/patient_form.html', {'form': form, 'title': 'Register New Patient'})

@login_required
def patient_detail(request, pk):
    patient = get_object_or_404(Patient.objects.select_related('account', 'barangay'), pk=pk)
    bite_cases = patient.bite_cases.select_related().prefetch_related('vaccination_schedule__doses').order_by('-created_at') if hasattr(patient, 'bite_cases') else []
    # One-time temp credentials from session (set after auto-creation)
    temp_creds = request.session.pop(f'temp_creds_{patient.pk}', None)
    # Also check if there's a recent reset stored generically
    if not temp_creds:
        temp_creds = request.session.pop('temp_creds_last', None)
        if temp_creds and str(temp_creds.get('patient_pk')) != str(patient.pk):
            temp_creds = None
    # Vital signs — latest and recent for admin dynamic editing
    latest_vitals = patient.get_latest_vitals() if hasattr(patient, 'get_latest_vitals') else None
    recent_vitals = list(patient.vital_signs.select_related('taken_by').order_by('-taken_at')[:5]) if hasattr(patient, 'vital_signs') else []
    return render(request, 'patients/patient_detail.html', {'patient': patient, 'bite_cases': bite_cases, 'temp_creds': temp_creds, 'latest_vitals': latest_vitals, 'recent_vitals': recent_vitals})


@login_required
def patient_reset_password(request, pk):
    patient = get_object_or_404(Patient.objects.select_related('account'), pk=pk)
    # Only staff allowed (not patient)
    if getattr(request.user, 'role', '') == 'patient':
        messages.error(request, 'Not authorized.')
        return redirect('patients:patient_detail', pk=patient.pk)
    if request.method == 'POST':
        from utils.credentials import reset_patient_password, create_patient_user
        # If no account, create; else reset
        if patient.account_id:
            user, temp, _ = reset_patient_password(patient, created_by=request.user)
        else:
            user, temp, _ = create_patient_user(patient, created_by=request.user)
        # Store in session for display
        request.session[f'temp_creds_{patient.pk}'] = {
            'username': user.username,
            'password': temp,
            'created': False,
            'reset': True,
        }
        # Try SMS
        try:
            from vaccination.sms import send_semaphore_sms
            login_url = request.build_absolute_uri(reverse('accounts:login'))
            sms_msg = (
                f"ABTC Dumingag: Hello {patient.first_name}, your login was reset. "
                f"Username: {user.username} New Temp Password: {temp}. "
                f"Login at {login_url} and change password. -RHUDumingag"
            )
            res = send_semaphore_sms(patient.contact_number, sms_msg)
            if res.get('success'):
                messages.info(request, f'Password reset SMS sent to {patient.contact_number}.')
        except Exception:
            pass
        messages.success(request, f'Password reset for {patient.patient_number}. New temp: {temp} (user: {user.username}) — share securely and ask patient to change via Profile > Change Password.')
        return redirect('patients:patient_detail', pk=patient.pk)
    # GET shows confirm page? For now just redirect with POST required
    messages.info(request, 'Use the Reset button to generate a new temporary password.')
    return redirect('patients:patient_detail', pk=patient.pk)

@login_required
def patient_edit(request, pk):
    # Only staff/admin can edit patient info (same layout as print sheet)
    if getattr(request.user, 'role', '') == 'patient':
        messages.error(request, 'Not authorized to edit patient information.')
        return redirect('patients:patient_detail', pk=pk)
    patient = get_object_or_404(Patient, pk=pk)
    if request.method == 'POST':
        form = PatientForm(request.POST, instance=patient)
        if form.is_valid():
            form.save()
            AuditLog.objects.create(user=request.user, action='UPDATE_PATIENT', description=f'Updated patient {patient.patient_number}')
            messages.success(request, 'Patient updated successfully.')
            return redirect('patients:patient_detail', pk=patient.pk)
    else:
        form = PatientForm(instance=patient)
    return render(request, 'patients/patient_form.html', {'form': form, 'title': 'Edit Patient'})


@login_required
def patient_print(request, pk):
    """HTML printable sheet — emerald style, with stored vital signs if available."""
    patient = get_object_or_404(Patient.objects.select_related('barangay', 'account'), pk=pk)
    bite_cases = patient.bite_cases.select_related().prefetch_related('vaccination_schedule__doses').order_by('-created_at')[:5] if hasattr(patient, 'bite_cases') else []
    latest_case = bite_cases[0] if bite_cases else None
    # Latest vitals for filling the sheet
    latest_vitals = patient.get_latest_vitals() if hasattr(patient, 'get_latest_vitals') else None
    # Also recent vitals history for reference (last 3)
    recent_vitals = list(patient.vital_signs.order_by('-taken_at')[:3]) if hasattr(patient, 'vital_signs') else []
    ctx = {
        'patient': patient,
        'bite_cases': bite_cases,
        'latest_case': latest_case,
        'latest_vitals': latest_vitals,
        'recent_vitals': recent_vitals,
        'now': datetime.now(),
        'print_mode': True,
    }
    return render(request, 'patients/patient_print.html', ctx)


@login_required
def patient_print_pdf(request, pk):
    """Generate print-ready PDF for patient — emerald style, includes blank vital signs and vaccine schedule."""
    patient = get_object_or_404(Patient.objects.select_related('barangay', 'account'), pk=pk)
    try:
        bite_cases_qs = patient.bite_cases.select_related().prefetch_related('vaccination_schedule__doses').order_by('-created_at')
        bite_cases = list(bite_cases_qs[:5])
    except Exception:
        bite_cases = list(patient.bite_cases.order_by('-created_at')[:5]) if hasattr(patient, 'bite_cases') else []

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable, KeepTogether
    from reportlab.lib.colors import HexColor

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=12*mm, bottomMargin=10*mm, leftMargin=10*mm, rightMargin=10*mm,
        title=f"{patient.patient_number} - {patient.full_name()}",
        author="ABTC System"
    )

    emerald_950 = HexColor("#0a1f1a")
    emerald_800 = HexColor("#143d33")
    emerald_700 = HexColor("#1b4d3e")
    sage_line = HexColor("#c8d8c6")
    sage_50 = HexColor("#f2f5f1")
    sage_100 = HexColor("#e6ece5")
    muted = HexColor("#1e332b")
    light_muted = HexColor("#5a6b63")

    styles = getSampleStyleSheet()
    s_h1 = ParagraphStyle('h1', parent=styles['Heading1'], fontSize=13, leading=15, textColor=emerald_950, fontName='Helvetica-Bold', alignment=TA_CENTER, spaceAfter=2)
    s_h1sub = ParagraphStyle('h1sub', parent=styles['Normal'], fontSize=6.5, leading=8, textColor=light_muted, fontName='Helvetica', alignment=TA_CENTER, spaceAfter=4)
    s_section = ParagraphStyle('section', parent=styles['Normal'], fontSize=7, leading=9, textColor=colors.white, fontName='Helvetica-Bold', alignment=TA_LEFT)
    s_label = ParagraphStyle('label', parent=styles['Normal'], fontSize=6, leading=7, textColor=HexColor("#5a6b63"), fontName='Helvetica-Bold', alignment=TA_LEFT)
    s_value = ParagraphStyle('value', parent=styles['Normal'], fontSize=7.5, leading=9, textColor=emerald_950, fontName='Helvetica-Bold', alignment=TA_LEFT)
    s_value_small = ParagraphStyle('vsmall', parent=s_value, fontSize=7)
    s_td = ParagraphStyle('td', parent=styles['Normal'], fontSize=6.5, leading=8, textColor=muted, fontName='Helvetica', alignment=TA_LEFT)
    s_td_center = ParagraphStyle('tdc', parent=s_td, alignment=TA_CENTER)
    s_footer = ParagraphStyle('foot', parent=styles['Normal'], fontSize=6, leading=8, textColor=HexColor("#8aa589"), fontName='Helvetica', alignment=TA_CENTER)
    s_note = ParagraphStyle('note', parent=styles['Normal'], fontSize=6, leading=7.5, textColor=muted, fontName='Helvetica')

    cell = lambda txt, style=s_td: Paragraph(str(txt) if txt not in (None, '') else "-", style)
    label_cell = lambda txt: Paragraph(f'<font color="#5a6b63"><b>{txt}</b></font>', s_label)

    story = []

    # Header
    hdr_data = [[
        Paragraph('<b>ABTC</b> <font color="#1b4d3e">Management System</font>', ParagraphStyle('hdrL', parent=styles['Normal'], fontSize=8, leading=9, textColor=emerald_950, fontName='Helvetica-Bold')),
        Paragraph(f'{datetime.now().strftime("%b %d, %Y %I:%M %p")} &nbsp;·&nbsp; Generated by {request.user.get_full_name() or request.user.username}', ParagraphStyle('hdrR', parent=styles['Normal'], fontSize=6, leading=8, textColor=light_muted, fontName='Helvetica', alignment=TA_RIGHT)),
    ]]
    hdr_tbl = Table(hdr_data, colWidths=[85*mm, 85*mm])
    hdr_tbl.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
        ('RIGHTPADDING', (0,0), (-1,-1), 0),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2),
    ]))
    story.append(hdr_tbl)
    story.append(Paragraph("Municipal Health Office · Animal Bite Treatment Center — Dumingag, Zamboanga del Sur", ParagraphStyle('sub', parent=styles['Normal'], fontSize=6, leading=8, textColor=HexColor("#7a8b83"), fontName='Helvetica', alignment=TA_LEFT)))
    story.append(HRFlowable(width="100%", thickness=0.7, color=emerald_800, spaceAfter=6, spaceBefore=3))
    story.append(Paragraph("PATIENT INFORMATION SHEET", s_h1))
    story.append(Paragraph("For clinical use — present at triage · Vital signs to be filled on-site", s_h1sub))
    story.append(HRFlowable(width="100%", thickness=0.4, color=sage_line, spaceAfter=8, spaceBefore=2))

    initials = f"{(patient.first_name[:1] or '').upper()}{(patient.last_name[:1] or '').upper()}"
    pid = patient.patient_number
    pname = patient.full_name()
    age_sex = f"{patient.age}y · {patient.get_sex_display()} · {patient.get_civil_status_display()}"
    barangay_name = str(patient.barangay) if patient.barangay_id else (patient.address[:40] if patient.address else "-")
    band_data = [
        [Paragraph(f'<font color="white" size="13"><b>{initials}</b></font>', ParagraphStyle('init', parent=styles['Normal'], textColor=colors.white, fontName='Helvetica-Bold', alignment=TA_CENTER, fontSize=13, leading=15)), cell(f'<b>{pname}</b>', ParagraphStyle('pn', parent=styles['Normal'], fontSize=10, leading=12, textColor=colors.white, fontName='Helvetica-Bold')), cell(f'<b>{pid}</b><br/><font color="#c8d8c6" size="6">{age_sex}</font>', ParagraphStyle('pid', parent=styles['Normal'], fontSize=8, leading=9, textColor=colors.white, fontName='Helvetica-Bold', alignment=TA_RIGHT))],
    ]
    band = Table(band_data, colWidths=[18*mm, 110*mm, 42*mm])
    band.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), emerald_950),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('LEFTPADDING', (0,0), (-1,-1), 6),
        ('RIGHTPADDING', (0,0), (-1,-1), 6),
        ('TOPPADDING', (0,0), (-1,-1), 7),
        ('BOTTOMPADDING', (0,0), (-1,-1), 7),
        ('ROUNDEDCORNERS', [4,4,4,4]),
        ('BOX', (0,0), (-1,-1), 0.6, emerald_950),
    ]))
    story.append(band)
    story.append(Spacer(1, 6))
    contact_data = [[
        cell(f'<b>Contact:</b> {patient.contact_number}', ParagraphStyle('c1', parent=s_td, fontSize=6.5, textColor=muted)),
        cell(f'<b>Barangay:</b> {barangay_name}', ParagraphStyle('c2', parent=s_td, fontSize=6.5, textColor=muted)),
        cell(f'<b>Registered:</b> {patient.created_at.strftime("%b %d, %Y")}', ParagraphStyle('c3', parent=s_td, fontSize=6.5, textColor=muted, alignment=TA_RIGHT)),
    ]]
    c_tbl = Table(contact_data, colWidths=[60*mm, 60*mm, 50*mm])
    c_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), sage_50),
        ('BOX', (0,0), (-1,-1), 0.5, sage_line),
        ('INNERGRID', (0,0), (-1,-1), 0.4, sage_line),
        ('LEFTPADDING', (0,0), (-1,-1), 5),
        ('RIGHTPADDING', (0,0), (-1,-1), 5),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('ROUNDEDCORNERS', [3,3,3,3]),
    ]))
    story.append(c_tbl)
    story.append(Spacer(1, 8))

    def section_header(title):
        t = Table([[Paragraph(f'{title}', s_section)]], colWidths=[170*mm])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), emerald_800),
            ('LEFTPADDING', (0,0), (-1,-1), 6),
            ('TOPPADDING', (0,0), (-1,-1), 4),
            ('BOTTOMPADDING', (0,0), (-1,-1), 4),
            ('ROUNDEDCORNERS', [3,3,3,3]),
        ]))
        return t

    # Section 1: Demographics
    story.append(section_header("1 &nbsp;·&nbsp; Patient Demographics"))
    story.append(Spacer(1, 4))
    demo_rows = [
        [label_cell("PATIENT #"), cell(pid, s_value), label_cell("FULL NAME"), cell(pname, s_value)],
        [label_cell("BIRTHDATE"), cell(patient.birthdate.strftime("%b %d, %Y") if patient.birthdate else "-", s_value_small), label_cell("AGE / SEX"), cell(f"{patient.age} / {patient.get_sex_display()}", s_value_small)],
        [label_cell("CIVIL STATUS"), cell(patient.get_civil_status_display(), s_value_small), label_cell("BARANGAY"), cell(barangay_name, s_value_small)],
        [label_cell("CONTACT #"), cell(patient.contact_number, s_value_small), label_cell("ADDRESS"), cell(patient.address or "-", s_value_small)],
        [label_cell("PARENT / GUARDIAN"), cell(patient.parent_guardian or "—", s_value_small), label_cell("EMERGENCY CONTACT"), cell(f"{patient.emergency_contact_name or '—'}  {patient.emergency_contact_number or ''}".strip(), s_value_small)],
    ]
    demo_tbl = Table(demo_rows, colWidths=[28*mm, 57*mm, 30*mm, 55*mm])
    demo_tbl.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.4, sage_line),
        ('BACKGROUND', (0,0), (0,-1), sage_50),
        ('BACKGROUND', (2,0), (2,-1), sage_50),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('LEFTPADDING', (0,0), (-1,-1), 4),
        ('RIGHTPADDING', (0,0), (-1,-1), 4),
        ('TOPPADDING', (0,0), (-1,-1), 3.5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3.5),
    ]))
    story.append(demo_tbl)
    if patient.medical_history or patient.allergy_info or patient.previous_rabies_vax or patient.remarks:
        story.append(Spacer(1, 3))
        med_rows = []
        if patient.medical_history:
            med_rows.append([label_cell("MEDICAL HISTORY"), cell(patient.medical_history, s_td)])
        if patient.allergy_info:
            med_rows.append([label_cell("ALLERGIES"), cell(f"<b><font color=\"#991b1b\">{patient.allergy_info}</font></b>", s_td)])
        if patient.previous_rabies_vax:
            med_rows.append([label_cell("PREV. RABIES VAX"), cell(patient.previous_rabies_vax, s_td)])
        if patient.remarks:
            med_rows.append([label_cell("REMARKS"), cell(patient.remarks, s_td)])
        if med_rows:
            med_tbl = Table(med_rows, colWidths=[28*mm, 142*mm])
            med_tbl.setStyle(TableStyle([
                ('GRID', (0,0), (-1,-1), 0.4, sage_line),
                ('BACKGROUND', (0,0), (0,-1), HexColor("#fef3c7") if patient.allergy_info else sage_50),
                ('VALIGN', (0,0), (-1,-1), 'TOP'),
                ('LEFTPADDING', (0,0), (-1,-1), 4),
                ('RIGHTPADDING', (0,0), (-1,-1), 4),
                ('TOPPADDING', (0,0), (-1,-1), 3.5),
                ('BOTTOMPADDING', (0,0), (-1,-1), 3.5),
            ]))
            story.append(med_tbl)
    story.append(Spacer(1, 8))

    # Section 2: Vital signs — dynamic (shows stored if exists)
    latest_vitals = patient.get_latest_vitals() if hasattr(patient, 'get_latest_vitals') else None
    # Header hint — show taken info if exists
    if latest_vitals:
        taken_str = latest_vitals.taken_at.strftime("%m/%d/%Y  %H:%M") if latest_vitals.taken_at else "—"
        taken_by_str = str(latest_vitals.taken_by) if latest_vitals.taken_by else "—"
        vit_hint = f"<i>Last taken: <b>{taken_str}</b> &nbsp;by <b>{taken_by_str}</b> &nbsp;·&nbsp; Fill next on printed copy if needed.</i>"
    else:
        vit_hint = "<i>Staff: fill with ballpen on printed copy. Leave blank if not taken. Date/Time: &nbsp;_____ / _____ / _____ &nbsp;&nbsp; ___:___ &nbsp;&nbsp; Taken by: ______________________</i>"
    story.append(section_header("2 &nbsp;·&nbsp; Vital Signs &nbsp; <font color=\"#c8d8c6\" size=\"6\">— " + ("stored — editable by admin" if latest_vitals else "to be filled on-site (use blank fields)") + "</font>"))
    story.append(Spacer(1, 2))
    story.append(Paragraph(vit_hint, ParagraphStyle('vitals_hint', parent=styles['Normal'], fontSize=6, leading=8, textColor=HexColor("#5a6b63"), alignment=TA_CENTER, spaceAfter=4, fontName='Helvetica-Oblique')))
    def vit_cell(label, unit, value=None, blank_len=14):
        # If value is provided and not None/empty, show it bold; else show blank underscores
        if value is not None and str(value).strip() not in ("", "—", "None"):
            val_str = str(value)
            # For BP, value already formatted like "120/80"
            return Paragraph(f'<font color="#5a6b63" size="6"><b>{label}</b></font><br/><font color="#0a1f1a" size="9"><b>{val_str}</b></font><font color="#5a6b63" size="6"> {unit}</font><br/><font color="#8aa589" size="5">__________________</font>', ParagraphStyle('vit', parent=styles['Normal'], alignment=TA_CENTER, leading=9))
        blank = "_" * blank_len
        return Paragraph(f'<font color="#5a6b63" size="6"><b>{label}</b></font><br/><font color="#0a1f1a" size="9">{blank}</font><font color="#5a6b63" size="6"> {unit}</font><br/><font color="#8aa589" size="5">__________________</font>', ParagraphStyle('vit', parent=styles['Normal'], alignment=TA_CENTER, leading=9))
    # Prepare values from latest_vitals
    if latest_vitals:
        bp_val = latest_vitals.bp_display if latest_vitals.bp_display else None
        temp_val = f"{latest_vitals.temperature}" if latest_vitals.temperature is not None else None
        pulse_val = f"{latest_vitals.pulse_rate}" if latest_vitals.pulse_rate is not None else None
        rr_val = f"{latest_vitals.respiratory_rate}" if latest_vitals.respiratory_rate is not None else None
        spo2_val = f"{latest_vitals.spo2}" if latest_vitals.spo2 is not None else None
        wt_val = f"{latest_vitals.weight}" if latest_vitals.weight is not None else None
        ht_val = f"{latest_vitals.height}" if latest_vitals.height is not None else None
        bmi_val = f"{latest_vitals.bmi}" if latest_vitals.bmi is not None else None
        pain_val = f"{latest_vitals.pain_score}" if latest_vitals.pain_score is not None else None
        glucose_val = f"{latest_vitals.blood_glucose}" if latest_vitals.blood_glucose is not None else None
        glucose_type = latest_vitals.glucose_type or ""
        appearance = latest_vitals.general_appearance or ""
        notes_val = latest_vitals.notes or ""
        # For radio-like fields, show filled circle if matches
        def circ(chosen, val):
            return "●" if chosen == val else "○"
        glucose_para = f'<font color="#5a6b63" size="6"><b>RANDOM / FASTING</b></font><br/><font size="6">{circ(glucose_type,"random")} Random &nbsp; {circ(glucose_type,"fasting")} Fasting</font>'
        appearance_para = f'<font color="#5a6b63" size="6"><b>GENERAL APPEARANCE</b></font><br/><font size="6">{circ(appearance,"well")} Well &nbsp; {circ(appearance,"ill")} Ill &nbsp; {circ(appearance,"toxic")} Toxic</font>'
    else:
        bp_val = temp_val = pulse_val = rr_val = spo2_val = wt_val = ht_val = bmi_val = pain_val = glucose_val = None
        glucose_para = '<font color="#5a6b63" size="6"><b>RANDOM / FASTING</b></font><br/><font size="6">○ Random &nbsp; ○ Fasting</font>'
        appearance_para = '<font color="#5a6b63" size="6"><b>GENERAL APPEARANCE</b></font><br/><font size="6">○ Well &nbsp; ○ Ill &nbsp; ○ Toxic</font>'
        notes_val = ""
    v_rows = [
        [vit_cell("BLOOD PRESSURE", "mmHg", bp_val, 10), vit_cell("TEMPERATURE", "°C", temp_val, 8), vit_cell("PULSE RATE", "bpm", pulse_val, 8), vit_cell("RESP. RATE", "/min", rr_val, 8)],
        [vit_cell("SpO<sub>2</sub>", "%", spo2_val, 8), vit_cell("WEIGHT", "kg", wt_val, 8), vit_cell("HEIGHT", "cm", ht_val, 8), vit_cell("BMI", "", bmi_val, 8)],
        [vit_cell("PAIN SCORE", "/10", pain_val, 8), vit_cell("BLOOD GLUCOSE", "mg/dL", glucose_val, 8), Paragraph(glucose_para, ParagraphStyle('vit2', parent=styles['Normal'], alignment=TA_CENTER, leading=9)), Paragraph(appearance_para, ParagraphStyle('vit3', parent=styles['Normal'], alignment=TA_CENTER, leading=9))],
    ]
    v_tbl = Table(v_rows, colWidths=[42.5*mm]*4)
    v_tbl.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.5, sage_line),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('LEFTPADDING', (0,0), (-1,-1), 4),
        ('RIGHTPADDING', (0,0), (-1,-1), 4),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('ROWBACKGROUNDS', (0,0), (-1,-1), [colors.white, sage_50, colors.white]),
    ]))
    story.append(v_tbl)
    story.append(Spacer(1, 2))
    if 'notes_val' in locals() and notes_val:
        story.append(Paragraph(f"<i>Additional notes / allergies check — nurse to confirm before vaccination:</i> &nbsp; <b>{notes_val}</b>", s_note))
        story.append(Spacer(1, 4))
    else:
        story.append(Paragraph("<i>Additional notes / allergies check — nurse to confirm before vaccination:</i> &nbsp; _______________________________________________________________________________", s_note))
        story.append(Paragraph("&nbsp; _______________________________________________________________________________", s_note))
    story.append(Spacer(1, 8))

    # Section 3: Clinical Assessment
    story.append(section_header("3 &nbsp;·&nbsp; Initial Assessment <font color=\"#c8d8c6\" size=\"6\">— blank for clinician</font>"))
    story.append(Spacer(1, 4))
    assessment_data = [
        [cell("<b>Chief Complaint / History of Present Illness:</b><br/><br/>___________________________________________________________________________________________<br/>___________________________________________________________________________________________<br/>___________________________________________________________________________________________", ParagraphStyle('ass', parent=s_td, leading=9))],
        [cell("<b>Exposure Narrative (if animal bite):</b> Animal: ________ &nbsp; Owned / Stray &nbsp; Vax: ________ &nbsp; Wound wash: ○ Yes ○ No<br/>Body part: _________________ &nbsp; No. of wounds: ______ &nbsp; Date/time of bite: ______________________<br/>___________________________________________________________________________________________", ParagraphStyle('ass2', parent=s_td, leading=9))],
        [cell("<b>Physical Exam / System Review:</b><br/><br/>HEENT: ________________________________ &nbsp; Chest/Lungs: ________________________________<br/>Cardiac: ________________________________ &nbsp; Abdomen: ________________________________<br/>Skin/Wound description: __________________________________________________________________<br/>___________________________________________________________________________________________", ParagraphStyle('ass3', parent=s_td, leading=9))],
        [cell("<b>Assessment & Plan:</b><br/><br/>○ Category I &nbsp; ○ Category II &nbsp; ○ Category III &nbsp;&nbsp; | &nbsp; PEP: ○ Yes ○ No &nbsp; RIG: ○ Yes ○ No &nbsp; TT: ○ Yes ○ No<br/>Plan / Vaccine brand / Dose schedule: _____________________________________________________<br/>___________________________________________________________________________________________", ParagraphStyle('ass4', parent=s_td, leading=9))],
    ]
    ass_tbl = Table(assessment_data, colWidths=[170*mm])
    ass_tbl.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.4, sage_line),
        ('BACKGROUND', (0,0), (-1,0), sage_50),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING', (0,0), (-1,-1), 5),
        ('RIGHTPADDING', (0,0), (-1,-1), 5),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
    ]))
    story.append(ass_tbl)
    story.append(Spacer(1, 8))

    # Section 4: Vaccination Schedule — ADDED, emerald style
    story.append(section_header("4 &nbsp;·&nbsp; Vaccination Schedule <font color=\"#c8d8c6\" size=\"6\">— ABTC use — tick / sign when given</font>"))
    story.append(Spacer(1, 4))
    has_sched = any(getattr(c, 'vaccination_schedule', None) for c in bite_cases)
    if bite_cases and has_sched:
        for case in bite_cases:
            sched = getattr(case, 'vaccination_schedule', None)
            if not sched:
                continue
            story.append(Paragraph(f"<b>Case {case.case_number}</b> &nbsp;·&nbsp; {case.date_time_bite.strftime('%b %d, %Y') if case.date_time_bite else ''} &nbsp;·&nbsp; {case.get_animal_type_display()} · {case.get_exposure_type_display()} &nbsp;<font color=\"#5a6b63\">· {case.incident_barangay} · {case.get_case_status_display()} · {sched.doses.count()} dose(s)</font>", ParagraphStyle('sch_h', parent=styles['Normal'], fontSize=6.5, leading=8, textColor=emerald_950, fontName='Helvetica', spaceAfter=3, spaceBefore=4)))
            doses = list(sched.doses.all().order_by('scheduled_date')) if hasattr(sched.doses, 'all') else []
            if doses:
                hdr = ["#", "Dose", "Scheduled Date", "Date Given", "Brand / Lot", "Status", "Site / Route", "Given by / Sign"]
                header_row = [Paragraph(f"<b>{h}</b>", ParagraphStyle('thv', parent=styles['Normal'], fontSize=5.5, leading=7, textColor=colors.white, fontName='Helvetica-Bold', alignment=TA_CENTER)) for h in hdr]
                data = [header_row]
                for idx_d, d in enumerate(doses, 1):
                    status = d.get_dose_status_display() if hasattr(d, 'get_dose_status_display') else d.dose_status
                    # Brand
                    brand = (d.vaccine_brand or "") + (f" · {d.batch_number}" if getattr(d, 'batch_number', None) else "")
                    if not brand.strip():
                        brand = "—"
                    data.append([
                        Paragraph(str(idx_d), ParagraphStyle(f'c{idx_d}', parent=s_td_center, fontSize=6)),
                        Paragraph(d.dose_label or f"Dose {idx_d}", ParagraphStyle(f'd{idx_d}', parent=s_td, fontName='Helvetica-Bold', fontSize=6.5)),
                        Paragraph(d.scheduled_date.strftime("%b %d, %Y") if d.scheduled_date else "—", ParagraphStyle(f'sd{idx_d}', parent=s_td_center, fontSize=6)),
                        Paragraph(d.actual_date.strftime("%b %d, %Y") if getattr(d,'actual_date',None) and d.actual_date else "—", ParagraphStyle(f'ad{idx_d}', parent=s_td_center, fontSize=6)),
                        Paragraph(brand, ParagraphStyle(f'br{idx_d}', parent=s_td, fontSize=6)),
                        Paragraph(f"{status}", ParagraphStyle(f'st{idx_d}', parent=s_td_center, fontSize=6)),
                        Paragraph("—", s_td_center),
                        Paragraph("______________", ParagraphStyle(f'sg{idx_d}', parent=s_td_center, fontSize=6, textColor=light_muted)),
                    ])
                cw = [8*mm, 18*mm, 28*mm, 28*mm, 32*mm, 22*mm, 20*mm, 24*mm]
                t = Table(data, colWidths=cw, repeatRows=1)
                t.setStyle(TableStyle([
                    ('BACKGROUND', (0,0), (-1,0), emerald_950),
                    ('TEXTCOLOR', (0,0), (-1,0), colors.white),
                    ('ALIGN', (0,0), (-1,-1), 'CENTER'),
                    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                    ('GRID', (0,0), (-1,-1), 0.4, sage_line),
                    ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, sage_50]),
                    ('LEFTPADDING', (0,0), (-1,-1), 3),
                    ('RIGHTPADDING', (0,0), (-1,-1), 3),
                    ('TOPPADDING', (0,0), (-1,-1), 3),
                    ('BOTTOMPADDING', (0,0), (-1,-1), 3),
                ]))
                story.append(t)
                if getattr(sched, 'doctor_notes', None):
                    story.append(Spacer(1,2))
                    story.append(Paragraph(f"<b>Notes:</b> {sched.doctor_notes}", ParagraphStyle('sch_n', parent=styles['Normal'], fontSize=6, leading=7, textColor=muted)))
            else:
                story.append(Paragraph("No doses recorded for this schedule.", ParagraphStyle('nod', parent=styles['Normal'], fontSize=6.5, textColor=light_muted, fontName='Helvetica-Oblique', alignment=TA_CENTER, borderPadding=(6,6,6), borderColor=sage_line, borderWidth=0.4, backColor=sage_50)))
            story.append(Spacer(1, 3))
        story.append(Paragraph("<i>Bring this sheet at every visit. Tick & sign when dose given. Scheduled doses highlight next due.</i>", ParagraphStyle('leg', parent=styles['Normal'], fontSize=6, leading=7, textColor=light_muted, fontName='Helvetica-Oblique', alignment=TA_CENTER, spaceBefore=4)))
        story.append(Spacer(1, 6))
    elif bite_cases:
        # Cases exist but no schedule — show placeholder per case + blank template
        for case in bite_cases:
            if not getattr(case, 'vaccination_schedule', None):
                story.append(Paragraph(f"<b>Case {case.case_number}</b> &nbsp;·&nbsp; {case.date_time_bite.strftime('%b %d, %Y') if case.date_time_bite else ''} — <font color=\"#5a6b63\">No vaccination schedule yet</font>", ParagraphStyle('ch2', parent=styles['Normal'], fontSize=6.5, leading=8, textColor=emerald_950, fontName='Helvetica', spaceAfter=2)))
                story.append(Paragraph("Schedule to be created by ABTC clinician. Standard Essen regimen (Days 0, 3, 7, 14, 28) will appear here once generated.", ParagraphStyle('ph', parent=styles['Normal'], fontSize=6.5, textColor=light_muted, fontName='Helvetica-Oblique', alignment=TA_LEFT, borderPadding=(4,4,4), borderColor=sage_line, borderWidth=0.3, backColor=sage_50, spaceAfter=4)))
        # Blank template
        story.append(Paragraph("<b>Standard Post-Exposure Prophylaxis (Essen 5-dose IM) — blank template</b> &nbsp;<font color=\"#5a6b63\">to be scheduled by clinician</font>", ParagraphStyle('std', parent=styles['Normal'], fontSize=6.5, leading=8, textColor=emerald_950, fontName='Helvetica', spaceAfter=3, spaceBefore=4)))
        data = [[Paragraph(f"<b>{h}</b>", ParagraphStyle('thb', parent=styles['Normal'], fontSize=5.5, leading=7, textColor=colors.white, fontName='Helvetica-Bold', alignment=TA_CENTER)) for h in ["#", "Dose", "Scheduled Date", "Date Given", "Brand / Lot", "Status", "Site / Route", "Given by"]]]
        for i, label in enumerate(["Day 0", "Day 3", "Day 7", "Day 14", "Day 28"], 1):
            data.append([
                Paragraph(str(i), s_td_center),
                Paragraph(label, ParagraphStyle(f'lb{i}', parent=s_td, fontName='Helvetica-Bold', fontSize=6.5)),
                Paragraph("____ / ____ / ____", ParagraphStyle(f'sd{i}', parent=s_td_center, fontSize=6)),
                Paragraph("", s_td_center),
                Paragraph("", s_td_center),
                Paragraph("Pending", s_td_center),
                Paragraph("", s_td_center),
                Paragraph("______________", ParagraphStyle(f'sg{i}', parent=s_td_center, textColor=light_muted, fontSize=6)),
            ])
        data.append([Paragraph("—", s_td_center), Paragraph("RIG", s_td_center), Paragraph("if indicated", s_td_center), Paragraph("", s_td_center), Paragraph("", s_td_center), Paragraph("—", s_td_center), Paragraph("", s_td_center), Paragraph("", s_td_center)])
        t = Table(data, colWidths=[8*mm, 18*mm, 28*mm, 28*mm, 32*mm, 22*mm, 20*mm, 24*mm])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), emerald_950),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('GRID', (0,0), (-1,-1), 0.4, sage_line),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('LEFTPADDING', (0,0), (-1,-1), 3),
            ('RIGHTPADDING', (0,0), (-1,-1), 3),
            ('TOPPADDING', (0,0), (-1,-1), 3),
            ('BOTTOMPADDING', (0,0), (-1,-1), 3),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, sage_50]),
        ]))
        story.append(t)
        story.append(Spacer(1,4))
    else:
        story.append(Paragraph("<b>Standard Post-Exposure Prophylaxis (Essen 5-dose IM)</b> — to be scheduled by clinician. Fill actual dates on-site.", ParagraphStyle('std0', parent=styles['Normal'], fontSize=6.5, leading=8, textColor=muted, fontName='Helvetica', spaceAfter=3)))
        data = [[Paragraph(f"<b>{h}</b>", ParagraphStyle('th0', parent=styles['Normal'], fontSize=5.5, leading=7, textColor=colors.white, fontName='Helvetica-Bold', alignment=TA_CENTER)) for h in ["#", "Dose", "Scheduled Date", "Date Given", "Brand / Lot", "Status", "Site / Route", "Given by"]]]
        for i, label in enumerate(["Day 0", "Day 3", "Day 7", "Day 14", "Day 28"], 1):
            data.append([
                Paragraph(str(i), s_td_center),
                Paragraph(label, ParagraphStyle(f'la{i}', parent=s_td, fontName='Helvetica-Bold', fontSize=6.5)),
                Paragraph("____ / ____ / ____", s_td_center),
                Paragraph("", s_td_center),
                Paragraph("", s_td_center),
                Paragraph("Pending", s_td_center),
                Paragraph("", s_td_center),
                Paragraph("______________", ParagraphStyle(f'si{i}', parent=s_td_center, textColor=light_muted)),
            ])
        data.append([Paragraph("—", s_td_center), Paragraph("RIG", s_td_center), Paragraph("if indicated", s_td_center), Paragraph("", s_td_center), Paragraph("", s_td_center), Paragraph("—", s_td_center), Paragraph("", s_td_center), Paragraph("", s_td_center)])
        t = Table(data, colWidths=[8*mm, 18*mm, 28*mm, 28*mm, 32*mm, 22*mm, 20*mm, 24*mm])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), emerald_950),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('GRID', (0,0), (-1,-1), 0.4, sage_line),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('LEFTPADDING', (0,0), (-1,-1), 3),
            ('RIGHTPADDING', (0,0), (-1,-1), 3),
            ('TOPPADDING', (0,0), (-1,-1), 3),
            ('BOTTOMPADDING', (0,0), (-1,-1), 3),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, sage_50]),
        ]))
        story.append(t)
        story.append(Spacer(1,6))

    # Section 5: Recent Bite Cases
    story.append(section_header("5 &nbsp;·&nbsp; Recent Bite Cases <font color=\"#c8d8c6\" size=\"6\">— for reference</font>"))
    story.append(Spacer(1, 4))
    if bite_cases:
        case_headers = ["Case #", "Date", "Animal", "Exposure", "Status"]
        case_data = [[cell(f"<b>{h}</b>", ParagraphStyle('ch', parent=s_td, alignment=TA_CENTER, textColor=colors.white, fontName='Helvetica-Bold')) for h in case_headers]]
        for c in bite_cases:
            case_data.append([
                cell(c.case_number, s_td_center),
                cell(c.date_time_bite.strftime("%Y-%m-%d") if c.date_time_bite else "-", s_td_center),
                cell(c.get_animal_type_display(), s_td_center),
                cell(c.get_exposure_type_display(), s_td_center),
                cell(c.get_case_status_display(), s_td_center),
            ])
        cw = [30*mm, 28*mm, 28*mm, 32*mm, 52*mm]
        ctbl = Table(case_data, colWidths=cw)
        ctbl.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), emerald_950),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('GRID', (0,0), (-1,-1), 0.4, sage_line),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, sage_50]),
            ('LEFTPADDING', (0,0), (-1,-1), 3),
            ('RIGHTPADDING', (0,0), (-1,-1), 3),
            ('TOPPADDING', (0,0), (-1,-1), 3),
            ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ]))
        story.append(ctbl)
    else:
        story.append(Paragraph("<i>No bite cases recorded yet — this sheet will serve as baseline intake form.</i>", ParagraphStyle('no_case', parent=styles['Normal'], fontSize=7, leading=9, textColor=light_muted, alignment=TA_CENTER, fontName='Helvetica-Oblique', borderPadding=(6,6,6), borderColor=sage_line, borderWidth=0.5, backColor=sage_50)))
    story.append(Spacer(1, 8))

    # Signature footer
    sig_data = [
        [Paragraph("____________________________<br/><font size=\"6\" color=\"#5a6b63\"><b>Patient / Guardian</b><br/>Signature over printed name & date</font>", ParagraphStyle('sig', parent=styles['Normal'], alignment=TA_CENTER, leading=8)),
         Paragraph("____________________________<br/><font size=\"6\" color=\"#5a6b63\"><b>Triage Nurse</b><br/>Signature & date/time</font>", ParagraphStyle('sig2', parent=styles['Normal'], alignment=TA_CENTER, leading=8)),
         Paragraph("____________________________<br/><font size=\"6\" color=\"#5a6b63\"><b>Physician / ABTC Doctor</b><br/>Signature & PRC no.</font>", ParagraphStyle('sig3', parent=styles['Normal'], alignment=TA_CENTER, leading=8)),
        ],
        [Paragraph("Date: ______ / ______ / ______", s_td_center), Paragraph("Time: ______ : ______", s_td_center), Paragraph("Date: ______ / ______ / ______", s_td_center)],
    ]
    sig_tbl = Table(sig_data, colWidths=[56*mm, 56*mm, 58*mm])
    sig_tbl.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING', (0,0), (-1,-1), 6),
        ('RIGHTPADDING', (0,0), (-1,-1), 6),
        ('TOPPADDING', (0,0), (-1,-1), 12),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('LINEBELOW', (0,1), (-1,1), 0.3, sage_line),
    ]))
    story.append(KeepTogether(sig_tbl))
    story.append(Spacer(1, 10))
    story.append(HRFlowable(width="100%", thickness=0.4, color=sage_line, spaceAfter=4, spaceBefore=6))
    story.append(Paragraph("ABTC · This sheet is system-generated and confidential. Bring this form at every visit. For questions, contact MHO Dumingag. · <b>ABTC System</b> · Page 1 of 1", s_footer))
    story.append(Paragraph(f"Patient {pid} · Printed {datetime.now().strftime('%b %d, %Y %I:%M %p')} by {request.user.get_full_name() or request.user.username}", ParagraphStyle('foot2', parent=s_footer, fontSize=5.5, textColor=HexColor("#9bb5a0"))))

    def _footer(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 5.5)
        canvas.setFillColor(HexColor("#8aa589"))
        canvas.drawString(10*mm, 8*mm, f"Page {doc.page}")
        canvas.drawRightString(A4[0]-10*mm, 8*mm, f"{pid} · {pname} · ABTC Confidential")
        canvas.setStrokeColor(sage_line)
        canvas.setLineWidth(0.4)
        canvas.line(10*mm, 9.5*mm, A4[0]-10*mm, 9.5*mm)
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    pdf_bytes = buf.getvalue()
    buf.close()

    resp = HttpResponse(pdf_bytes, content_type='application/pdf')
    inline = request.GET.get('preview') == '1' or request.GET.get('inline') == '1'
    disp = 'inline' if inline else 'attachment'
    resp['Content-Disposition'] = f'{disp}; filename="ABTC_Patient_{pid}_{datetime.now().strftime("%Y%m%d")}.pdf"'
    resp['X-Frame-Options'] = 'SAMEORIGIN'
    return resp


# ---- Vital Signs — dynamic admin input ----
@login_required
def patient_vital_create(request, pk):
    """Admin dynamically inputs vital signs for a patient — same layout as print grid."""
    if getattr(request.user, 'role', '') == 'patient':
        messages.error(request, 'Not authorized to input vital signs.')
        return redirect('patients:patient_detail', pk=pk)
    patient = get_object_or_404(Patient, pk=pk)
    # Support HTMX or normal POST
    if request.method == 'POST':
        form = VitalSignForm(request.POST)
        if form.is_valid():
            vital = form.save(commit=False)
            vital.patient = patient
            vital.taken_by = request.user
            vital.save()
            AuditLog.objects.create(user=request.user, action='CREATE_VITALS', description=f'Added vitals for {patient.patient_number} ({vital.taken_at:%Y-%m-%d %H:%M})')
            messages.success(request, 'Vital signs saved.')
            nxt = request.POST.get('next') or request.GET.get('next')
            if nxt:
                return redirect(nxt)
            return redirect('patients:patient_detail', pk=patient.pk)
    else:
        form = VitalSignForm()
    # Allow prefill from latest if exists (copy forward)
    latest = patient.get_latest_vitals()
    next_url = request.GET.get('next') or request.POST.get('next') or ''
    context = {
        'form': form,
        'patient': patient,
        'latest_vitals': latest,
        'title': f'Add Vital Signs — {patient.patient_number}',
        'is_create': True,
        'next_url': next_url,
    }
    # If HTMX request, return partial
    if request.headers.get('HX-Request'):
        return render(request, 'patients/vital_form_partial.html', context)
    return render(request, 'patients/vital_form.html', context)


@login_required
def patient_vital_edit(request, vital_id):
    vital = get_object_or_404(VitalSign.objects.select_related('patient'), pk=vital_id)
    patient = vital.patient
    if getattr(request.user, 'role', '') == 'patient':
        messages.error(request, 'Not authorized.')
        return redirect('patients:patient_detail', pk=patient.pk)
    if request.method == 'POST':
        form = VitalSignForm(request.POST, instance=vital)
        if form.is_valid():
            vital = form.save()
            # keep taken_by as who last edited
            vital.taken_by = request.user
            vital.save(update_fields=['taken_by', 'updated_at'])
            AuditLog.objects.create(user=request.user, action='UPDATE_VITALS', description=f'Updated vitals {vital.pk} for {patient.patient_number}')
            messages.success(request, 'Vital signs updated.')
            nxt = request.POST.get('next') or request.GET.get('next')
            if nxt:
                return redirect(nxt)
            return redirect('patients:patient_detail', pk=patient.pk)
    else:
        form = VitalSignForm(instance=vital)
    next_url = request.GET.get('next') or request.POST.get('next') or ''
    context = {'form': form, 'patient': patient, 'vital': vital, 'title': f'Edit Vital Signs — {patient.patient_number}', 'is_create': False, 'next_url': next_url}
    if request.headers.get('HX-Request'):
        return render(request, 'patients/vital_form_partial.html', context)
    return render(request, 'patients/vital_form.html', context)


@login_required
def patient_vital_delete(request, vital_id):
    vital = get_object_or_404(VitalSign, pk=vital_id)
    patient = vital.patient
    if getattr(request.user, 'role', '') == 'patient':
        messages.error(request, 'Not authorized.')
        return redirect('patients:patient_detail', pk=patient.pk)
    if request.method == 'POST':
        patient_pk = patient.pk
        vital.delete()
        AuditLog.objects.create(user=request.user, action='DELETE_VITALS', description=f'Deleted vitals for {patient.patient_number}')
        messages.success(request, 'Vital signs deleted.')
        return redirect('patients:patient_detail', pk=patient_pk)
    return render(request, 'patients/vital_confirm_delete.html', {'vital': vital, 'patient': patient})
