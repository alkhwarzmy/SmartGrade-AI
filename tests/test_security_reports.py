import io
import numpy as np
import pytest
from PIL import Image
from openpyxl import load_workbook
from modules.preprocessing import decode_image, png_bytes
from modules.review import Store
from modules.mcq_grader import template
from modules.schemas import ExamSpec
from modules.reports import frames, excel_bytes, csv_bytes
from modules.evaluation import ocr_metrics, essay_metrics


@pytest.mark.parametrize("data", [b"not an image", b"<script>x</script>", b"x" * (10 * 1024 * 1024 + 1)])
def test_reject_upload(data):
    with pytest.raises(ValueError):
        decode_image(data)


def test_reject_gif_and_small_image():
    out = io.BytesIO()
    Image.fromarray(np.zeros((200, 200, 3), dtype=np.uint8)).save(out, format="GIF")
    with pytest.raises(ValueError):
        decode_image(out.getvalue())
    with pytest.raises(ValueError):
        decode_image(png_bytes(np.zeros((20, 20, 3), dtype=np.uint8)))


def test_path_traversal(tmp_path):
    store = Store(tmp_path)
    with pytest.raises(ValueError):
        store.image_path("../../secret.png")


def test_approved_only_arabic_and_safe_export(tmp_path):
    store = Store(tmp_path)
    eid = store.create_exam(ExamSpec(title="اختبار عربي", kind="mcq", answer_key=["A"] * 20))
    sid = store.upload(eid, "=HYPERLINK(1)", png_bytes(template()))
    assert frames(store)[0].empty
    store.approve_mcq(sid, ["A"] * 20, "معلم", "", 1, True)
    summary, details = frames(store)
    assert len(summary) == 1 and len(details) == 20
    excel = load_workbook(io.BytesIO(excel_bytes(summary, details)))
    sheet = excel["النتائج المعتمدة"]
    assert sheet["B2"].data_type != "f" and sheet["B2"].value.startswith("'")
    assert sheet["C2"].value == "اختبار عربي" and sheet.sheet_view.rightToLeft
    assert "اختبار عربي" in csv_bytes(summary).decode("utf-8-sig")


def test_metrics_are_computed_from_references():
    assert ocr_metrics(["abc def"], ["abc def"])["CER"] == 0
    assert ocr_metrics(["abc"], ["abd"])["CER"] == pytest.approx(1 / 3)
    metrics = essay_metrics([0, 2, 4], [0, 3, 4], 4)
    assert metrics["MAE"] == pytest.approx(1 / 3)
    assert metrics["within_one_point"] == 1
    assert essay_metrics([2], [2], 4)["QWK"] is None
    with pytest.raises(ValueError):
        ocr_metrics([], [])
