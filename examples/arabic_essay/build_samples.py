"""Render synthetic Arabic answer sheets and a self-contained download notebook."""
import base64
import hashlib
import io
import json
from pathlib import Path
import sys
import zipfile

import arabic_reshaper
from bidi.algorithm import get_display
from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent
OUT = HERE / "generated"
W, H, MARGIN = 1654, 2339, 100
FONT_PATH = ROOT / "assets/fonts/DejaVuSans.ttf"
FONT_NAME = "EssaySampleArabic"
BLACK = (20, 20, 20)
TEAL = (17, 74, 75)


def visual(text):
    return get_display(arabic_reshaper.reshape(str(text)))


class Paper:
    def __init__(self, name, *, single_page=False):
        self.name = name
        self.single_page = single_page
        self.pages = []
        self.fonts = {}
        self.new_page()

    def new_page(self):
        self.image = Image.new("RGB", (W, H), "white")
        self.draw = ImageDraw.Draw(self.image)
        self.commands = []
        self.cursor = MARGIN
        self.pages.append((self.image, self.commands))

    def font(self, size):
        if size not in self.fonts:
            self.fonts[size] = ImageFont.truetype(str(FONT_PATH), size, layout_engine=ImageFont.Layout.BASIC)
        return self.fonts[size]

    def width(self, text, size):
        return self.draw.textlength(visual(text), font=self.font(size))

    def ensure(self, height):
        if self.cursor + height > H - MARGIN:
            if self.single_page:
                raise ValueError(f"{self.name}: answer sheet must fit one uploadable page")
            self.new_page()

    def text(self, text, *, size=29, color=BLACK, gap=10):
        for paragraph in str(text).split("\n"):
            lines, line = [], ""
            for word in paragraph.split():
                proposed = (line + " " + word).strip()
                if line and self.width(proposed, size) > W - 2 * MARGIN:
                    lines.append(line)
                    line = word
                else:
                    line = proposed
            lines.append(line)
            for line in lines:
                self.ensure(size + 17)
                shaped = visual(line)
                width = self.draw.textlength(shaped, font=self.font(size))
                self.draw.text((W - MARGIN - width, self.cursor), shaped, font=self.font(size), fill=color)
                self.commands.append((line, self.cursor, size, color))
                self.cursor += size + 15
        self.cursor += gap

    def space(self, pixels=18):
        self.ensure(pixels)
        self.cursor += pixels

    def save(self):
        pdfmetrics.registerFont(TTFont(FONT_NAME, str(FONT_PATH)))
        pdf = canvas.Canvas(str(OUT / f"{self.name}.pdf"), pagesize=(W * 72 / 200, H * 72 / 200))
        pdf.setTitle("SmartGrade synthetic Arabic essay test")
        pdf.setAuthor("SmartGrade AI")
        scale = 72 / 200
        for index, (image, commands) in enumerate(self.pages):
            for text, y, size, color in commands:
                pdf.setFont(FONT_NAME, size * scale)
                pdf.setFillColorRGB(*(c / 255 for c in color))
                pdf.drawRightString((W - MARGIN) * scale, (H - y - size * 1.22) * scale, visual(text))
            pdf.showPage()
            suffix = "" if len(self.pages) == 1 else f"-page-{index + 1}"
            image.save(OUT / f"{self.name}{suffix}.png", optimize=True, dpi=(200, 200))
        pdf.save()


def header(paper, student=None):
    paper.text("اختبار العلوم المقالي", size=43, color=TEAL)
    paper.text("المرحلة المتوسطة | خمسة أسئلة | الدرجة الكاملة: 30", size=29)
    if student:
        paper.text("رمز الورقة الاصطناعية: " + student, size=26)
    paper.text("أجب إجابة علمية واضحة عن كل سؤال.", size=28)
    paper.space(20)


def reference_proposal(spec, case):
    return {"needs_review": True, "questions": [
        {"question_id": question["id"], "needs_review": True, "criteria": [
            {"criterion_id": criterion["id"], "score": score,
             "evidence": case["answers"][question["id"]] if score else "",
             "rationale": "تحقق عنصران صحيحان في هذا المعيار." if score == 2 else
                          "تحقق عنصر واحد فقط في هذا المعيار." if score == 1 else
                          "لا يوجد عنصر صحيح مستوفى لهذا المعيار.",
             "missing": "" if score == 2 else
                        "عنصر واحد من وصف المعيار." if score == 1 else criterion["description"]}
            for criterion, score in zip(question["criteria"], case["scores"][index], strict=True)]}
        for index, question in enumerate(spec["questions"])]}


def code_cell(source, ident):
    return {"cell_type": "code", "id": ident, "metadata": {"cellView": "form"},
            "source": source.strip() + "\n", "execution_count": None, "outputs": []}


