"""Verify actual count-specific OMR, invalid layout rejection and storage/report integration."""
from pathlib import Path
import cv2
import numpy as np
import pytest
from modules.mcq_grader import template, read_answers, bubble_center, score_answers, geometry, annotate, BAR_X, BAR_Y
from modules.preprocessing import png_bytes, decode_image
from modules.schemas import ExamSpec
from modules.review import Store
from modules.reports import frames


@pytest.mark.parametrize("count", range(1, 51))
def test_all_teacher_counts_round_trip(count):
    key = ["ABCD"[i % 4] for i in range(count)]
    image = template({i: [answer] for i, answer in enumerate(key)}, True, question_count=count)
    aligned, rows = read_answers(image, question_count=count)
    assert [r["answer"] for r in rows] == key
    assert score_answers(key, key, 2.5) == count * 2.5
    assert annotate(aligned, rows).shape == image.shape


@pytest.mark.parametrize("count", [1, 7, 19, 21, 35, 49, 50])
def test_dynamic_rotation_lighting_and_jpeg(count):
    image = template({i: ["C"] for i in range(count)}, True, question_count=count)
    image = cv2.copyMakeBorder(image, 130, 130, 130, 130, cv2.BORDER_CONSTANT, value=(255,) * 3)
    h, w = image.shape[:2]
    image = cv2.warpAffine(image, cv2.getRotationMatrix2D((w / 2, h / 2), 5, 1),
                          (w, h), borderValue=(255,) * 3)
    image = (image * np.linspace(0.7, 1, w)[None, :, None]).astype(np.uint8)
    ok, jpeg = cv2.imencode(".jpg", cv2.cvtColor(image, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 55])
    assert ok
    _, rows = read_answers(decode_image(jpeg.tobytes()), question_count=count)
    assert [r["answer"] for r in rows] == ["C"] * count


@pytest.mark.parametrize("printed,expected", [(20, 19), (19, 20), (50, 20), (20, 50), (49, 50), (50, 49), (1, 50)])
def test_mismatched_count_is_rejected(printed, expected):
    with pytest.raises(ValueError):
        read_answers(template(question_count=printed), question_count=expected)


@pytest.mark.parametrize("count", [0, -1, 51, True, 2.5, "50"])
def test_invalid_template_counts(count):
    with pytest.raises(ValueError):
        template(question_count=count)


def test_count_marker_corruption_does_not_become_blank_paper():
    image = template(question_count=50)
    image[BAR_Y:BAR_Y + 18, BAR_X:BAR_X + 18] = 255
    with pytest.raises(ValueError):
        read_answers(image, question_count=50)


def test_fifty_blank_multiple_and_weak_marks():
    image = template({i: (["A", "B"] if i == 1 else ["A"])
                      for i in range(50) if i != 2}, True, question_count=50)
    radius = geometry(50)[2]
    cv2.circle(image, bubble_center(3, 0, 50), radius - 3, (175, 175, 175), -1)
    _, rows = read_answers(image, question_count=50)
    assert [r["state"] for r in rows[:4]] == ["single", "multiple", "blank", "unclear"]
    assert score_answers([r["answer"] for r in rows], ["A"] * 50) == 47


def test_original_twenty_question_fixture_is_still_readable():
    raw = (Path(__file__).parents[1] / "assets/omr/legacy20.png").read_bytes()
    _, rows = read_answers(decode_image(raw))
    assert [r["answer"] for r in rows] == list("ABCD" * 5)


@pytest.mark.parametrize("count", [1, 7, 50])
def test_variable_storage_approval_and_report_survive_reopen(tmp_path, count):
    store = Store(tmp_path)
    eid = store.create_exam(ExamSpec(title="متغير", kind="mcq", answer_key=["A"] * count, points_per_question=2))
    if count != 20:
        with pytest.raises(ValueError):
            store.upload(eid, "WRONG-SHEET", png_bytes(template()))
        assert store.counts(eid)["submissions"] == 0
    sid = store.upload(eid, "SYNTHETIC", png_bytes(template(
        {i: ["A"] for i in range(count)}, True, question_count=count)))
    with pytest.raises(ValueError):
        store.approve_mcq(sid, ["A"] * (count + 1), "معلم", "", 1, True)
    assert store.submission(sid).total is None
    store.approve_mcq(sid, ["A"] * count, "معلم", "", 1, True)
    reopened = Store(tmp_path)
    assert reopened.submission(sid).total == 2 * count
    summary, details = frames(reopened, eid)
    assert summary.iloc[0]["الدرجة"] == 2 * count
    assert len(details) == count
