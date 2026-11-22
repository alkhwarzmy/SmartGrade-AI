"""Observed teacher-facing regressions: stale visible text and erased approvals."""
from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest
import config
from modules.review import Store
from modules.preprocessing import png_bytes
from modules.mcq_grader import template


@pytest.mark.parametrize("stage", ["ocr", "llm"])
def test_failed_processing_preserves_existing_teacher_approval(tmp_path, essay_spec, valid_proposal, stage):
    store = Store(tmp_path)
    eid = store.create_exam(essay_spec)
    sid = store.upload(eid, "SYNTHETIC-DIAG", png_bytes(template()))
    store.save_text(sid, "الماء مهم", "teacher", 1)
    store.approve_essay(sid, valid_proposal, "teacher", "", store.submission(sid).revision, True)
    before = store.submission(sid)
    store.record_processing_failure(sid, stage, before.revision)
    after = store.submission(sid)
    assert (after.status, after.total, after.approved) == (before.status, before.total, before.approved)
    assert after.original[stage + "_failed"] is True
    assert store.history(sid)[-1].action == stage + "_failed"


@pytest.mark.parametrize("button_label", ["اقتراح درجات بالذكاء الاصطناعي", "اعتماد الدرجة المقالية"])
def test_visible_unsaved_source_blocks_grading(tmp_path, monkeypatch, essay_spec, button_label):
    monkeypatch.setenv("SMARTGRADE_MODE", "demo")
    for key, value in {"ALLOW_EXTERNAL_AI": "true", "ALLOW_PAID_API": "true",
                       "LLM_API_KEY": "synthetic-never-called", "LLM_MODEL": "synthetic"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    store = Store(tmp_path)
    eid = store.create_exam(essay_spec)
    sid = store.upload(eid, "SYNTHETIC-DRAFT", png_bytes(template()))
    store.save_text(sid, "الماء مهم", "teacher", 1)
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=30).run()
    app.radio(key="navigation").set_value("التصحيح والمراجعة").run()
    app.text_area[0].set_value("مسودة مختلفة لم تحفظ").run()
    next(c for c in app.checkbox if c.label.startswith("أوافق على إرسال")).check().run()
    assert not app.exception
    assert next(b for b in app.button if b.label == button_label).disabled
    assert store.submission(sid).text == "الماء مهم"