def notebook(bundle):
    encoded = base64.b64encode(bundle).decode()
    digest = hashlib.sha256(bundle).hexdigest()
    return {
        "nbformat": 4, "nbformat_minor": 5,
        "metadata": {"colab": {"name": "SmartGrade_Arabic_Essay_Test.ipynb", "provenance": []},
                     "kernelspec": {"display_name": "Python 3", "name": "python3"},
                     "language_info": {"name": "python"}, "synthetic": True,
                     "bundle_sha256": digest},
        "cells": [
            {"cell_type": "markdown", "id": "essay-intro", "metadata": {}, "source": (
                "# اختبار عربي محلول لتجربة SmartGrade\n"
                "خمسة أسئلة علوم من30 درجة، ومفتاح تصحيح، وست أوراق إجابة مطبوعة بصيغتي PNG وPDF.\n"
                "شغّل الخلية الأولى لاستخراج الملفات، والثانية لتنزيل الحزمة. لا تحتاج مفاتيح API أو تثبيت مكتبات.\n"
                "ابدأ بصورة student-c.png: درجاتها المرجعية للأسئلة 2،5،4،3،3، والإجمالي17/30.\n"
                "أنشئ اختبارًا جديدًا في مشروع SmartGrade من exam-spec.json أو teacher-key.md، ثم ارفع الصورة.\n"
                "راجع OCR وحدد إجابة كل سؤال واحفظها قبل طلب اقتراح الدرجات. النموذج يحتاج إعداد مزوده مسبقًا.\n"
                "هذه الخلايا تنزّل ملفات فقط؛ لا تنشئ بيانات داخل مؤسستك ولا تستدعي مزود تصحيح.\n"
                "راجع README.md؛ الدرجات مرجعية لهذه العينات وليست قياسًا معتمدًا لدقة النموذج.\n")},
            code_cell('''# @title 1. استخراج ملفات الاختبار المضمنة
import base64, hashlib, io, stat, zipfile
from pathlib import Path, PurePosixPath
BUNDLE_SHA256 = "__DIGEST__"
BUNDLE_B64 = "__PAYLOAD__"
blob = base64.b64decode(BUNDLE_B64, validate=True)
if hashlib.sha256(blob).hexdigest() != BUNDLE_SHA256:
    raise ValueError("بصمة ملفات الاختبار غير صحيحة")
TARGET = Path("/content/SmartGrade-Arabic-Essay-Test")
TARGET.mkdir(parents=True, exist_ok=True)
if TARGET.is_symlink():
    raise ValueError("اختر مجلدًا غير رمزي")
with zipfile.ZipFile(io.BytesIO(blob)) as archive:
    for member in archive.infolist():
        part = PurePosixPath(member.filename)
        if part.is_absolute() or ".." in part.parts or stat.S_ISLNK(member.external_attr >> 16):
            raise ValueError("مسار غير آمن")
        destination = TARGET.joinpath(*part.parts)
        if TARGET.resolve() not in destination.resolve().parents:
            raise ValueError("مسار خارج مجلد الاختبار")
        if member.file_size > 10 * 1024 * 1024:
            raise ValueError("ملف أكبر من الحد المحدد")
        content = archive.read(member)
        if destination.exists() and destination.read_bytes() != content:
            raise FileExistsError("ملف معدل موجود؛ اختر مجلدًا جديدًا دون استبداله")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
ZIP_FILE = TARGET / "SmartGrade-Arabic-Essay-Test.zip"
ZIP_FILE.write_bytes(blob)
print("ملفات الاختبار جاهزة:", TARGET)
'''.replace("__DIGEST__", digest).replace("__PAYLOAD__", encoded), "essay-extract"),
            code_cell('''# @title 2. تنزيل الاختبار والمفتاح وجميع الصور
from google.colab import files
files.download(str(ZIP_FILE))
''', "essay-download"),
            code_cell('''# @title 3. تنزيل صورة مفردة للرفع — اختياري
CASE = "student-c" # @param ["student-a", "student-b", "student-c", "student-d", "student-e", "student-f"]
DOWNLOAD_ONE_IMAGE = False # @param {type:"boolean"}
if DOWNLOAD_ONE_IMAGE:
    from google.colab import files
    files.download(str(TARGET / (CASE + ".png")))
''', "essay-image"),
        ],
    }


