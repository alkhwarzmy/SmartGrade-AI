"""User-flow regressions for the commercial workspace navigation and review queue."""
from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest
import config
from modules.review import Store
from modules.schemas import ExamSpec
from modules.mcq_grader import template
from modules.preprocessing import png_bytes


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTGRADE_MODE", "demo")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    store = Store(tmp_path)
    eid = store.create_exam(ExamSpec(title="علوم الفصل الأول", kind="mcq", answer_key=["A"] * 20))
    raw = png_bytes(template({i: ["A"] for i in range(20)}))
    first = store.upload(eid, "ST-001", raw)
    second = store.upload(eid, "ST-002", raw)
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=30).run()
    assert not app.exception
    return store, eid, first, second, app


def test_exam_shortcut_keeps_desktop_and_mobile_navigation_in_sync(workspace):
    store, eid, first, second, app = workspace
    app.button(key="open_upload_" + eid).click().run()
    assert not app.exception
    assert app.radio(key="navigation").value == "رفع الأوراق"
    assert app.selectbox(key="mobile_navigation").value == "رفع الأوراق"
    assert app.selectbox(key="upload_exam").value == eid
    assert next(b for b in app.button if b.label == "رفع وبدء المعالجة").disabled


def test_review_search_status_and_paper_navigation_do_not_write_grades(workspace):
    store, eid, first, second, app = workspace
    store.approve_mcq(first, ["A"] * 20, "المعلم", "", 1, True)
    app.radio(key="navigation").set_value("التصحيح والمراجعة").run()
    app.text_input(key="paper_query").set_value("ST-001").run()
    selector = app.selectbox(key="review_submission")
    assert selector.options and len(selector.options) == 1 and selector.value == first
    app.selectbox(key="paper_status").set_value("review").run()
    assert not app.exception and not any(s.key == "review_submission" for s in app.selectbox)
    app.text_input(key="paper_query").set_value("").run()
    assert app.selectbox(key="review_submission").value == second
    app.selectbox(key="paper_status").set_value("all").run()
    app.selectbox(key="review_submission").set_value(second).run()
    next(b for b in app.button if b.label == "الورقة التالية").click().run()
    assert not app.exception and app.selectbox(key="review_submission").value == first
    assert store.submission(first).total == 20
    assert not store.submission(second).approved
