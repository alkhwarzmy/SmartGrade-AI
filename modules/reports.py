"""Approved-only exports with Arabic text and spreadsheet-formula neutralization."""

import io
import pandas as pd
from modules.schemas import ExamSpec

SUMMARY_COLUMNS = [
    "معرف التسليم",
    "رمز الطالب",
    "الاختبار",
    "النوع",
    "الدرجة",
    "الدرجة الكاملة",
    "النسبة %",
    "زمن المعالجة ثانية",
    "آخر تعديل",
]
DETAIL_COLUMNS = [
    "معرف التسليم",
    "رمز الطالب",
    "الاختبار",
    "السؤال / المعيار",
    "الإجابة / الدليل",
    "الإجابة الصحيحة / التبرير",
    "الدرجة",
    "الدرجة الكاملة",
]


def safe_cell(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def frames(store, exam_id=None):
    summaries, details = [], []
    for row in store.submissions(exam_id):
        if row.status != "approved":
            continue
        spec = ExamSpec.model_validate(store.exam(row.exam_id).spec)
        summaries.append(
            [
                row.id,
                row.student_code,
                spec.title,
                spec.kind,
                row.total,
                spec.maximum,
                round(100 * row.total / spec.maximum, 2),
                round(row.processing_seconds, 3),
                row.updated_at,
            ]
        )
        if spec.kind == "mcq":
            for i, (answer, key) in enumerate(zip(row.approved["answers"], spec.answer_key)):
                details.append(
                    [
                        row.id,
                        row.student_code,
                        spec.title,
                        str(i + 1),
                        answer or "فارغ / متعدد",
                        key,
                        spec.points_per_question if answer == key else 0,
                        spec.points_per_question,
                    ]
                )
        else:
            rubric = {c.id: c for c in spec.criteria}
            for grade in row.approved["criteria"]:
                c = rubric[grade["criterion_id"]]
                details.append(
                    [
                        row.id,
                        row.student_code,
                        spec.title,
                        c.description,
                        grade["evidence"],
                        grade["rationale"],
                        grade["score"],
                        c.maximum,
                    ]
                )
    return (
        pd.DataFrame(summaries, columns=SUMMARY_COLUMNS).map(safe_cell),
        pd.DataFrame(details, columns=DETAIL_COLUMNS).map(safe_cell),
    )


def excel_bytes(summary, details):
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        summary.map(safe_cell).to_excel(writer, sheet_name="النتائج المعتمدة", index=False)
        details.map(safe_cell).to_excel(writer, sheet_name="تفاصيل التصحيح", index=False)
        for sheet in writer.book:
            sheet.sheet_view.rightToLeft = True
            sheet.freeze_panes = "A2"
            sheet.auto_filter.ref = sheet.dimensions
            for cells in sheet.columns:
                sheet.column_dimensions[cells[0].column_letter].width = min(
                    45, max(16, len(str(cells[0].value)) + 4)
                )
    return out.getvalue()


def csv_bytes(summary):
    return summary.map(safe_cell).to_csv(index=False).encode("utf-8-sig")
