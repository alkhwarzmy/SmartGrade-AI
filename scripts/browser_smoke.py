from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright, expect
from openpyxl import load_workbook
from modules.mcq_grader import template
from modules.preprocessing import png_bytes
import json
import re
from contextlib import contextmanager
import os
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

root = Path(__file__).resolve().parents[1]
artifacts = root / "outputs" / "browser"
artifacts.mkdir(parents=True, exist_ok=True)
(artifacts / "synthetic-complete.png").write_bytes(
    png_bytes(template({i: ["ABCD"[i % 4]] for i in range(20)}, True))
)


@contextmanager
def running_server():
    with tempfile.TemporaryDirectory(prefix="smartgrade-browser-") as temporary:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = {
            **os.environ,
            "SMARTGRADE_DATA_DIR": temporary,
            "SMARTGRADE_MODE": "demo",
            "OCR_PROVIDER": "tesseract",
            "ALLOW_EXTERNAL_AI": "false",
            "ALLOW_PAID_API": "false",
        }
        command = [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            "app.py",
            "--server.address",
            "127.0.0.1",
            "--server.port",
            str(port),
            "--server.headless",
            "true",
        ]
        with (artifacts / "server.log").open("w") as log:
            process = subprocess.Popen(command, cwd=root, env=env, stdout=log, stderr=log)
            try:
                url = f"http://127.0.0.1:{port}"
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    try:
                        with urlopen(url + "/_stcore/health", timeout=1) as response:
                            if response.status == 200:
                                break
                    except OSError:
                        if process.poll() is not None:
                            raise RuntimeError("Streamlit test server exited")
                        time.sleep(0.2)
                else:
                    raise RuntimeError("Streamlit test server startup timeout")
                yield url
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


