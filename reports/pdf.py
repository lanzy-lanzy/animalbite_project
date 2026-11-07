"""Shared ABTC official print/PDF building blocks.

Every generated document — reports, patient sheets, clinical records — uses the
same A4 portrait letterhead, forest-green palette, section headings, cream
information panels, table styling and footer.
"""
import io
from pathlib import Path
from xml.sax.saxutils import escape

from django.conf import settings
from django.utils import timezone

from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import (
    HRFlowable, Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer,
    Table, TableStyle,
)

INK = HexColor('#08291F')
SUBTITLE = HexColor('#496458')
BODY = HexColor('#26352F')
CREAM = HexColor('#FAF8F2')
CREAM_BORDER = HexColor('#DFCDAA')
TABLE_HEAD = HexColor('#F3F0E7')
GRID = HexColor('#D9DED9')
FOOTER = HexColor('#71847A')
WHITE = colors.white

LOGO_PATH = Path(settings.BASE_DIR) / 'static' / 'images' / 'lgu_dumingag_logo.png'

PAGE_MARGIN = 15 * mm
CONTENT_WIDTH = A4[0] - (2 * PAGE_MARGIN)

_STYLES = getSampleStyleSheet()

ORG_STYLE = ParagraphStyle(
    'abtc_org', parent=_STYLES['Normal'], fontName='Helvetica-Bold',
    fontSize=13.5, leading=15.5, textColor=INK)
OFFICE_STYLE = ParagraphStyle(
    'abtc_office', parent=_STYLES['Normal'], fontName='Helvetica',
    fontSize=8.5, leading=10.5, textColor=SUBTITLE)
DOC_TITLE_STYLE = ParagraphStyle(
    'abtc_title', parent=_STYLES['Normal'], fontName='Helvetica-Bold',
    fontSize=10.5, leading=12.5, textColor=INK, spaceBefore=3)
META_STYLE = ParagraphStyle(
    'abtc_meta', parent=_STYLES['Normal'], fontName='Helvetica',
    fontSize=8, leading=10, textColor=SUBTITLE)
SECTION_STYLE = ParagraphStyle(
    'abtc_section', parent=_STYLES['Normal'], fontName='Helvetica-Bold',
    fontSize=9, leading=11, textColor=INK, keepWithNext=True)
LABEL_STYLE = ParagraphStyle(
    'abtc_label', parent=_STYLES['Normal'], fontName='Helvetica',
    fontSize=8, leading=10, textColor=SUBTITLE)
VALUE_STYLE = ParagraphStyle(
    'abtc_value', parent=_STYLES['Normal'], fontName='Helvetica-Bold',
    fontSize=8.5, leading=10.5, textColor=INK)
TH_STYLE = ParagraphStyle(
    'abtc_th', parent=_STYLES['Normal'], fontName='Helvetica-Bold',
    fontSize=6.8, leading=8.4, textColor=INK, alignment=TA_LEFT)
TD_STYLE = ParagraphStyle(
    'abtc_td', parent=_STYLES['Normal'], fontName='Helvetica',
    fontSize=7.2, leading=9, textColor=BODY, alignment=TA_LEFT)
TD_CENTER_STYLE = ParagraphStyle(
    'abtc_td_center', parent=TD_STYLE, alignment=TA_CENTER)
EMPTY_STYLE = ParagraphStyle(
    'abtc_empty', parent=TD_STYLE, alignment=TA_CENTER, fontName='Helvetica-Oblique',
    textColor=SUBTITLE)
FOOT_STYLE = ParagraphStyle(
    'abtc_foot', parent=_STYLES['Normal'], fontName='Helvetica',
    fontSize=6.4, leading=8, textColor=FOOTER, alignment=TA_CENTER)


def printed_at_label():
    return timezone.localtime().strftime('%B %d, %Y · %I:%M %p')


def _p(text, style):
    return Paragraph(escape(str(text if text not in (None, '') else '—')), style)


def letterhead(title):
    """Logo left, organisation and document title right, followed by the rule."""
    logo = None
    if LOGO_PATH.exists():
        logo = Image(str(LOGO_PATH), width=24 * mm, height=24 * mm)
    heading = [
        Paragraph('ANIMAL BITE TREATMENT CENTER', ORG_STYLE),
        Paragraph('Municipal Health Office', OFFICE_STYLE),
        Paragraph(escape(str(title).upper()), DOC_TITLE_STYLE),
    ]
    cells = [logo or '', heading]
    table = Table([cells], colWidths=[26 * mm, CONTENT_WIDTH - 26 * mm])
    table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    return table


def _rule(space_after=8):
    return HRFlowable(
        width='100%', thickness=1.1, color=INK,
        spaceBefore=4, spaceAfter=space_after)


