"""Build a synthetic Arabic acceptance kit. No accounts, database writes or provider calls."""
import argparse
import csv
import hashlib
from html import escape
import json
from pathlib import Path
import zipfile

import arabic_reshaper
from bidi.algorithm import get_display
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from modules.schemas import ExamSpec, parse_essay_grade
from modules.grading_evaluation import reference_proposal
from modules.mcq_grader import template, read_answers, score_answers, bubble_center
from modules.preprocessing import decode_image

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "assets/acceptance_pack/exams.json"
SCENARIOS = ROOT / "assets/acceptance_pack/scenarios.json"
FONT = ROOT / "assets/fonts/DejaVuSans.ttf"


def arabic(value):
    return get_display(arabic_reshaper.reshape(str(value)))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path, headers, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)


def wrapped(text, font, width=1080):
    if not text.strip():
        return [""]
    lines, current = [], ""
    for word in text.split():
        candidate = (current + " " + word).strip()
        if current and font.getlength(arabic(candidate)) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    return lines + [current]


def answer_sheet(dataset, case, path):
    font = ImageFont.truetype(str(FONT), 31)
    lines = [("SmartGrade | SYNTHETIC TEST PAPER", False),
             (dataset["subject"] + " — عينة إجابة مطبوعة اصطناعية", True),
             ("Student code: " + case["id"], False)]
    for number, question in enumerate(dataset["exam"]["questions"], 1):
        lines.append((f"السؤال {number}", True))
        lines.extend((line, True) for line in wrapped(case["answers"][question["id"]], font))
        lines.append(("", True))
    image = Image.new("RGB", (1240, max(1754, 100 + len(lines) * 48)), "white")
    draw = ImageDraw.Draw(image)
    y = 50
    for line, rtl in lines:
        if rtl:
            draw.text((1180, y), arabic(line), font=font, fill="#172e38", anchor="ra")
        else:
            draw.text((60, y), line, font=font, fill="#172e38")
        y += 48
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    # Only text actually printed; no question prompts, model answers or scores in the student's image.
    return "\n".join(line for line, _ in lines)


def image_pdf(paths, path):
    pdf = canvas.Canvas(str(path), pagesize=A4)
    for image_path in paths:
        with Image.open(image_path) as im:
            width, height = im.size
            scale = min((A4[0] - 40) / width, (A4[1] - 40) / height)
            pdf.drawImage(ImageReader(im.convert("RGB")), (A4[0] - width * scale) / 2,
                          (A4[1] - height * scale) / 2, width * scale, height * scale)
        pdf.showPage()
    pdf.save()


def handwriting_pdf(datasets, path):
    font = ImageFont.truetype(str(FONT), 30)
    images = []
    for dataset in datasets:
        image = Image.new("RGB", (1240, 1754), "white")
        draw = ImageDraw.Draw(image)
        draw.text((1180, 45), arabic(dataset["subject"] + " — ورقة كتابة يدوية"), font=font,
                  fill="black", anchor="ra")
        draw.text((60, 100), "Student code: ____________________", font=font, fill="black")
        y = 180
        for number, question in enumerate(dataset["exam"]["questions"], 1):
            for line in wrapped(f"{number}. " + question["question"], font):
                draw.text((1180, y), arabic(line), font=font, fill="black", anchor="ra")
                y += 42
            for _ in range(3):
                draw.line((70, y + 35, 1170, y + 35), fill="#b9c4c9", width=1)
                y += 49
            y += 12
        sheet_path = path.parent / (dataset["id"] + "-handwriting.png")
        image.save(sheet_path)
        images.append(sheet_path)
    image_pdf(images, path)


