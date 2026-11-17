import pytest
from modules.review import Store
from modules.schemas import ExamSpec
from modules.preprocessing import png_bytes
from modules.mcq_grader import template


def test_mcq_persist_review_reopen_delete(tmp_path):
    store = Store(tmp_path)
    eid = store.create_exam(ExamSpec(title="اختبار", kind="mcq", answer_key=["A"] * 20))
    sid = store.upload(eid, "S01", png_bytes(template({0: ["A"]})))
    row = store.submission(sid)
    with pytest.raises(ValueError):
        store.approve_mcq(sid, ["A"] * 20, "معلم", "", row.revision, False)
    store.approve_mcq(sid, ["A"] * 20, "معلم", "تصحيح القراءة", row.revision, True)
    reopened = Store(tmp_path)
    saved = reopened.submission(sid)
    assert saved.status == "approved" and saved.total == 20
    assert saved.original["readings"][1]["state"] == "blank"
    assert len(reopened.history(sid)) == 2
    with pytest.raises(ValueError):
        reopened.approve_mcq(sid, ["A"] * 20, "معلم", "", row.revision, True)
    path = reopened.image_path(saved.image_name)
    reopened.delete_submission(sid, saved.revision)
    assert not path.exists() and not reopened.history(sid)
    with pytest.raises(ValueError):
        reopened.submission(sid)
