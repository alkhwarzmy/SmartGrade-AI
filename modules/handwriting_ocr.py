"""Optional local Tesseract baseline, not a validated Arabic handwriting model."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from config import ROOT
from modules.preprocessing import decode_image, png_bytes


class OCRUnavailable(ValueError):
    pass


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
        raise OCRUnavailable("OCR غير مفعّل؛ اضبط OCR_PROVIDER=tesseract أو أدخل النص يدويًا")
    available, message = ocr_status()
    if not available:
        raise OCRUnavailable(message)
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
        except (subprocess.SubprocessError, OSError) as exc:
            raise OCRUnavailable("فشل OCR المحلي؛ أدخل النص يدويًا أو أعد المحاولة") from exc
    text = result.stdout.strip()
    if not text or len(text) > 30000:
        raise OCRUnavailable("لم يُستخرج نص صالح؛ يلزم الإدخال أو المراجعة اليدوية")
    return text
