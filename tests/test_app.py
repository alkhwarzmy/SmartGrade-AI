from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest
import config
from modules.review import Store
from modules.schemas import ExamSpec
from modules.mcq_grader import template
from modules.preprocessing import png_bytes


@pytest.fixture(autouse=True)
def demo_mode(monkeypatch):
    monkeypatch.setenv("SMARTGRADE_MODE", "demo")


def test_app_create_exam_and_navigation(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=30).run()
    assert not app.exception
    app.text_input[0].set_value("اختبار واجهة")
    next(b for b in app.button if b.label == "إنشاء الاختبار").click().run()
    assert not app.exception and len(Store(tmp_path).exams()) == 1
    for page in ["رفع الأوراق", "التصحيح والمراجعة", "النتائج والتصدير", "الإعدادات"]:
        app.radio(key="navigation").set_value(page).run()
        assert not app.exception


def test_review_and_results_render(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    store = Store(tmp_path)
    eid = store.create_exam(ExamSpec(title="اختبار", kind="mcq", answer_key=["A"] * 20))
    sid = store.upload(eid, "test-student", png_bytes(template({i: ["A"] for i in range(20)})))
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=30).run()
    app.radio(key="navigation").set_value("التصحيح والمراجعة").run()
    assert not app.exception
    app.checkbox[0].check()
    next(b for b in app.button if b.label == "اعتماد النتيجة").click().run()
    assert not app.exception
    assert store.submission(sid).total == 20
    app.radio(key="navigation").set_value("النتائج والتصدير").run()
    assert not app.exception and app.dataframe


def test_manual_essay_review_ui(tmp_path, monkeypatch, essay_spec):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    store = Store(tmp_path)
    eid = store.create_exam(essay_spec)
    sid = store.upload(eid, "synthetic-essay", png_bytes(template()))
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=30).run()
    app.radio(key="navigation").set_value("التصحيح والمراجعة").run()
    app.text_area[0].set_value("الماء ضروري للحياة")
    next(b for b in app.button if b.label == "حفظ النص").click().run()
    assert not app.exception
    revision = store.submission(sid).revision
    prefix = f"{sid}_{revision}_c1"
    app.number_input(key="score_" + prefix).set_value(3.0)
    app.text_input(key="evidence_" + prefix).set_value("الماء")
    app.text_input(key="rationale_" + prefix).set_value("ذكر الفكرة الأساسية")
    next(c for c in app.checkbox if c.label == "راجعت نص الإجابة وجميع المعايير وأوافق على الدرجات").check()
    next(b for b in app.button if b.label == "اعتماد الدرجة المقالية").click().run()
    assert not app.exception and store.submission(sid).total == 3


def test_unsaved_essay_draft_survives_navigation_without_overwriting_concurrent_edit(
    tmp_path, monkeypatch, essay_spec
):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    store = Store(tmp_path)
    eid = store.create_exam(essay_spec)
    sid = store.upload(eid, "SYNTHETIC", png_bytes(template()))
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=30).run()
    app.radio(key="navigation").set_value("التصحيح والمراجعة").run()
    app.text_area[0].set_value("مسودة المعلم").run()
    app.radio(key="navigation").set_value("الاختبارات").run()
    app.radio(key="navigation").set_value("التصحيح والمراجعة").run()
    assert app.text_area[0].value == "مسودة المعلم" and store.submission(sid).text == ""
    store.save_text(sid, "تعديل من جلسة أخرى", "another-reviewer", 1)
    app.run()
    next(b for b in app.button if b.label == "حفظ النص").click().run()
    assert app.error and store.submission(sid).text == "تعديل من جلسة أخرى"
    assert app.text_area[0].value == "مسودة المعلم"
    next(b for b in app.button if b.label == "تحميل النص المحفوظ الأحدث").click().run()
    assert not app.exception and app.text_area[0].value == "تعديل من جلسة أخرى"
