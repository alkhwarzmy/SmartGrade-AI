"""Count-specific A4 OMR for 1..50 questions; preserve legacy OMR v1 at 20."""
import io
import math
import os
import cv2
import numpy as np
from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from modules.schemas import CHOICES, MAX_MCQ_QUESTIONS

cv2.setNumThreads(max(1, min(8, int(os.getenv("SMARTGRADE_CV_THREADS", "2")))))
WIDTH, HEIGHT = 1240, 1754
MARKERS = np.float32([[70, 70], [1170, 70], [1170, 1684], [70, 1684]])
BAR_X, BAR_Y, BAR_STEP, BAR_CELL = 320, 355, 26, 18
BAR_MAGIC = [1, 0, 1, 0]


def valid_count(count):
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_MCQ_QUESTIONS:
        raise ValueError("عدد أسئلة الاختيارات يجب أن يكون عددًا صحيحًا من 1 إلى 50")
    return count


def geometry(question_count=20):
    count = valid_count(question_count)
    if count == 20:
        return 10, 105, 22
    rows = (count + 1) // 2
    step = min(105, 1050 // max(1, rows - 1))
    return rows, step, min(22, step // 2 - 5)


def bubble_center(question: int, choice: int, question_count: int = 20) -> tuple[int, int]:
    rows, step, _ = geometry(question_count)
    if not 0 <= question < question_count or not 0 <= choice < 4:
        raise ValueError("رقم السؤال أو الاختيار خارج القالب")
    col, row = divmod(question, rows)
    return 280 + col * 540 + choice * 80, 420 + row * step


def _draw_count(image, count):
    bits = [(count >> shift) & 1 for shift in range(5, -1, -1)]
    values = BAR_MAGIC + bits + [sum(bits) % 2]
    for index, bit in enumerate(values):
        for pair, dark in enumerate((bool(bit), not bool(bit))):
            x = BAR_X + (2 * index + pair) * BAR_STEP
            cv2.rectangle(image, (x, BAR_Y), (x + BAR_CELL - 1, BAR_Y + BAR_CELL - 1),
                          (0, 0, 0) if dark else (255, 255, 255), -1)
    cv2.circle(image, (1060, 125), 9, (0, 0, 0), -1)


def _read_count(gray):
    means = []
    for cell in range(22):
        x = BAR_X + cell * BAR_STEP + BAR_CELL // 2
        y = BAR_Y + BAR_CELL // 2
        means.append(float(np.mean(gray[y - 5:y + 5, x - 5:x + 5])))
    if min(means) > 150:
        return None
    values = []
    for index in range(0, len(means), 2):
        a, b = means[index:index + 2]
        low, high = min(a, b), max(a, b)
        if high < 120 or high - low < 80 or low > 0.45 * high:
            raise ValueError("رمز عدد الأسئلة غير واضح؛ أعد تصوير القالب كاملًا")
        values.append(int(a < b))
    bits = values[4:10]
    if values[:4] != BAR_MAGIC or values[-1] != sum(bits) % 2:
        raise ValueError("رمز القالب غير صالح؛ استخدم القالب الذي نزّلته للاختبار")
    return valid_count(sum(bit << (5 - i) for i, bit in enumerate(bits)))


def template(marks: dict[int, list[str]] | None = None, synthetic: bool = False,
             question_count: int = 20) -> np.ndarray:
    count = valid_count(question_count)
    if marks is not None and any(
        isinstance(q, bool) or not isinstance(q, int) or not 0 <= q < count
        or not isinstance(choices, (list, tuple)) or any(c not in CHOICES for c in choices)
        for q, choices in marks.items()
    ):
        raise ValueError("تظليل خارج أسئلة القالب أو اختيار غير صالح")
    rows, _, radius = geometry(count)
    image = np.full((HEIGHT, WIDTH, 3), 255, np.uint8)
    for x, y in MARKERS.astype(int):
        cv2.rectangle(image, (x - 20, y - 20), (x + 20, y + 20), (0, 0, 0), -1)
    cv2.circle(image, (160, 125), 12, (0, 0, 0), -1)
    version = "v1" if count == 20 else "v2"
    cv2.putText(image, "SmartGrade / OMR " + version, (200, 195),
                cv2.FONT_HERSHEY_SIMPLEX, 1.3, (20, 35, 40), 2, cv2.LINE_AA)
    cv2.putText(image, f"{count} QUESTIONS | A B C D | Fill one circle completely",
                (170, 255), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (40, 40, 40), 1, cv2.LINE_AA)
    cv2.putText(image, "Student code: __________________  Exam: ______________",
                (170, 310), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (40, 40, 40), 1, cv2.LINE_AA)
    if count != 20:
        _draw_count(image, count)
    for q in range(count):
        x, y = bubble_center(q, 0, count)
        cv2.putText(image, f"{q + 1:02d}", (x - 95, y + 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (20, 20, 20), 2, cv2.LINE_AA)
        for c, letter in enumerate(CHOICES):
            cx, cy = bubble_center(q, c, count)
            cv2.circle(image, (cx, cy), radius, (0, 0, 0), 2, cv2.LINE_AA)
            if count == 20 or q % rows == 0:
                cv2.putText(image, letter, (cx - 7, cy - radius - 12), cv2.FONT_HERSHEY_SIMPLEX,
                            0.45, (50, 50, 50), 1, cv2.LINE_AA)
            if marks and letter in marks.get(q, []):
                cv2.circle(image, (cx, cy), radius - 3, (0, 0, 0), -1, cv2.LINE_AA)
    label = "SYNTHETIC TEST DATA - NOT A REAL EXAM" if synthetic else "Print A4 portrait. Keep all four black squares visible."
    cv2.putText(image, label, (170, 1580), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (40, 40, 40), 1)
    return image


def template_pdf(question_count: int = 20) -> bytes:
    count = valid_count(question_count)
    out = io.BytesIO()
    pdf = canvas.Canvas(out, pagesize=(595.28, 841.89))
    pdf.drawImage(ImageReader(Image.fromarray(template(question_count=count))), 0, 0,
                  width=595.28, height=841.89)
    pdf.setTitle(f"SmartGrade OMR - {count} questions - A4")
    pdf.showPage()
    pdf.save()
    return out.getvalue()


def align(image: np.ndarray, question_count: int = 20) -> np.ndarray:
    count = valid_count(question_count)
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
    ordered = np.float32([pts[np.argmin(pts.sum(1))], pts[np.argmax(pts[:, 0] - pts[:, 1])],
                          pts[np.argmax(pts.sum(1))], pts[np.argmin(pts[:, 0] - pts[:, 1])]])
    if len(np.unique(ordered, axis=0)) != 4 or abs(cv2.contourArea(ordered)) < 0.35 * w * h:
        raise ValueError("هندسة الورقة غير صالحة للمحاذاة")
    matrix = cv2.getPerspectiveTransform(ordered, MARKERS)
    warped = cv2.warpPerspective(small, matrix, (WIDTH, HEIGHT), borderValue=(255, 255, 255))
    oriented = cv2.cvtColor(warped, cv2.COLOR_RGB2GRAY)
    if np.mean(oriented[118:132, 153:167]) > 100:
        raise ValueError("اتجاه الورقة أو إصدار القالب غير صحيح؛ استخدم القالب بوضع رأسي")
    is_v2 = float(np.mean(oriented[119:132, 1054:1067])) < 100
    encoded_count = _read_count(oriented)
    if is_v2:
        if encoded_count is None or encoded_count != count:
            raise ValueError(f"عدد أسئلة القالب لا يطابق الاختبار ({count})؛ نزّل القالب الخاص بالاختبار")
    elif count != 20 or encoded_count is not None:
        raise ValueError(f"إصدار القالب أو عدد أسئلته لا يطابق الاختبار ({count})؛ نزّل القالب الصحيح")
    # The old twenty-question drawing is accepted only in its original geometry.
    _, _, radius = geometry(count)
    margin = radius + 3
    yy, xx = np.mgrid[-margin:margin + 1, -margin:margin + 1]
    ring = (xx * xx + yy * yy >= (radius - 2)**2) & (xx * xx + yy * yy <= (radius + 2)**2)
    valid = 0
    for q in range(count):
        for c in range(4):
            x, y = bubble_center(q, c, count)
            valid += np.mean(oriented[y - margin:y + margin + 1, x - margin:x + margin + 1][ring] < 150) > 0.18
    if valid < math.ceil(count * 4 * 0.9):
        raise ValueError("الورقة لا تطابق القالب أو جودتها لا تسمح بالقراءة")
    return warped


def read_answers(image: np.ndarray, blank_threshold: float = 0.12, marked_threshold: float = 0.55,
                 *, question_count: int = 20) -> tuple[np.ndarray, list[dict]]:
    count = valid_count(question_count)
    if not 0 <= blank_threshold < marked_threshold <= 1:
        raise ValueError("حدود التظليل غير صحيحة")
    warped = align(image, count)
    gray = cv2.cvtColor(warped, cv2.COLOR_RGB2GRAY)
    _, _, radius = geometry(count)
    inner, outer = radius - 8, radius + 10
    yy, xx = np.mgrid[-inner:inner + 1, -inner:inner + 1]
    mask = xx * xx + yy * yy <= inner**2
    answers = []
    for q in range(count):
        strengths = []
        for c in range(4):
            x, y = bubble_center(q, c, count)
            patch = gray[y - outer:y + outer + 1, x - outer:x + outer + 1]
            background = max(float(np.percentile(patch, 90)), 1)
            ink = np.clip(1 - gray[y - inner:y + inner + 1, x - inner:x + inner + 1][mask] / background, 0, 1)
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
    if not 1 <= len(key) <= MAX_MCQ_QUESTIONS or len(answers) != len(key) or any(a not in CHOICES for a in key):
        raise ValueError("عدد الإجابات لا يطابق أسئلة الاختبار (1 إلى 50)")
    if any(a not in (*CHOICES, None) for a in answers):
        raise ValueError("إجابات القالب غير صحيحة")
    return round(sum(a == k for a, k in zip(answers, key)) * points, 6)


def annotate(image: np.ndarray, readings: list[dict]) -> np.ndarray:
    count = valid_count(len(readings))
    annotated = image.copy()
    _, _, radius = geometry(count)
    for i, row in enumerate(readings):
        color = (22, 135, 110) if row["state"] == "single" else (210, 90, 30)
        x, y = bubble_center(i, 0, count)
        cv2.rectangle(annotated, (x - 40, y - radius - 8), (x + 280, y + radius + 8), color, 2)
    return annotated
