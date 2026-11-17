"""Portable Arabic PDF summaries of APPROVED results, using a bundled licensed font."""

from datetime import datetime
from zoneinfo import ZoneInfo
import io
from xml.sax.saxutils import escape
import arabic_reshaper
from bidi.algorithm import get_display
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from config import ROOT


def _arabic(text):
    return get_display(arabic_reshaper.reshape(str(text)))


def pdf_bytes(summary, organization_name="SmartGrade AI"):
    if len(summary) > 2000:
        raise ValueError("تقرير PDF يقتصر على 2000 نتيجة؛ استخدم Excel لتقارير أكبر")
    font_name = "SmartGradeArabic"
    if font_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(font_name, str(ROOT / "assets/fonts/DejaVuSans.ttf")))
    out = io.BytesIO()
    document = SimpleDocTemplate(
        out,
        pagesize=A4,
        rightMargin=32,
        leftMargin=32,
        topMargin=36,
        bottomMargin=38,
        title="SmartGrade - Approved results",
        author="SmartGrade AI",
    )
    style = ParagraphStyle("Arabic", fontName=font_name, fontSize=10, leading=18, alignment=TA_RIGHT)
    heading = ParagraphStyle(
        "ArabicTitle", parent=style, fontSize=17, leading=26, textColor=colors.HexColor("#103e43")
    )

    def paragraph(value, used_style=style):
        return Paragraph(escape(_arabic(value)), used_style)

    now = datetime.now(ZoneInfo("Asia/Riyadh")).strftime("%Y-%m-%d %H:%M")
    story = [
        paragraph(organization_name, heading),
        paragraph("تقرير النتائج المعتمدة", heading),
        paragraph(f"أُنشئ بتوقيت الرياض: {now}"),
        Spacer(1, 14),
    ]
    if summary.empty:
        story.append(paragraph("لا توجد نتائج معتمدة ضمن هذا التقرير."))
    else:
        # RTL table: percentage left, student code right. Each data cell is escaped before markup rendering.
        headers = ["النسبة", "الدرجة الكاملة", "الدرجة", "الاختبار", "رمز الطالب"]
        data = [[paragraph(h) for h in headers]]
        for _, row in summary.iterrows():
            data.append(
                [
                    paragraph(f"{row['النسبة %']:g}%"),
                    paragraph(f"{row['الدرجة الكاملة']:g}"),
                    paragraph(f"{row['الدرجة']:g}"),
                    paragraph(row["الاختبار"]),
                    paragraph(row["رمز الطالب"]),
                ]
            )
        table = Table(data, colWidths=[52, 85, 52, 196, 146], repeatRows=1, hAlign="RIGHT")
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e0f0ec")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f7f8")]),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.HexColor("#167d72")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 7),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story.extend([table, Spacer(1, 14), paragraph("يشمل التقرير الدرجات التي اعتمدها المعلم فقط.")])

    def footer(canvas, doc):
        canvas.setFont(font_name, 8)
        canvas.setFillColor(colors.HexColor("#657781"))
        canvas.drawRightString(A4[0] - 32, 20, _arabic(f"SmartGrade AI | صفحة {doc.page}"))

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return out.getvalue()
