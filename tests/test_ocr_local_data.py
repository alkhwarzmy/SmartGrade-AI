import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import pytest
from modules.handwriting_ocr import recognize, ocr_status
from modules.preprocessing import png_bytes
from modules.local_data import import_manifest
import numpy as np


def test_local_arabic_ocr_baseline(monkeypatch):
    if not ocr_status()[0]:
        pytest.skip("Tesseract Arabic not installed")
    font = Path("/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf")
    if not font.exists():
        pytest.skip("Arabic test font not installed")
    monkeypatch.setenv("OCR_PROVIDER", "tesseract")
    image = Image.new("RGB", (1500, 300), "white")
    ImageDraw.Draw(image).text(
        (1400, 80),
        "الماء ضروري للحياة",
        font=ImageFont.truetype(str(font), 80),
        fill="black",
        anchor="rt",
        direction="rtl",
    )
    text = recognize(png_bytes(np.array(image)))
    assert "الماء" in text
    # A successful printed-text smoke test does NOT validate handwriting recognition.


def test_local_manifest_and_leakage(tmp_path, essay_spec):
    image = Image.new("RGB", (200, 200), "white")
    image.save(tmp_path / "answer.png")
    entry = {
        "sample_id": "test-1",
        "image": "answer.png",
        "reference_text": "مرجع مدقق",
        "exam": essay_spec.model_dump(),
        "human_scores": [3, 4],
        "split": "test",
    }
    manifest = tmp_path / "samples.json"
    manifest.write_text(json.dumps([entry]), encoding="utf-8")
    loaded = import_manifest(manifest)
    assert loaded[0].human_scores == [3, 4]
    manifest.write_text(json.dumps([entry, {**entry, "sample_id": "train-1", "split": "train"}]))
    with pytest.raises(ValueError, match="تسرب"):
        import_manifest(manifest)
    manifest.write_text(json.dumps([{**entry, "image": "../outside.png"}]))
    with pytest.raises(ValueError):
        import_manifest(manifest)


def test_ocr_disabled_is_explicit(monkeypatch):
    monkeypatch.setenv("OCR_PROVIDER", "manual")
    with pytest.raises(ValueError):
        recognize(b"unused")
