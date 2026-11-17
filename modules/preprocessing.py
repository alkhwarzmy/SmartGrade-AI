"""Strict image decoding; filenames are never used as filesystem paths."""

import io
import warnings
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError
from config import MAX_FILE_BYTES, MAX_IMAGE_PIXELS


def decode_image(raw: bytes) -> np.ndarray:
    if not raw or len(raw) > MAX_FILE_BYTES:
        raise ValueError("حجم الملف غير مقبول؛ الحد الأقصى 10 MB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as im:
                if im.format not in {"PNG", "JPEG"}:
                    raise ValueError("الأنواع المدعومة PNG وJPEG فقط")
                if im.width * im.height > MAX_IMAGE_PIXELS or min(im.size) < 100:
                    raise ValueError("أبعاد الصورة غير مقبولة")
                im.load()
                return np.asarray(ImageOps.exif_transpose(im).convert("RGB")).copy()
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValueError("تعذر قراءة الصورة أو تجاوزت حدود الأمان") from exc


def png_bytes(image: np.ndarray) -> bytes:
    out = io.BytesIO()
    Image.fromarray(image).save(out, format="PNG")
    return out.getvalue()
