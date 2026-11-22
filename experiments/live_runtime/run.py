"""Exercise the real commercial web app, worker and API using only disposable synthetic data."""
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time

import httpx
from openpyxl import load_workbook
from playwright.sync_api import sync_playwright, expect
from sqlalchemy import select

from modules.auth import AuthService
from modules.workspace import Workspace
from modules.schemas import ExamSpec
from modules.institution_runtime import launch_institution
from modules.essay_grader import provider_blockers
from modules.grading_evaluation import compare_proposal
from database.models import Job

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "experiments/live_runtime/evidence"
OUT.mkdir(parents=True, exist_ok=True)
report = {
    "source_commit": os.getenv("TESTED_SOURCE", ""),
    "execution": "real_streamlit_browser_api_and_worker",
    "synthetic_data_only": True,
    "mocked_provider": False,
    "checks": {},
    "provider": {"requests_triggered": 0, "model_evaluated": False},
    "persistent_hosting_created": False,
    "google_colab_account_tested": False,
}
last_step = "setup"


def step(name):
    global last_step
    last_step = name
    print("runtime_step:", name, flush=True)


def check(name, condition):
    report["checks"][name] = bool(condition)
    if not condition:
        raise AssertionError(name)


def wait_for(function, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = function()
        if value:
            return value
        time.sleep(0.3)
    raise TimeoutError("runtime check timed out")


def unused_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def stop_api(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def login_web(page, web_url, password):
    page.goto(web_url, wait_until="domcontentloaded")
    page.get_by_label("رمز المؤسسة", exact=True).fill("runtime-school")
    page.get_by_label("البريد الإلكتروني", exact=True).fill("runner@smartgrade.test")
    page.get_by_label("كلمة المرور", exact=True).fill(password)
    page.get_by_role("button", name="دخول إلى مساحة العمل", exact=True).click()
    expect(page.get_by_test_id("stSidebar")).to_be_visible(timeout=30000)
    expect(page.get_by_test_id("stSidebar").get_by_text("الاختبارات", exact=True)).to_be_visible()


def navigate(page, label):
    page.get_by_test_id("stSidebar").get_by_text(label, exact=True).click()
    heading = "رفع أوراق الاختبار" if label == "رفع الأوراق" else label
    expect(page.get_by_role("heading", name=heading, exact=True)).to_be_visible(timeout=30000)


def snapshot(page, name):
    page.screenshot(path=str(OUT / (name + ".png")), full_page=True)


def main():
    stale_failure = OUT / "failure.png"
    if stale_failure.exists():
        stale_failure.unlink()
    page = None
    runtime = None
    api_process = None
    exit_status = 0
    fixture = json.loads((ROOT / "assets/evaluation/arabic_essay.json").read_text())
    case = next(c for c in fixture["cases"] if c["id"] == "student-c")
    spec = ExamSpec.model_validate(fixture["exam"])
    image = ROOT / "assets/evaluation/student-c.png"
    blocker_codes = provider_blockers()
    report["provider"]["configuration_blockers"] = blocker_codes
    report["provider"]["key_present"] = bool(os.getenv("LLM_API_KEY", "").strip())
    report["provider"]["model_configured"] = bool(os.getenv("LLM_MODEL", "").strip())
    live_ready = not blocker_codes
    with tempfile.TemporaryDirectory(prefix="smartgrade-actual-run-") as temporary:
        data_dir = Path(temporary) / "data"
        password = secrets.token_urlsafe(30)
        auth = AuthService(data_dir)
        auth.provision_organization("runtime-school", "مؤسسة اختبار اصطناعية",
                                    "runner@smartgrade.test", "فاحص التشغيل", password, plan="school")
        token = auth.login("runtime-school", "runner@smartgrade.test", password)
        if live_ready:
            auth.set_ai_policy(token, True)
        workspace = Workspace(auth, token)
        api_port, web_port = unused_port(), unused_port()
        api_url, web_url = f"http://127.0.0.1:{api_port}", f"http://127.0.0.1:{web_port}"
        server_env = {**os.environ, "SMARTGRADE_DATA_DIR": str(data_dir),
                      "SMARTGRADE_MODE": "commercial", "OCR_PROVIDER": "tesseract"}
        extra_env = {name: os.environ[name] for name in [
            "LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL", "ALLOW_EXTERNAL_AI", "ALLOW_PAID_API"
        ] if name in os.environ}
        with (Path(temporary) / "api.log").open("w") as api_log, sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={"width": 1600, "height": 1100}, locale="ar")
            context.set_default_timeout(30000)
            expect.set_options(timeout=30000)
            page = context.new_page()
            try:
                step("start_api_web_and_worker")
                api_process = subprocess.Popen([sys.executable, "-m", "uvicorn", "api:app",
                    "--host", "127.0.0.1", "--port", str(api_port)], cwd=ROOT, env=server_env,
                    stdout=api_log, stderr=api_log)
                def api_ready():
                    try:
                        return httpx.get(api_url + "/ready", timeout=2).status_code == 200
                    except httpx.HTTPError:
                        return False
                wait_for(api_ready)
                runtime = launch_institution(ROOT, sys.executable, data_dir, port=web_port,
                                             extra_env=extra_env, timeout=60)
                check("real_web_and_worker_started", runtime.process.poll() is None and runtime.worker.poll() is None)
                with httpx.Client(base_url=api_url, headers={"Authorization": "Bearer " + token}, timeout=30) as api:
                    check("anonymous_api_denied", httpx.get(api_url + "/exams", timeout=10).status_code == 401)
                    response = api.post("/exams", json=spec.model_dump())
                    check("five_question_exam_created_via_real_api", response.status_code == 201)
                    eid = response.json()["id"]
                    step("login_and_upload_in_browser")
                    login_web(page, web_url, password)
                    navigate(page, "رفع الأوراق")
                    page.locator('input[type="file"]').set_input_files(str(image))
                    expect(page.get_by_text("student-c.png", exact=True)).to_be_visible()
                    page.get_by_label("رمز الطالب", exact=True).fill("SYNTHETIC-LIVE-C")
                    page.get_by_role("button", name="رفع وبدء المعالجة", exact=True).click()
                    expect(page.get_by_text(re.compile("تم حفظ الورقة"))).to_be_visible(timeout=45000)
                    papers = api.get(f"/exams/{eid}/submissions").json()
                    check("image_uploaded_from_real_browser", len(papers) == 1)
                    sid = papers[0]["id"]
                    snapshot(page, "01-upload")
                    step("real_ocr_job")
                    navigate(page, "التصحيح والمراجعة")
                    page.get_by_role("button", name="استخراج النص بمحرك OCR المحلي", exact=True).click()
                    def get_job():
                        with workspace._core.sessions() as db:
                            return db.scalar(select(Job).where(Job.kind == "ocr").order_by(Job.created_at.desc()))
                    job = wait_for(get_job)
                    def completed():
                        current = api.get(f"/jobs/{job.id}").json()
                        return current if current["state"] in {"succeeded", "failed", "cancelled"} else None
                    result = wait_for(completed, 75)
                    check("actual_tesseract_worker_completed", result["state"] == "succeeded")
                    paper = api.get(f"/submissions/{sid}").json()
                    check("ocr_text_saved", len(paper["text"]) > 100)
                    report["ocr"] = {"saved_characters": len(paper["text"]), "engine": "real_tesseract"}
                    page.get_by_role("button", name="تحديث حالة استخراج النص", exact=True).click()
                    source_matches = page.get_by_label("النص المستخدم للتصحيح", exact=True)
                    report["ocr"]["label_matches"] = source_matches.evaluate_all(
                        "(items) => items.map(e => ({tag: e.tagName, role: e.getAttribute('role')}))")
                    source_field = source_matches.and_(page.locator("textarea")).last
                    try:
                        wait_for(lambda: " ".join(source_field.input_value().split()) ==
                                 " ".join(paper["text"].split()), timeout=35)
                    finally:
                        (OUT / "ocr-public-sample.txt").write_text(paper["text"])
                        (OUT / "browser-ocr-public-sample.txt").write_text(source_field.input_value())
                        report["ocr"]["browser_characters"] = len(source_field.input_value())
                    check("browser_displays_saved_ocr_text", True)
                    snapshot(page, "02-ocr")
                    step("map_five_answers_in_browser")
                    for number, question in enumerate(spec.questions, 1):
                        field = page.get_by_label(f"إجابة السؤال {number}", exact=True).and_(page.locator("textarea")).last
                        field.fill(case["answers"][question.id])
                        field.press("Tab")
                        page.wait_for_timeout(500)
                    page.get_by_role("button", name="حفظ إجابات الأسئلة", exact=True).click()
                    def answers_saved():
                        current = api.get(f"/submissions/{sid}").json()
                        return current if current["current"].get("essay_answers") == case["answers"] else None
                    paper = wait_for(answers_saved)
                    check("five_answers_saved_from_browser", True)
                    step("diagnostics_and_unsaved_source_guard")
                    page.get_by_role("button", name="فحص سبب تعثر التصحيح", exact=True).click()
                    expect(page.get_by_text("نتيجة فحص التصحيح", exact=True)).to_be_visible()
                    diagnosis = api.get(f"/submissions/{sid}/diagnostics").json()
                    report["provider"]["readiness_codes"] = [b["code"] for b in diagnosis["blockers"]]
                    check("diagnostic_check_makes_no_provider_call",
                          diagnosis["external_call_performed_by_this_check"] is False)
                    original = paper["text"]
                    source = page.get_by_label("النص المستخدم للتصحيح", exact=True).and_(page.locator("textarea")).last
                    source.fill(original + "\nمسودة غير محفوظة")
                    source.press("Tab")
                    expect(page.get_by_role("button", name="اعتماد الدرجة المقالية", exact=True)).to_be_disabled()
                    expect(page.get_by_role("button", name="اقتراح درجات بالذكاء الاصطناعي", exact=True)).to_be_disabled()
                    check("unsaved_source_blocks_grading_in_real_browser",
                          api.get(f"/submissions/{sid}").json()["text"] == original)
                    source.fill(original)
                    source.press("Tab")
                    expect(page.get_by_role("button", name="اعتماد الدرجة المقالية", exact=True)).to_be_enabled()
                    snapshot(page, "03-diagnostics")
                    step("teacher_reference_grading_in_browser")
                    grades = page.get_by_label("درجة المعيار", exact=True).and_(page.locator("input"))
                    expect(grades).to_have_count(15)
                    evidence = page.get_by_label("دليل حرفي من الإجابة (اختياري)", exact=True).and_(page.locator("input"))
                    rationales = page.get_by_label("تبرير الدرجة", exact=True).and_(page.locator("input"))
                    missing = page.get_by_label("العناصر الناقصة (إن وجدت)", exact=True).and_(page.locator("input"))
                    index = 0
                    for qindex, question in enumerate(spec.questions):
                        for cindex, criterion in enumerate(question.criteria):
                            mark = case["scores"][qindex][cindex]
                            grades.nth(index).fill(str(mark))
                            evidence.nth(index).fill(case["answers"][question.id] if mark else "")
                            rationales.nth(index).fill("تحقق عنصران" if mark == 2 else "تحقق عنصر واحد" if mark else "لم يتحقق المعيار")
                            missing.nth(index).fill("" if mark == 2 else criterion.description)
                            index += 1
                    page.get_by_label("راجعت نص الإجابة وجميع المعايير وأوافق على الدرجات", exact=True).and_(page.locator('input[type="checkbox"]')).check(force=True)
                    page.get_by_role("button", name="اعتماد الدرجة المقالية", exact=True).click()
                    def approved():
                        current = api.get(f"/submissions/{sid}").json()
                        return current if current["status"] == "approved" else None
                    paper = wait_for(approved)
                    check("teacher_reference_grade_persisted", paper["total"] == 17)
                    report["teacher_grade"] = {"source": "reference_entered_by_test_teacher",
                                                "total": paper["total"], "maximum": 30,
                                                "question_totals": [2, 5, 4, 3, 3],
                                                "this_is_not_a_model_prediction": True}
                    expect(page.get_by_text("نتيجة معتمدة: 17 / 30", exact=True)).to_be_visible()
                    snapshot(page, "04-approved")
                    step("download_real_reports_in_browser")
                    navigate(page, "النتائج والتصدير")
                    for label, extension in [
                        ("تصدير Excel مع تفاصيل التصحيح", "xlsx"),
                        ("تصدير CSV", "csv"), ("تصدير PDF بالعربية", "pdf")
                    ]:
                        with page.expect_download() as event:
                            page.get_by_role("button", name=label, exact=True).click()
                        download = event.value
                        destination = OUT / ("synthetic-results." + extension)
                        download.save_as(str(destination))
                        check("browser_download_" + extension, destination.stat().st_size > 100)
                    workbook = load_workbook(OUT / "synthetic-results.xlsx", data_only=True)
                    check("question_totals_in_actual_excel", "درجات الأسئلة" in workbook.sheetnames)
                    rows = list(workbook["درجات الأسئلة"].values)
                    score_index = rows[0].index("الدرجة")
                    check("excel_question_scores_match_reference", [r[score_index] for r in rows[1:]] == [2, 5, 4, 3, 3])
                    check("actual_pdf_is_pdf", (OUT / "synthetic-results.pdf").read_bytes().startswith(b"%PDF-"))
                    snapshot(page, "05-results")
                    step("optional_one_real_model_request")
                    if live_ready:
                        upload = api.post(f"/exams/{eid}/submissions", data={"student_code": "SYNTHETIC-MODEL-C"},
                                          files={"file": ("student-c.png", image.read_bytes(), "image/png")})
                        check("separate_model_test_paper_created", upload.status_code == 201)
                        model_sid = upload.json()["id"]
                        saved = api.put(f"/submissions/{model_sid}/essay-answers",
                                        json={"revision": 1, "answers": case["answers"]})
                        check("model_test_answers_saved", saved.status_code == 200)
                        report["provider"]["requests_triggered"] = 1
                        result = api.post(f"/submissions/{model_sid}/proposal",
                                          json={"revision": saved.json()["revision"],
                                                "consent_to_external_processing_and_cost": True}, timeout=75)
                        if result.status_code == 200:
                            proposal = result.json()["proposal"]
                            report["provider"].update({"model_evaluated": True,
                                "comparison": compare_proposal(spec, case, proposal)})
                        else:
                            error = result.json()
                            report["provider"].update({"model_evaluated": False,
                                "status": "request_failed", "http_status": result.status_code,
                                "code": error.get("code", "provider_failed")})
                    else:
                        report["provider"]["status"] = "blocked_missing_operator_configuration"
                    check("model_attempt_did_not_change_teacher_paper",
                          api.get(f"/submissions/{sid}").json()["total"] == 17)
                    step("restart_and_verify_saved_grade")
                    runtime.stop()
                    runtime = None
                    stop_api(api_process)
                    api_process = None
                    api_process = subprocess.Popen([sys.executable, "-m", "uvicorn", "api:app",
                        "--host", "127.0.0.1", "--port", str(api_port)], cwd=ROOT, env=server_env,
                        stdout=api_log, stderr=api_log)
                    wait_for(api_ready)
                    runtime = launch_institution(ROOT, sys.executable, data_dir, port=web_port,
                                                 extra_env=extra_env, timeout=60)
                    login_web(page, web_url, password)
                    navigate(page, "التصحيح والمراجعة")
                    # The model test, when enabled, is a separate newer submission; persistence is checked by API.
                    check("approved_grade_survived_process_restart",
                          api.get(f"/submissions/{sid}").json()["total"] == 17)
                    report["passed"] = True
                    step("complete")
            except Exception as exc:
                report["passed"] = False
                error_text = str(exc)
                for sensitive in [password, token, os.getenv("LLM_API_KEY", "")]:
                    if sensitive:
                        error_text = error_text.replace(sensitive, "[REDACTED]")
                report["failure"] = {"step": last_step, "exception_type": type(exc).__name__,
                                     "detail": error_text[:1600]}
                if page is not None:
                    try:
                        snapshot(page, "failure")
                    except Exception:
                        pass
                exit_status = 1
            finally:
                report["last_step"] = last_step
                cleanup = True
                if runtime is not None:
                    try:
                        runtime.stop()
                    except Exception:
                        cleanup = False
                try:
                    stop_api(api_process)
                except Exception:
                    cleanup = False
                browser.close()
                report["temporary_servers_stopped"] = cleanup
                (OUT / "runtime-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
                print(json.dumps({"passed": report.get("passed", False), "last_step": last_step,
                                  "checks_passed": sum(report["checks"].values()),
                                  "model_evaluated": report["provider"]["model_evaluated"],
                                  "provider_blockers": blocker_codes}, ensure_ascii=False), flush=True)
    return exit_status


if __name__ == "__main__":
    raise SystemExit(main())