def omr_samples(out):
    key = list("ABCD" * 5)
    good = {i: [key[i]] for i in range(20)}
    perfect = template(good, synthetic=True)
    wrong = template({i: ["ABCD"[("ABCD".index(key[i]) + 1) % 4]] for i in range(20)}, synthetic=True)
    half = template({i: [key[i] if i % 2 == 0 else "ABCD"[("ABCD".index(key[i]) + 1) % 4]]
                     for i in range(20)}, synthetic=True)
    multiple = template({**good, 0: ["A", "B"]}, synthetic=True)
    weak = perfect.copy()
    cv2.circle(weak, bubble_center(4, 0), 19, (175, 175, 175), -1)
    padded = cv2.copyMakeBorder(perfect, 130, 130, 130, 130, cv2.BORDER_CONSTANT, value=(255,) * 3)
    h, w = padded.shape[:2]
    rotated = cv2.warpAffine(padded, cv2.getRotationMatrix2D((w / 2, h / 2), 5, 1),
                             (w, h), borderValue=(255,) * 3)
    h, w = perfect.shape[:2]
    src = np.float32([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]])
    dst = np.float32([[100, 50], [w - 90, 10], [w - 5, h - 70], [20, h - 5]])
    perspective = cv2.warpPerspective(perfect, cv2.getPerspectiveTransform(src, dst), (w, h),
                                     borderValue=(255,) * 3)
    gradient = (perfect * np.linspace(0.7, 1, w)[None, :, None]).astype(np.uint8)
    missing = perfect.copy()
    missing[35:108, 35:108] = 255
    samples = [
        ("01-perfect.png", perfect, 20, None, "20 إجابة صحيحة"),
        ("02-wrong.png", wrong, 0, None, "20 اختيارًا خاطئًا واضحًا"),
        ("03-half.png", half, 10, None, "الأسئلة الفردية صحيحة والزوجية خاطئة"),
        ("04-blank.png", template(synthetic=True), 0, ("blank", 0), "ورقة اختيارات فارغة"),
        ("05-multiple.png", multiple, 19, ("multiple", 0), "السؤال الأول متعدد التظليل"),
        ("06-weak.png", weak, 19, ("unclear", 4), "السؤال الخامس تظليله غير واضح"),
        ("07-rotation.png", rotated, 20, None, "دوران 5 درجات مع بقاء العلامات"),
        ("08-perspective.png", perspective, 20, None, "منظور مائل مع علامات كاملة"),
        ("09-lighting.png", gradient, 20, None, "تدرج إضاءة اصطناعي"),
        ("10-upside-down.png", cv2.rotate(perfect, cv2.ROTATE_180), None, None, "اتجاه مقلوب: يرفض قبل تصحيحه"),
        ("11-missing-marker.png", missing, None, None, "علامة تسجيل مفقودة: يرفض"),
        ("12-jpeg.jpg", perfect, 20, None, "JPEG بضغط 35"),
    ]
    checks, paths, records = [], [], []
    for filename, array, expected, special, label in samples:
        path = out / filename
        image = Image.fromarray(array)
        image.save(path, quality=35) if path.suffix == ".jpg" else image.save(path)
        decoded = decode_image(path.read_bytes())
        try:
            _, readings = read_answers(decoded)
        except ValueError:
            if expected is not None:
                raise
            checks.append({"file": filename, "expected": "reject", "actual": "reject", "passed": True})
        else:
            if expected is None:
                raise AssertionError("Expected rejection: " + filename)
            actual = score_answers([r["answer"] for r in readings], key)
            if actual != expected or (special and readings[special[1]]["state"] != special[0]):
                raise AssertionError("OMR mismatch: " + filename)
            checks.append({"file": filename, "expected": expected, "actual": actual, "passed": True})
        records.append([filename, label, "رفض الورقة" if expected is None else expected, 20])
        paths.append(path)
    return key, records, checks, paths


def invalid_files(out):
    (out / "fake.png").write_text("This is deliberately not an image.", encoding="utf-8")
    Image.new("RGB", (64, 64), "white").save(out / "too-small.png")
    Image.new("RGB", (4100, 4000), "white").save(out / "too-many-pixels.png")
    (out / "unsupported.txt").write_text("ملف نصي غير مقبول كصورة إجابة.", encoding="utf-8")
    checks = []
    for path in sorted(out.iterdir()):
        try:
            decode_image(path.read_bytes())
        except ValueError:
            checks.append({"file": path.name, "expected": "reject", "actual": "reject", "passed": True})
        else:
            raise AssertionError("Invalid input was accepted: " + path.name)
    return checks


