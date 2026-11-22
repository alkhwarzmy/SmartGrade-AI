"""Optional local Tesseract baseline, not a validated Arabic handwriting model."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from config import ROOT
from modules.preprocessing import decode_image, png_bytes
from modules.processing import ProcessingError, MESSAGES


class OCRUnavailable(ProcessingError):
    def __init__(self, code="ocr_failed"):
        super().__init__(code if code in MESSAGES else "ocr_failed")


def tessdata_args():
    bundled = ROOT / "assets" / "tessdata"
    return ["--tessdata-dir", str(bundled)] if (bundled / "ara.traineddata").exists() else []


def ocr_status():
    if not shutil.which("tesseract"):
        return False, "Tesseract غير مثبت؛ استخدم إدخال النص يدويًا"
    try:
        result = subprocess.run(
            ["tesseract", *tessdata_args(), "--list-langs"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        if "ara" not in result.stdout.split():
            return False, "حزمة اللغة العربية ara غير مثبتة؛ الإدخال اليدوي متاح"
        return True, "Tesseract عربي محلي متاح؛ جودة خط اليد غير معتمدة"
    except (subprocess.SubprocessError, OSError):
        return False, "تعذر التحقق من محرك OCR المحلي"


def recognize(raw: bytes) -> str:
    if os.getenv("OCR_PROVIDER", "tesseract") != "tesseract":
        raise OCRUnavailable("ocr_disabled")
    available, _ = ocr_status()
    if not available:
        raise OCRUnavailable("ocr_unavailable")
    image = decode_image(raw)
    with tempfile.TemporaryDirectory(prefix="smartgrade-ocr-") as directory:
        path = Path(directory) / "answer.png"
        path.write_bytes(png_bytes(image))
        try:
            result = subprocess.run(
                ["tesseract", str(path), "stdout", *tessdata_args(), "-l", "ara", "--psm", "6"],
                capture_output=True,
                text=True,
                timeout=45,
                check=True,
            )
        except subprocess.TimeoutExpired as exc:
            raise OCRUnavailable("ocr_timeout") from exc
        except (subprocess.SubprocessError, OSError) as exc:
            raise OCRUnavailable("ocr_failed") from exc
    text = result.stdout.strip()
    if not text:
        raise OCRUnavailable("ocr_empty")
    if len(text) > 30000:
        raise OCRUnavailable("ocr_text_too_large")
    return text
