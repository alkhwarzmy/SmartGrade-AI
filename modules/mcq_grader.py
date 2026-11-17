"""Fixed-template OMR v1. Designed for upright sheets with four visible registration squares."""

import io
import os
import cv2
import numpy as np
from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from modules.schemas import CHOICES

# OpenCV otherwise sizes its thread pool from host CPUs rather than container CPU quotas.
cv2.setNumThreads(max(1, min(8, int(os.getenv("SMARTGRADE_CV_THREADS", "2")))))
WIDTH, HEIGHT = 1240, 1754
MARKERS = np.float32([[70, 70], [1170, 70], [1170, 1684], [70, 1684]])


def bubble_center(question: int, choice: int) -> tuple[int, int]:
    col, row = divmod(question, 10)
    return 280 + col * 540 + choice * 80, 420 + row * 105


def template(marks: dict[int, list[str]] | None = None, synthetic: bool = False) -> np.ndarray:
    image = np.full((HEIGHT, WIDTH, 3), 255, np.uint8)
    for x, y in MARKERS.astype(int):
        cv2.rectangle(image, (x - 20, y - 20), (x + 20, y + 20), (0, 0, 0), -1)
    cv2.circle(image, (160, 125), 12, (0, 0, 0), -1)  # upright orientation check
    cv2.putText(
        image, "SmartGrade / OMR v1", (200, 195), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (20, 35, 40), 2, cv2.LINE_AA
    )
    cv2.putText(
        image,
        "20 QUESTIONS | A B C D | Fill one circle completely",
        (170, 255),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (40, 40, 40),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        image,
        "Student code: __________________  Exam: ______________",
        (170, 310),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (40, 40, 40),
        1,
        cv2.LINE_AA,
    )
    for q in range(20):
        x, y = bubble_center(q, 0)
        cv2.putText(
            image,
            f"{q + 1:02d}",
            (x - 95, y + 8),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (20, 20, 20),
            2,
            cv2.LINE_AA,
        )
        for c, letter in enumerate(CHOICES):
            cx, cy = bubble_center(q, c)
            cv2.circle(image, (cx, cy), 22, (0, 0, 0), 2, cv2.LINE_AA)
            cv2.putText(
                image, letter, (cx - 7, cy - 34), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (50, 50, 50), 1, cv2.LINE_AA
            )
            if marks and letter in marks.get(q, []):
                cv2.circle(image, (cx, cy), 19, (0, 0, 0), -1, cv2.LINE_AA)
    label = (
        "SYNTHETIC TEST DATA - NOT A REAL EXAM"
        if synthetic
        else "Print A4 portrait. Keep all four black squares visible."
    )
    cv2.putText(image, label, (170, 1580), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (40, 40, 40), 1)
    return image


def template_pdf() -> bytes:
    out = io.BytesIO()
    pdf = canvas.Canvas(out, pagesize=(595.28, 841.89))
    pdf.drawImage(ImageReader(Image.fromarray(template())), 0, 0, width=595.28, height=841.89)
    pdf.setTitle("SmartGrade OMR v1 - A4")
    pdf.showPage()
    pdf.save()
    return out.getvalue()