def teacher_html(data):
    parts = ['<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><title>دليل اختبارات SmartGrade</title>',
             '<style>body{font-family:Tahoma,sans-serif;max-width:1000px;margin:32px auto;padding:20px;line-height:1.9;color:#173c40}h1,h2{color:#12685d}section{border:1px solid #ccdeda;padding:20px;margin:24px 0;border-radius:12px}pre{white-space:pre-wrap;font:inherit;background:#f3f6f7;padding:12px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccdeda;padding:8px}a{color:#12685d}@media print{details{display:block}section{break-before:page}summary{font-weight:bold}}</style>',
             '<h1>حزمة اختبارات SmartGrade العربية</h1>',
             '<p>عينات اصطناعية ودرجات مرجعية مؤلفة. لم يُقيّم مزود LLM ولم تُقَس دقة عامة. لا تظهر مفاتيح الدرجات في صور إجابات الطلاب.</p>',
             '<p>أنشئ كل اختبار في الواجهة وفق الأسئلة والمعايير أدناه، ثم ارفع صور essays. احفظ النص وتوزيع الإجابات. سجّل اقتراح النموذج قبل تعديلات المعلم. ابدأ بورقتي A وD من كل مادة.</p>']
    for dataset in data["exams"]:
        maximum = sum(q["maximum"] for q in dataset["exam"]["questions"])
        parts.append('<section><h2>' + escape(dataset["exam"]["title"]) + f' — {maximum:g} درجة</h2>')
        parts.append('<p>' + escape(dataset["exam"]["instructions"]) + '</p>')
        for number, question in enumerate(dataset["exam"]["questions"], 1):
            parts.append(f'<h3>السؤال {number} — {question["maximum"]:g} درجات</h3><p>' + escape(question["question"]) + '</p>')
            parts.append('<p><strong>الإجابة النموذجية:</strong> ' + escape(question["model_answer"]) + '</p><ul>')
            parts.extend('<li>' + escape(c["description"]) + f' — {c["maximum"]:g} درجات</li>' for c in question["criteria"])
            parts.append('</ul>')
        parts.append('<h3>الأوراق والدرجات المرجعية</h3><table><tr><th>الورقة</th><th>الحالة</th><th>المجموع</th></tr>')
        parts.extend('<tr><td>' + escape(c["id"]) + '</td><td>' + escape(c["label"]) + f'</td><td>{c["expected_total"]:g}</td></tr>' for c in dataset["cases"])
        parts.append('</table>')
        for case in dataset["cases"]:
            parts.append('<details><summary>' + escape(case["id"] + ' — ' + case["label"]) + '</summary>')
            for number, question in enumerate(dataset["exam"]["questions"], 1):
                parts.append(f'<p><strong>إجابة السؤال {number}:</strong></p><pre>' + escape(case["answers"][question["id"]] or "إجابة فارغة") + '</pre>')
                parts.append('<p>درجات المعايير: ' + escape(str(case["scores"][number - 1])) + '</p>')
            parts.append('</details>')
        parts.append('</section>')
    return "\n".join(parts) + '</html>'