image = Image.new("RGB", (1500, 350), "white")
ImageDraw.Draw(image).text(
    (1400, 80),
    "الماء ضروري للحياة",
    font=ImageFont.truetype("/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf", 80),
    fill="black",
    anchor="rt",
    direction="rtl",
)
image.save(artifacts / "synthetic-printed-arabic.png")
report = {"dataset": "SYNTHETIC browser workflow; no real student data", "checks": []}
with running_server() as url, sync_playwright() as p:
    browser = p.chromium.launch(headless=True, executable_path="/usr/bin/chromium", args=["--no-sandbox"])
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
    page.set_default_timeout(30000)
    expect.set_options(timeout=30000)
    try:
        page.goto(url)
        page.get_by_label("عنوان الاختبار", exact=True).fill("اختبار اصطناعي — MCQ")
        page.get_by_role("button", name="إنشاء الاختبار", exact=True).click()
        expect(page.get_by_text("تم إنشاء الاختبار. انتقل إلى رفع الأوراق لبدء التصحيح.")).to_be_visible()
        page.get_by_text("رفع الأوراق", exact=True).click()
        page.get_by_label("رمز الطالب", exact=True).fill("SYNTHETIC-001")
        page.locator("input[type=file]").set_input_files(str(artifacts / "synthetic-complete.png"))
        expect(page.get_by_role("button", name="رفع وبدء المعالجة", exact=True)).to_be_enabled()
        page.get_by_role("button", name="رفع وبدء المعالجة", exact=True).click()
        expect(page.get_by_text("تم حفظ الورقة", exact=False)).to_be_visible()
        page.get_by_text("التصحيح والمراجعة", exact=True).click()
        page.get_by_text("راجعت الصورة والحالات غير الواضحة وأوافق على اعتماد الإجابات", exact=True).click()
        page.get_by_role("button", name="اعتماد النتيجة", exact=True).click()
        expect(page.get_by_text("نتيجة معتمدة: 20 / 20", exact=True)).to_be_visible()
        page.screenshot(path=str(artifacts / "review-mcq.png"), full_page=True)
        report["checks"].append("MCQ: create -> upload -> read -> review -> approve 20/20")
        page.get_by_text("النتائج والتصدير", exact=True).click()
        with page.expect_download() as download:
            page.get_by_role("button", name="تصدير Excel مع تفاصيل التصحيح").click()
        target = artifacts / "browser-results.xlsx"
        download.value.save_as(str(target))
        book = load_workbook(target)
        assert book["النتائج المعتمدة"]["E2"].value == 20
        assert book["تفاصيل التصحيح"].max_row == 21
        report["checks"].append(
            "Excel download: approved score 20; 20 question detail rows; Arabic preserved"
        )
        page.screenshot(path=str(artifacts / "results.png"), full_page=True)
        page.get_by_text("الاختبارات", exact=True).first.click()
        page.get_by_text("إجابة مقالية", exact=True).click()
        page.get_by_label("عنوان الاختبار", exact=True).fill("اختبار اصطناعي — مقالي")
        page.get_by_label("نص السؤال", exact=True).fill("لماذا الماء مهم للحياة؟")
        page.get_by_label("الإجابة النموذجية", exact=True).fill("الماء ضروري للحياة")
        page.get_by_role("button", name="إنشاء الاختبار", exact=True).click()
        expect(page.get_by_text("تم إنشاء الاختبار. انتقل إلى رفع الأوراق لبدء التصحيح.")).to_be_visible()
        page.get_by_text("رفع الأوراق", exact=True).click()
        # Most recent exam appears first, but existing widget may preserve old selection.
        box = page.get_by_role("combobox").first
        box.click()
        page.get_by_role("option", name="اختبار اصطناعي — مقالي", exact=False).click()
        page.get_by_label("رمز الطالب", exact=True).fill("SYNTHETIC-002")
        page.locator("input[type=file]").set_input_files(str(artifacts / "synthetic-printed-arabic.png"))
        expect(page.get_by_role("button", name="رفع وبدء المعالجة", exact=True)).to_be_enabled()
        page.get_by_role("button", name="رفع وبدء المعالجة", exact=True).click()
        expect(page.get_by_text("تم حفظ الورقة", exact=False)).to_be_visible()
        page.get_by_text("التصحيح والمراجعة", exact=True).click()
        page.get_by_role("combobox").first.click()
        page.get_by_role("option", name="اختبار اصطناعي — مقالي", exact=False).click()
        page.get_by_role("button", name="استخراج النص بمحرك OCR المحلي", exact=True).click()
        expect(
            page.get_by_text("تم استخراج النص محليًا. راجعه وصحح الأخطاء قبل التصحيح.", exact=True)
        ).to_be_visible()
        expect(page.get_by_label("النص المستخدم للتصحيح", exact=True)).to_have_count(1, timeout=30000)
        expect(page.get_by_label("النص المستخدم للتصحيح", exact=True).first).to_have_value(
            re.compile(".*الماء.*"), timeout=30000
        )
        report["checks"].append("Local Arabic OCR: real Tesseract execution on SYNTHETIC PRINTED Arabic")
        page.get_by_label("النص المستخدم للتصحيح", exact=True).fill("الماء ضروري للحياة")
        page.get_by_role("button", name="حفظ النص", exact=True).click()
        expect(page.get_by_text("تم حفظ النص. الورقة بانتظار مراجعة الدرجات.", exact=True)).to_be_visible()
        expect(page.get_by_label("النص المستخدم للتصحيح", exact=True)).to_have_value("الماء ضروري للحياة")
        for i, score in enumerate([3, 3, 1]):
            page.get_by_label("درجة المعيار", exact=True).nth(i).fill(str(score))
            page.get_by_label("درجة المعيار", exact=True).nth(i).press("Tab")
            page.get_by_label("دليل حرفي من الإجابة (اختياري)", exact=True).nth(i).fill("الماء")
            page.get_by_label("تبرير الدرجة", exact=True).nth(i).fill(
                "تقييم يدوي اصطناعي للتحقق من دورة العمل"
            )
        page.get_by_text("راجعت نص الإجابة وجميع المعايير وأوافق على الدرجات", exact=True).click()
        page.get_by_role("button", name="اعتماد الدرجة المقالية", exact=True).click()
        expect(page.get_by_text("نتيجة معتمدة: 7 / 10", exact=True)).to_be_visible()
        report["checks"].append(
            "Essay: OCR -> corrected text -> manual rubric grades -> teacher approval 7/10"
        )
        page.screenshot(path=str(artifacts / "review-essay.png"), full_page=True)
        page.get_by_label("النص المستخدم للتصحيح", exact=True).fill("تم تعديل النص بعد الاعتماد")
        page.get_by_role("button", name="حفظ النص", exact=True).click()
        expect(page.get_by_text("نتيجة معتمدة: 7 / 10", exact=True)).to_have_count(0)
        report["checks"].append("Editing approved essay text invalidates the previous approval")
        page.set_viewport_size({"width": 390, "height": 844})
        expect(page.get_by_label("انتقل إلى", exact=True)).to_be_visible()
        expect(page.get_by_test_id("stSidebar")).not_to_be_visible()
        page.screenshot(path=str(artifacts / "mobile-review.png"), full_page=True)
        # horizontal overflow should not exceed viewport (data grids scroll internally).
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        report["checks"].append(
            "Mobile 390px: top navigation visible, sidebar hidden, no horizontal overflow"
        )
        assert page.get_by_test_id("stException").count() == 0
        report["passed"] = True
    except Exception:
        page.screenshot(path=str(artifacts / "browser-failure.png"), full_page=True)
        print(page.locator("body").inner_text()[-7000:])
        raise
    finally:
        (artifacts / "browser-e2e-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        browser.close()
print(json.dumps(report, ensure_ascii=False, indent=2))