def align(image: np.ndarray) -> np.ndarray:
    scale = min(1.0, 2000 / max(image.shape[:2]))
    small = cv2.resize(image, None, fx=scale, fy=scale)
    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 51, 15)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    h, w = gray.shape
    candidates = []
    for contour in contours:
        area = cv2.contourArea(contour)
        polygon = cv2.approxPolyDP(contour, 0.035 * cv2.arcLength(contour, True), True)
        if len(polygon) != 4 or not cv2.isContourConvex(polygon):
            continue
        x, y, cw, ch = cv2.boundingRect(contour)
        if not (0.0002 * h * w < area < 0.008 * h * w):
            continue
        if not (0.65 < cw / ch < 1.5 and area / (cw * ch) > 0.7):
            continue
        cx, cy = x + cw / 2, y + ch / 2
        if (cx < 0.22 * w or cx > 0.78 * w) and (cy < 0.22 * h or cy > 0.78 * h):
            candidates.append((cx, cy))
    if len(candidates) != 4:
        raise ValueError("تعذر العثور على العلامات الأربع بوضوح؛ ارفع القالب كاملًا بخلفية بسيطة")
    pts = np.float32(candidates)
    ordered = np.float32(
        [
            pts[np.argmin(pts.sum(1))],
            pts[np.argmax(pts[:, 0] - pts[:, 1])],
            pts[np.argmax(pts.sum(1))],
            pts[np.argmin(pts[:, 0] - pts[:, 1])],
        ]
    )
    if len(np.unique(ordered, axis=0)) != 4 or abs(cv2.contourArea(ordered)) < 0.35 * w * h:
        raise ValueError("هندسة الورقة غير صالحة للمحاذاة")
    matrix = cv2.getPerspectiveTransform(ordered, MARKERS)
    warped = cv2.warpPerspective(small, matrix, (WIDTH, HEIGHT), borderValue=(255, 255, 255))
    oriented = cv2.cvtColor(warped, cv2.COLOR_RGB2GRAY)
    if np.mean(oriented[118:132, 153:167]) > 100:
        raise ValueError("اتجاه الورقة أو إصدار القالب غير صحيح؛ استخدم OMR v1 بوضع رأسي")
    # Confirm expected bubble outlines; avoids treating arbitrary four-square pages as blank exams.
    yy, xx = np.mgrid[-25:26, -25:26]
    ring = (xx * xx + yy * yy >= 20**2) & (xx * xx + yy * yy <= 24**2)
    valid = 0
    for q in range(20):
        for c in range(4):
            x, y = bubble_center(q, c)
            valid += np.mean(oriented[y - 25 : y + 26, x - 25 : x + 26][ring] < 150) > 0.18
    if valid < 72:
        raise ValueError("الورقة لا تطابق القالب أو جودتها لا تسمح بالقراءة")
    return warped


def read_answers(
    image: np.ndarray, blank_threshold: float = 0.12, marked_threshold: float = 0.55
) -> tuple[np.ndarray, list[dict]]:
    if not 0 <= blank_threshold < marked_threshold <= 1:
        raise ValueError("حدود التظليل غير صحيحة")
    warped = align(image)
    gray = cv2.cvtColor(warped, cv2.COLOR_RGB2GRAY)
    yy, xx = np.mgrid[-14:15, -14:15]
    mask = xx * xx + yy * yy <= 14**2
    answers = []
    for q in range(20):
        strengths = []
        for c in range(4):
            x, y = bubble_center(q, c)
            patch = gray[y - 32 : y + 33, x - 32 : x + 33]
            background = max(float(np.percentile(patch, 90)), 1)
            ink = np.clip(1 - gray[y - 14 : y + 15, x - 14 : x + 15][mask] / background, 0, 1)
            strengths.append(round(float(np.mean(ink)), 4))
        strong = [CHOICES[c] for c, v in enumerate(strengths) if v >= marked_threshold]
        uncertain = any(blank_threshold <= v < marked_threshold for v in strengths)
        if len(strong) > 1:
            state, answer = "multiple", None
        elif uncertain:
            state, answer = "unclear", None
        elif strong:
            state, answer = "single", strong[0]
        else:
            state, answer = "blank", None
        answers.append({"question": q + 1, "state": state, "answer": answer, "strengths": strengths})
    return warped, answers


def score_answers(answers: list[str | None], key: list[str], points: float = 1) -> float:
    if len(answers) != 20 or len(key) != 20 or any(a not in (*CHOICES, None) for a in answers):
        raise ValueError("إجابات القالب غير صحيحة")
    return round(sum(a == k for a, k in zip(answers, key)) * points, 6)


def annotate(image: np.ndarray, readings: list[dict]) -> np.ndarray:
    annotated = image.copy()
    for i, row in enumerate(readings):
        color = (22, 135, 110) if row["state"] == "single" else (210, 90, 30)
        x, y = bubble_center(i, 0)
        cv2.rectangle(annotated, (x - 40, y - 30), (x + 280, y + 30), color, 2)
    return annotated