def build(out):
    if out.exists() and any(out.iterdir()):
        raise ValueError("اختر مجلد إخراج جديدًا؛ لا نستبدل ملفات موجودة.")
    out.mkdir(parents=True, exist_ok=True)
    data = json.loads(DATA.read_text(encoding="utf-8"))
    scenarios = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    for name in ["exams", "essays", "omr", "invalid", "references", "print", "ocr_references"]:
        (out / name).mkdir()
    totals, criteria, results, reference_checks, images = [], [], [], [], []
    for dataset in data["exams"]:
        spec = ExamSpec.model_validate(dataset["exam"])
        write_json(out / "exams" / (dataset["id"] + ".json"), spec.model_dump())
        folder = out / "essays" / dataset["id"]
        folder.mkdir()
        for case in dataset["cases"]:
            proposal = reference_proposal(spec, case)
            _, total = parse_essay_grade(proposal, spec, "", {"essay_answers": case["answers"]})
            if total != case["expected_total"]:
                raise AssertionError("Reference total mismatch: " + case["id"])
            path = folder / (case["id"] + ".png")
            printed = answer_sheet(dataset, case, path)
            decode_image(path.read_bytes())
            (folder / (case["id"] + ".txt")).write_text("\n\n".join(
                f"السؤال {n}:\n{case['answers'][q.id]}" for n, q in enumerate(spec.questions, 1)), encoding="utf-8")
            (out / "ocr_references" / (case["id"] + ".txt")).write_text(printed, encoding="utf-8")
            write_json(folder / (case["id"] + ".answers.json"), {"answers": case["answers"]})
            totals.append([dataset["id"], case["id"], case["label"], case["expected_total"], spec.maximum])
            reference_checks.append({"case": case["id"], "expected_total": total, "schema_and_bounds_valid": True})
            for i, question in enumerate(spec.questions):
                for j, criterion in enumerate(question.criteria):
                    criteria.append([dataset["id"], case["id"], i + 1, criterion.id,
                                     case["scores"][i][j], criterion.maximum])
            results.append([case["id"], dataset["subject"], "essay", case["expected_total"], spec.maximum,
                            "", "", "", "", "", "", "", "", "لم يُنفذ"])
            images.append(path)
    key, omr_rows, omr_checks, omr_paths = omr_samples(out / "omr")
    invalid_checks = invalid_files(out / "invalid")
    for filename, label, expected, maximum in omr_rows:
        results.append([filename, "اختيارات", "omr", expected, maximum, "", "", "", "", "", "", "", "", "لم يُنفذ"])
    for row in invalid_checks:
        results.append([row["file"], "رفع", "invalid", "رفض", "", "", "", "", "", "", "", "", "", "لم يُنفذ"])
    for scenario in scenarios:
        results.append([scenario["id"], scenario["title"], "functional", scenario["expected"], "", "", "", "", "", "", "", "", "", "لم يُنفذ"])
    write_csv(out / "references/expected_totals.csv", ["الاختبار", "الورقة", "الحالة", "الدرجة المرجعية", "الدرجة الكاملة"], totals)
    write_csv(out / "references/expected_criteria.csv", ["الاختبار", "الورقة", "السؤال", "المعيار", "المرجع", "الحد الأعلى"], criteria)
    write_csv(out / "references/omr_expected.csv", ["الملف", "الحالة", "المتوقع", "الحد الأعلى"], omr_rows)
    write_json(out / "references/omr_exam.json", {"title": "TEST-SG | OMR 20 سؤالًا", "kind": "mcq",
               "answer_key": key, "points_per_question": 1})
    write_csv(out / "results_template.csv", ["معرف الحالة", "المادة", "النوع", "المرجع", "الدرجة الكاملة",
              "المرحلة: نص يدوي أو OCR مدقق أو صورة كاملة", "المزود والنموذج", "إصدار التطبيق",
              "اقتراح النموذج قبل التعديل", "اعتماد المعلم", "الزمن بالثواني", "رمز الخطأ", "ملاحظة", "الحالة"], results)
    write_json(out / "functional_scenarios.json", scenarios)
    (out / "teacher_guide.html").write_text(teacher_html(data), encoding="utf-8")
    (out / "README_AR.md").write_text((ROOT / "docs/acceptance_pack/README_AR.md").read_text(encoding="utf-8"), encoding="utf-8")
    (out / "FONT_LICENSE.txt").write_text((ROOT / "assets/fonts/LICENSE.txt").read_text(), encoding="utf-8")
    image_pdf(images, out / "print/all_student_papers.pdf")
    image_pdf(omr_paths, out / "print/omr_papers.pdf")
    handwriting_pdf(data["exams"], out / "print/handwriting_sheets.pdf")
    report = {"synthetic": True, "reference_labels_independently_validated": False,
              "model_evaluated": False, "external_calls": 0, "handwriting_evaluated": False,
              "exams": len(data["exams"]), "essay_papers": len(images), "questions": sum(len(d["exam"]["questions"]) for d in data["exams"]),
              "omr_images": len(omr_checks), "invalid_files": len(invalid_checks),
              "functional_scenarios": len(scenarios), "reference_contract_checks": reference_checks,
              "omr_fixture_checks": omr_checks, "invalid_fixture_checks": invalid_checks, "passed": True}
    write_json(out / "validation.json", report)
    files = sorted(p for p in out.rglob("*") if p.is_file())
    write_json(out / "manifest.json", {"pack_id": data["id"], "files": [
        {"path": p.relative_to(out).as_posix(), "bytes": p.stat().st_size,
         "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in files]})
    archive = out / "SmartGrade_TestPack_AR_v1.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(out.rglob("*")):
            if path.is_file() and path != archive:
                z.write(path, "SmartGrade_TestPack_AR_v1/" + path.relative_to(out).as_posix())
    print(json.dumps({"passed": True, "exams": report["exams"], "essay_papers": len(images),
                      "omr_images": len(omr_checks), "external_calls": 0,
                      "zip_bytes": archive.stat().st_size}, ensure_ascii=False))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    build(options.output.resolve())


if __name__ == "__main__":
    main()