def main():
    from modules.schemas import ExamSpec, parse_essay_grade
    data = json.loads((HERE / "fixture.json").read_text())
    spec = ExamSpec.model_validate(data["exam"])
    assert len(spec.questions) == 5 and spec.maximum == 30
    OUT.mkdir(parents=True, exist_ok=True)
    expected = {}
    md = ["# اختبار العلوم المقالي — الأسئلة والحلول وسلم التصحيح\n",
          "**خمسة أسئلة × 6 درجات = 30 درجة.**\n", spec.instructions, ""]
    for index, question in enumerate(spec.questions, 1):
        md.extend([f"## السؤال {index} — 6 درجات\n", question.question,
                   "\n**الإجابة النموذجية:** " + question.model_answer, "\n**المعايير:**"])
        md.extend(f"- {criterion.description} **الحد: 2.**" for criterion in question.criteria)
        md.append("")
    md.extend(["## الحالات المرجعية\n", "| الورقة | الوصف | الدرجات لكل سؤال | الإجمالي /30 |",
               "|---|---|---|---:|"])
    for case in data["cases"]:
        grade = reference_proposal(data["exam"], case)
        parsed, total = parse_essay_grade(grade, spec, "", {"essay_answers": case["answers"]})
        assert total == case["expected_total"]
        assert [sum(c.score for c in q.criteria) for q in parsed.questions] == case["expected_question_totals"]
        expected[case["id"]] = {"synthetic": True, "proposal": grade, "total": total,
                                "provider_policy": case.get("provider_policy", "opt_in_only")}
        md.append(f'| {case["id"]} | {case["label"]} | '
                  + "، ".join(map(str, case["expected_question_totals"])) + f" | {total:g} |")
        paper = Paper(case["id"], single_page=True)
        header(paper, "SYNTHETIC-" + case["id"][-1].upper())
        for index, question in enumerate(spec.questions, 1):
            paper.text(f"السؤال {index}: " + question.question, size=30, color=TEAL)
            answer = case["answers"][question.id]
            paper.text(answer if answer else "........................................................................",
                       size=29)
            paper.space(16)
        paper.save()
    md.extend(["\n## تفسير الحالة الجزئية — student-c\n",
               *[data["partial_explanation"][q.id] for q in spec.questions],
               "\nهذه درجات مرجعية معدة للتجربة، وليست مخرجات نموذج أو نتائج اتفاق مصححين مستقلين.",
               "حالة student-f فارغة: يرفض البرنامج طلب مزود التصحيح قبل الاتصال، ويمكن اعتماد الصفر يدويًا."])
    (HERE / "teacher-key.md").write_text("\n\n".join(md) + "\n")
    (HERE / "expected-grades.json").write_text(json.dumps(expected, ensure_ascii=False, indent=2) + "\n")
    key = Paper("teacher-key")
    key.text("مفتاح تصحيح المعلم — 30 درجة", size=42, color=TEAL)
    key.text("مفتاح منفصل؛ لا ترفعه بوصفه إجابة الطالب.", size=28)
    for index, question in enumerate(spec.questions, 1):
        key.text(f"السؤال {index}: " + question.question, size=31, color=TEAL)
        key.text("الإجابة النموذجية: " + question.model_answer, size=28)
        for criterion in question.criteria:
            key.text(criterion.description, size=26)
        key.space(16)
    key.save()
    blank = Paper("blank-exam", single_page=True)
    header(blank)
    for index, question in enumerate(spec.questions, 1):
        blank.text(f"السؤال {index}: " + question.question, size=30, color=TEAL)
        blank.text("........................................................................", size=29)
        blank.text("........................................................................", size=29)
        blank.space(12)
    blank.save()
    image_info = []
    for path in sorted(OUT.glob("*.png")):
        with Image.open(path) as image:
            assert image.width * image.height <= 16_000_000
            assert path.stat().st_size < 10 * 1024 * 1024
            image_info.append({"file": path.name, "width": image.width, "height": image.height,
                               "bytes": path.stat().st_size})
    validation = {"synthetic": True, "questions": len(spec.questions), "maximum": spec.maximum,
                  "reference_totals": {c["id"]: c["expected_total"] for c in data["cases"]},
                  "reference_contract_validation": "passed", "images": image_info,
                  "paid_api_calls": 0, "llm_executed": False, "handwriting_evaluated": False}
    (OUT / "validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2) + "\n")
    artifacts = [HERE / n for n in ["README.md", "exam-spec.json", "cases.json",
                                  "expected-grades.json", "teacher-key.md"]]
    artifacts += sorted(p for p in OUT.iterdir() if p.suffix in {".pdf", ".png", ".json"})
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in artifacts:
            archive.writestr(path.name, path.read_bytes())
    bundle = stream.getvalue()
    (OUT / "SmartGrade-Arabic-Essay-Test.zip").write_bytes(bundle)
    nb = notebook(bundle)
    for cell in nb["cells"]:
        if cell["cell_type"] == "code":
            compile(cell["source"], cell["id"], "exec")
    (HERE / "notebook.ipynb").write_text(json.dumps(nb, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps(validation, ensure_ascii=False))


if __name__ == "__main__":
    main()