def _section_heading(text):
    bar = ''
    label_cell = Paragraph(escape(str(text).upper()), SECTION_STYLE)
    table = Table([[bar, label_cell]], colWidths=[1.6 * mm, CONTENT_WIDTH - 1.6 * mm])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, 0), INK),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (0, 0), 0),
        ('RIGHTPADDING', (0, 0), (0, 0), 0),
        ('LEFTPADDING', (1, 0), (1, 0), 7),
        ('TOPPADDING', (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
    ]))
    return table


def _summary_panel(summary_lines):
    rows = [
        [_p(label, LABEL_STYLE), _p(value, VALUE_STYLE)]
        for label, value in summary_lines
    ]
    table = Table(rows, colWidths=[CONTENT_WIDTH * 0.34, CONTENT_WIDTH * 0.66])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), CREAM),
        ('BOX', (0, 0), (-1, -1), 0.7, CREAM_BORDER),
        ('INNERGRID', (0, 0), (-1, -1), 0.4, CREAM_BORDER),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    return table


_LONG_HEADER_WORDS = (
    'patient', 'vaccine', 'medicine', 'product', 'brand', 'generic',
    'barangay', 'exposure', 'status', 'follow-up', 'nearest', 'stock',
    'dosage', 'unit', 'expiration', 'inventory',
)


def _column_widths(headers):
    weights = []
    for header in headers:
        key = str(header).casefold()
        weight = 1.0
        if '#' in key or 'number' in key:
            weight = 0.85
        elif any(word in key for word in _LONG_HEADER_WORDS):
            weight = 1.45
        elif 'date' in key:
            weight = 1.0
        weights.append(weight)
    total = sum(weights) or 1
    return [CONTENT_WIDTH * (weight / total) for weight in weights]


def _records_table(headers, rows):
    data = [[Paragraph(escape(str(header)), TH_STYLE) for header in headers]]
    if rows:
        numeric = (
            'received', 'used', 'dispensed', 'available', 'batches',
            'cases', 'percentage', 'quantity', 'count',
        )
        for row in rows:
            data.append([
                _p(value, TD_CENTER_STYLE)
                if index and any(word in str(headers[index]).casefold() for word in numeric)
                else _p(value, TD_STYLE)
                for index, value in enumerate(row)
            ])
    else:
        data.append([
            Paragraph('No records match this report.', EMPTY_STYLE)
        ] + [''] * (len(headers) - 1))
    table = Table(
        data, colWidths=_column_widths(headers),
        repeatRows=1, hAlign='LEFT')
    style = [
        ('BACKGROUND', (0, 0), (-1, 0), TABLE_HEAD),
        ('TEXTCOLOR', (0, 0), (-1, 0), INK),
        ('GRID', (0, 0), (-1, -1), 0.45, GRID),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]
    if not rows:
        style.append(('SPAN', (0, 1), (-1, 1)))
    table.setStyle(TableStyle(style))
    return table


class _NumberedCanvas(pdfcanvas.Canvas):
    """Two-pass canvas so every page can show "Page X of Y"."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._draw_footer(total_pages)
            super().showPage()
        super().save()

    def _draw_footer(self, total_pages):
        width = self._pagesize[0]
        self.saveState()
        self.setStrokeColor(GRID)
        self.setLineWidth(0.5)
        self.line(PAGE_MARGIN, 12 * mm, width - PAGE_MARGIN, 12 * mm)
        self.setFont('Helvetica', 6.4)
        self.setFillColor(FOOTER)
        self.drawCentredString(
            width / 2, 9.2 * mm,
            'Animal Bite Treatment Center · Municipal Health Office – Dumingag')
        self.drawCentredString(
            width / 2, 6.6 * mm,
            'System-Generated Document · Confidential — for clinical use only')
        self.setFont('Helvetica-Bold', 6.4)
        self.drawRightString(
            width - PAGE_MARGIN, 9.2 * mm,
            f'Page {self._pageNumber} of {total_pages}')
        self.restoreState()


def build_official_pdf(title, meta, headers, rows, summary_lines=None):
    """Build an A4 portrait official ABTC document (always portrait)."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        topMargin=PAGE_MARGIN, bottomMargin=18 * mm,
        leftMargin=PAGE_MARGIN, rightMargin=PAGE_MARGIN,
        title=str(title), author='ABTC System',
    )

    story = [letterhead(title), _rule()]
    meta_line = escape(str(meta)) if meta else ''
    if meta_line:
        story.append(Paragraph(
            f'{meta_line} &nbsp;·&nbsp; Date Printed: {escape(printed_at_label())}',
            META_STYLE))
    else:
        story.append(Paragraph(
            f'Date Printed: {escape(printed_at_label())}', META_STYLE))
    story.append(Spacer(1, 10))

    if summary_lines:
        story.append(_section_heading('Report Summary'))
        story.append(Spacer(1, 3))
        story.append(_summary_panel(summary_lines))
        story.append(Spacer(1, 12))

    story.append(_section_heading('Report Details'))
    story.append(Spacer(1, 3))
    story.append(_records_table(headers, rows))
    story.append(Spacer(1, 6))

    doc.build(story, canvasmaker=_NumberedCanvas)
    return buffer.getvalue()
