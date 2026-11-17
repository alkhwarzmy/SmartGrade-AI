"""Real Chromium checks on synthetic institutions; starts/stops an isolated local server."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
from playwright.sync_api import sync_playwright, expect
from openpyxl import load_workbook
from modules.auth import AuthService
from modules.mcq_grader import template
from modules.preprocessing import png_bytes

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "outputs" / "commercial-browser"
PASSWORD = "Synthetic-browser-password!2026"
NEW_PASSWORD = "Changed-synthetic-password!2027"


@contextmanager
def server():
    with tempfile.TemporaryDirectory(prefix="smartgrade-commercial-browser-") as directory:
        auth = AuthService(directory)
        auth.provision_organization(
            "school-one", "مؤسسة اصطناعية أولى", "admin@one.test", "مدير تجريبي", PASSWORD
        )
        auth.provision_organization(
            "school-two", "مؤسسة اصطناعية ثانية", "admin@two.test", "مدير ثان", PASSWORD
        )
        admin = auth.login("school-one", "admin@one.test", PASSWORD)
        auth.create_user(admin, "viewer@one.test", "مشاهد تجريبي", PASSWORD, "viewer")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = {
            **os.environ,
            "SMARTGRADE_MODE": "commercial",
            "SMARTGRADE_DATA_DIR": directory,
            "ALLOW_EXTERNAL_AI": "false",
            "ALLOW_PAID_API": "false",
        }
        with (ARTIFACTS / "server.log").open("w") as log:
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "streamlit",
                    "run",
                    "app.py",
                    "--server.address",
                    "127.0.0.1",
                    "--server.port",
                    str(port),
                    "--server.fileWatcherType",
                    "none",
                ],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=log,
            )
            try:
                url = f"http://127.0.0.1:{port}"
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    try:
                        with urlopen(url + "/_stcore/health", timeout=1) as response:
                            if response.status == 200:
                                break
                    except OSError:
                        time.sleep(0.2)
                else:
                    raise RuntimeError("Commercial UI startup timeout")
                yield url
            finally:
                proc.terminate()
                proc.wait(timeout=15)


def main():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    sample = ARTIFACTS / "synthetic-sheet.png"
    sample.write_bytes(png_bytes(template({i: ["A"] for i in range(20)}, True)))
    report = {"source": "SYNTHETIC institutions and credentials; no real student data", "checks": []}
    with server() as url, sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path="/usr/bin/chromium", args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
        page.set_default_timeout(30000)
        expect.set_options(timeout=30000)

        def login(slug, email, password):
            page.get_by_label("رمز المؤسسة", exact=True).fill(slug)
            page.get_by_label("البريد الإلكتروني", exact=True).fill(email)
            page.get_by_label("كلمة المرور", exact=True).fill(password)
            page.get_by_role("button", name="دخول إلى مساحة العمل", exact=True).click()

        try:
            page.goto(url)
            expect(page.get_by_text("تسجيل الدخول", exact=True)).to_be_visible()
            assert page.get_by_label("عنوان الاختبار", exact=True).count() == 0
            page.screenshot(path=str(ARTIFACTS / "login.png"), full_page=True)
            login("school-one", "admin@one.test", "incorrect")
            expect(
                page.get_by_text("تعذر تسجيل الدخول؛ تحقق من بياناتك وحالة المؤسسة", exact=True)
            ).to_be_visible()
            login("school-one", "admin@one.test", PASSWORD)
            expect(page.get_by_label("عنوان الاختبار", exact=True)).to_be_visible()
            report["checks"].append(
                "Anonymous access blocked; invalid login rejected; administrator login succeeds"
            )
            page.get_by_label("عنوان الاختبار", exact=True).fill("اختبار تجاري اصطناعي")
            page.get_by_label("مفتاح الإجابة بالترتيب", exact=True).fill(" ".join(["A"] * 20))
            page.get_by_role("button", name="إنشاء الاختبار", exact=True).click()
            expect(page.get_by_text("تم إنشاء الاختبار. انتقل إلى رفع الأوراق لبدء التصحيح.")).to_be_visible()
            page.get_by_text("رفع الأوراق", exact=True).click()
            page.locator("input[type=file]").set_input_files(str(sample))
            expect(page.get_by_role("button", name="رفع وبدء المعالجة", exact=True)).to_be_enabled()
            page.get_by_label("رمز الطالب", exact=True).fill("SYNTHETIC-COMMERCIAL")
            page.get_by_role("button", name="رفع وبدء المعالجة", exact=True).click()
            expect(page.get_by_text("تم حفظ الورقة", exact=False)).to_be_visible()
            page.get_by_text("التصحيح والمراجعة", exact=True).click()
            expect(page.get_by_label("اسم المراجع", exact=True)).to_be_disabled()
            image = page.get_by_alt_text("صورة الإجابة للمراجعة", exact=True)
            expect(image).to_be_visible()
            assert image.get_attribute("src").startswith("data:image/png;base64,")
            page.get_by_text(
                "راجعت الصورة والحالات غير الواضحة وأوافق على اعتماد الإجابات", exact=True
            ).click()
            page.get_by_role("button", name="اعتماد النتيجة", exact=True).click()
            expect(page.get_by_text("نتيجة معتمدة: 20 / 20", exact=True)).to_be_visible()
            report["checks"].append(
                "Authenticated MCQ review 20/20; reviewer identity fixed; private image inline, not on global media route"
            )
            page.get_by_text("النتائج والتصدير", exact=True).click()
            excel = page.get_by_role("button", name="تصدير Excel مع تفاصيل التصحيح", exact=True)
            assert excel.get_attribute("href").startswith("data:application/")
            with page.expect_download() as download:
                excel.click()
            xlsx = ARTIFACTS / "synthetic-results.xlsx"
            download.value.save_as(str(xlsx))
            assert load_workbook(xlsx)["النتائج المعتمدة"]["E2"].value == 20
            with page.expect_download() as download:
                page.get_by_role("button", name="تصدير PDF بالعربية", exact=True).click()
            download.value.save_as(str(ARTIFACTS / "synthetic-results.pdf"))
            report["checks"].append(
                "Private inline Excel and Arabic PDF download; approved score verified from downloaded workbook"
            )
            page.get_by_text("إدارة المؤسسة", exact=True).click()
            expect(page.get_by_text("فريق العمل", exact=True)).to_be_visible()
            page.screenshot(path=str(ARTIFACTS / "organization.png"), full_page=True)
            page.get_by_role("button", name="تسجيل الخروج", exact=True).click()
            expect(page.get_by_label("رمز المؤسسة", exact=True)).to_be_visible()
            assert page.get_by_label("عنوان الاختبار", exact=True).count() == 0
            login("school-one", "viewer@one.test", PASSWORD)
            expect(page.get_by_text("تأمين حسابك", exact=True)).to_be_visible()
            page.get_by_label("كلمة المرور الحالية", exact=True).fill(PASSWORD)
            page.get_by_label("كلمة المرور الجديدة", exact=True).fill(NEW_PASSWORD)
            page.get_by_label("تأكيد كلمة المرور الجديدة", exact=True).fill(NEW_PASSWORD)
            page.get_by_role("button", name="تغيير كلمة المرور وتسجيل الخروج", exact=True).click()
            expect(page.get_by_label("رمز المؤسسة", exact=True)).to_be_visible()
            login("school-one", "viewer@one.test", NEW_PASSWORD)
            expect(page.get_by_text("النتائج والتصدير", exact=True).first).to_be_visible()
            assert page.get_by_text("رفع الأوراق", exact=True).count() == 0
            assert page.get_by_text("إدارة المؤسسة", exact=True).count() == 0
            report["checks"].append(
                "Logout clears workspace; first-login password change enforced; viewer only sees approved results and account"
            )
            page.set_viewport_size({"width": 390, "height": 844})
            expect(page.get_by_label("انتقل إلى", exact=True)).to_be_visible()
            expect(page.get_by_test_id("stSidebar")).not_to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth+1")
            page.screenshot(path=str(ARTIFACTS / "mobile.png"), full_page=True)
            page.set_viewport_size({"width": 1440, "height": 1000})
            page.get_by_role("button", name="تسجيل الخروج", exact=True).click()
            expect(page.get_by_label("رمز المؤسسة", exact=True)).to_be_visible()
            login("school-two", "admin@two.test", PASSWORD)
            expect(page.get_by_label("عنوان الاختبار", exact=True)).to_be_visible()
            page.get_by_text("اختباراتي", exact=True).click()
            expect(page.get_by_text("ستظهر اختباراتك هنا بعد إنشاء أول اختبار.", exact=True)).to_be_visible()
            assert page.get_by_text("اختبار تجاري اصطناعي", exact=True).count() == 0
            assert page.get_by_test_id("stException").count() == 0
            report["checks"].append(
                "Mobile layout valid; second organization cannot see first organization's exams"
            )
            report["passed"] = True
        except Exception:
            page.screenshot(path=str(ARTIFACTS / "failure.png"), full_page=True)
            print(page.locator("body").inner_text()[-6000:])
            raise
        finally:
            (ARTIFACTS / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
            browser.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
