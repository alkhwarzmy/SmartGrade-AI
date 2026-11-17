import cv2
import numpy as np
import pytest
from modules.mcq_grader import template, read_answers, bubble_center, score_answers, align


def test_blank_single_multiple_and_unclear():
    image = template({0: ["B"], 1: ["A", "C"]}, synthetic=True)
    cv2.circle(image, bubble_center(3, 0), 19, (175, 175, 175), -1)
    _, readings = read_answers(image)
    assert readings[0]["answer"] == "B"
    assert readings[1]["state"] == "multiple"
    assert readings[2]["state"] == "blank"
    assert readings[3]["state"] == "unclear"
    assert score_answers([r["answer"] for r in readings], ["B"] * 20) == 1


@pytest.mark.parametrize("angle", [-5, 5])
def test_rotation_and_lighting(angle):
    image = template({i: ["ABCD"[i % 4]] for i in range(20)}, synthetic=True)
    # Pad before rotation to preserve registration squares.
    image = cv2.copyMakeBorder(image, 130, 130, 130, 130, cv2.BORDER_CONSTANT, value=(255,) * 3)
    h, w = image.shape[:2]
    image = cv2.warpAffine(
        image, cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1), (w, h), borderValue=(255,) * 3
    )
    gradient = np.linspace(0.7, 1, w)[None, :, None]
    image = (image * gradient).astype(np.uint8)
    _, readings = read_answers(image)
    assert [r["answer"] for r in readings] == ["ABCD"[i % 4] for i in range(20)]


def test_perspective_and_scan_resize():
    image = template({i: ["C"] for i in range(20)}, synthetic=True)
    h, w = image.shape[:2]
    src = np.float32([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]])
    dst = np.float32([[100, 50], [w - 90, 10], [w - 5, h - 70], [20, h - 5]])
    warped = cv2.warpPerspective(image, cv2.getPerspectiveTransform(src, dst), (w, h), borderValue=(255,) * 3)
    warped = cv2.resize(warped, (w // 2, h // 2))
    _, rows = read_answers(warped)
    assert [r["answer"] for r in rows] == ["C"] * 20


@pytest.mark.parametrize(
    "image", [np.ones((400, 400, 3), dtype=np.uint8) * 255, cv2.rotate(template(), cv2.ROTATE_180)]
)
def test_reject_invalid_alignment(image):
    with pytest.raises(ValueError):
        align(image)
