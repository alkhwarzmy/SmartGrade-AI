"""Run the actual commercial UI and API through the complete fifty-question workflow."""
import io
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import httpx
from openpyxl import load_workbook
from playwright.sync_api import sync_playwright, expect
from modules.auth import AuthService
from modules.workspace import Workspace
from modules.institution_runtime import launch_institution
from modules.mcq_grader import template, read_answers
from modules.preprocessing import decode_image, png_bytes

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "experiments/mcq_dynamic/evidence"
OUT.mkdir(parents=True, exist_ok=True)
report = {"source_commit": os.getenv("TESTED_SOURCE", ""), "synthetic_only": True,
          "external_calls": 0, "checks": {}, "permanent_hosting_created": False}


def check(name, value):
    report["checks"][name] = bool(value)
    if not value:
        raise AssertionError(name)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(fn, timeout=40):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        result = fn()
        if result:
            return result
        time.sleep(0.2)
    raise TimeoutError("runtime wait")


def main():
    runtime = api_process = page = None
    password = secrets.token_urlsafe(30)
    token = ""
    status = 0
    (OUT / "failure.png").unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="smartgrade-mcq50-") as tmp, sync_playwright() as p:
        temp = Path(tmp)
        data = temp / "data"
        auth = AuthService(data)
        auth.provision_organization("mcq-school", "مؤسسة اختبار العدد المرن", "test@mcq.test", "المعلم", password)
        token = auth.login("mcq-school", "test@mcq.test", password)
        workspace = Workspace(auth, token)
        api_port, web_port = free_port(), free_port()
        api_url, web_url = f"http://127.0.0.1:{api_port}", f"http://127.0.0.1:{web_port}"
        env = {**os.environ, "SMARTGRADE_DATA_DIR": str(data), "SMARTGRADE_MODE": "commercial",
               "ALLOW_EXTERNAL_AI": "false", "ALLOW_PAID_API": "false"}
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1100}, locale="ar")
        page.set_default_timeout(30000)
        expect.set_options(timeout=30000)
        with (temp / "api.log").open("w") as log:
            try:
                api_process = subprocess.Popen([sys.executable, "-m", "uvicorn", "api:app",
                    "--host", "127.0.0.1", "--port", str(api_port)], cwd=ROOT, env=env, stdout=log, stderr=log)
                def ready():
                    try:
                        return httpx.get(api_url + "/ready", timeout=2).status_code == 200
                    except httpx.HTTPError:
                        return False
                wait_for(ready)
                runtime = launch_institution(ROOT, sys.executable, data, port=web_port,
                    extra_env={"ALLOW_EXTERNAL_AI": "false", "ALLOW_PAID_API": "false"}, timeout=60)
                with httpx.Client(base_url=api_url, headers={"Authorization": "Bearer " + token}, timeout=30) as api:
                    page.goto(web_url)
                    page.get_by_label("رمز المؤسسة", exact=True).fill("mcq-school")
                    page.get_by_label("البريد الإلكتروني", exact=True).fill("test@mcq.test")
                    page.get_by_label("كلمة المرور", exact=True).fill(password)
                    page.get_by_role("button", name="دخول إلى مساحة العمل", exact=True).click()
                    expect(page.get_by_test_id("stSidebar")).to_be_visible()
                    page.get_by_label("عنوان الاختبار", exact=True).fill("MCQ — خمسون سؤالًا")
                    page.get_by_label("عدد أسئلة الاختيارات", exact=True).and_(page.locator("input")).fill("50")
                    key = ["ABCD"[i % 4] for i in range(50)]
                    page.get_by_label("مفتاح الإجابة بالترتيب", exact=True).and_(page.locator("textarea")).fill(" ".join(key))
                    page.screenshot(path=str(OUT / "01-create-50.png"), full_page=True)
                    page.get_by_role("button", name="إنشاء الاختبار", exact=True).click()
                    exam = wait_for(lambda: workspace.exams()[0] if workspace.exams() else None)
                    eid = exam.id
                    check("teacher_created_50_via_browser", len(exam.spec["answer_key"]) == 50)
                    page.get_by_test_id("stSidebar").get_by_text("رفع الأوراق", exact=True).click()
                    expect(page.get_by_role("heading", name="رفع أوراق الاختبار", exact=True)).to_be_visible()
                    page.get_by_text("تحميل القالب وعينة للتجربة", exact=True).click()
                    for label, suffix in [("تنزيل القالب PDF", "pdf"), ("تنزيل القالب PNG", "png"),
                                          ("عينة اصطناعية للتجربة", "sample.png")]:
                        with page.expect_download() as event:
                            page.get_by_role("button", name=label, exact=True).click()
                        event.value.save_as(str(OUT / ("template-50." + suffix)))
                    check("actual_template_pdf_download", (OUT / "template-50.pdf").read_bytes().startswith(b"%PDF-"))
                    _, blank = read_answers(decode_image((OUT / "template-50.png").read_bytes()), question_count=50)
                    check("downloaded_template_has_50_blank_answers", len(blank) == 50 and all(r["state"] == "blank" for r in blank))
                    page.locator('input[type="file"]').set_input_files(str(OUT / "template-50.sample.png"))
                    page.get_by_label("رمز الطالب", exact=True).fill("SYNTHETIC-50")
                    page.get_by_role("button", name="رفع وبدء المعالجة", exact=True).click()
                    wait_for(lambda: workspace.counts(eid)["submissions"] == 1)
                    sid = workspace.submissions(eid)[0].id
                    check("real_browser_upload_decoded_50", workspace.submission(sid).current["answers"] == key)
                    page.get_by_role("button", name="مراجعة الورقة المرفوعة", exact=True).click()
                    expect(page.get_by_role("heading", name="التصحيح والمراجعة", exact=True)).to_be_visible()
                    page.get_by_label("راجعت الصورة والحالات غير الواضحة وأوافق على اعتماد الإجابات", exact=True).check()
                    page.get_by_role("button", name="اعتماد النتيجة", exact=True).click()
                    wait_for(lambda: workspace.submission(sid).status == "approved")
                    check("real_browser_approval_50_of_50", workspace.submission(sid).total == 50)
                    page.get_by_role("heading", name="التصحيح والمراجعة", exact=True).scroll_into_view_if_needed()
                    page.screenshot(path=str(OUT / "02-reviewed-50.png"), full_page=True)
                    page.get_by_test_id("stSidebar").get_by_text("النتائج والتصدير", exact=True).click()
                    with page.expect_download() as event:
                        page.get_by_role("button", name="تصدير Excel مع تفاصيل التصحيح", exact=True).click()
                    destination = OUT / "results-50.xlsx"
                    event.value.save_as(str(destination))
                    workbook = load_workbook(io.BytesIO(destination.read_bytes()), data_only=True)
                    detail_sheet = workbook["تفاصيل التصحيح"]
                    check("excel_contains_50_questions", detail_sheet.max_row == 51)
                    page.screenshot(path=str(OUT / "03-results-50.png"), full_page=True)
                    wrong = api.post(f"/exams/{eid}/submissions", data={"student_code": "WRONG-COUNT"},
                                     files={"file": ("legacy20.png", png_bytes(template()), "image/png")})
                    check("wrong_20_template_rejected_for_50", wrong.status_code == 400 and workspace.counts(eid)["submissions"] == 1)
                    for count in [1, 7, 20, 49]:
                        result = api.post("/exams", json={"title": f"SYNTHETIC-{count}", "kind": "mcq", "answer_key": ["C"] * count})
                        ceid = result.json()["id"]
                        image = png_bytes(template({i: ["C"] for i in range(count)}, True, question_count=count))
                        result = api.post(f"/exams/{ceid}/submissions", data={"student_code": f"SYNTHETIC-{count}"},
                                          files={"file": ("sheet.png", image, "image/png")})
                        csid = result.json()["id"]
                        result = api.post(f"/submissions/{csid}/approve-mcq",
                                          json={"revision": 1, "reviewed": True, "answers": ["C"] * count})
                        check(f"real_api_count_{count}", result.status_code == 200 and result.json()["total"] == count)
                    for count in [0, 51]:
                        result = api.post("/exams", json={"title": "BAD-COUNT", "kind": "mcq", "answer_key": ["A"] * count})
                        check(f"reject_count_{count}", result.status_code == 422)
                    page.set_viewport_size({"width": 390, "height": 844})
                    expect(page.get_by_role("combobox", name="انتقل إلى", exact=True)).to_be_visible()
                    check("mobile_results_fit_width", page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"))
                    page.screenshot(path=str(OUT / "04-mobile-50.png"), full_page=True)
                    report["passed"] = True
            except Exception as exc:
                status = 1
                report["passed"] = False
                detail = str(exc)
                for value in [password, token]:
                    if value:
                        detail = detail.replace(value, "[REDACTED]")
                report["failure"] = {"type": type(exc).__name__, "detail": detail[:1800]}
                if page:
                    page.screenshot(path=str(OUT / "failure.png"), full_page=True)
            finally:
                if runtime:
                    runtime.stop()
                if api_process:
                    api_process.terminate()
                    try:
                        api_process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        api_process.kill()
                        api_process.wait()
                browser.close()
                report["temporary_servers_stopped"] = True
                (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
                print(json.dumps({"passed": report.get("passed"), "checks": report["checks"]}, ensure_ascii=False))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
