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


def prepare_image(raw, rotation=0, horizontal=(0, 100), vertical=(0, 100)):
    """Explicit teacher-selected orientation and crop; preserve conservative OMR rejection."""
    if (
        not isinstance(rotation, int)
        or not -360 <= rotation <= 360
        or not all(0 <= low < high <= 100 for low, high in (horizontal, vertical))
    ):
        raise ValueError("منطقة القص أو التدوير غير صالحة")
    image = decode_image(raw)
    if rotation % 90 == 0:
        image = np.rot90(image, rotation // 90)
    else:
        import cv2

        height, width = image.shape[:2]
        matrix = cv2.getRotationMatrix2D((width / 2, height / 2), rotation, 1)
        cosine, sine = abs(matrix[0, 0]), abs(matrix[0, 1])
        new_width, new_height = int(height * sine + width * cosine), int(height * cosine + width * sine)
        if new_width * new_height > MAX_IMAGE_PIXELS:
            raise ValueError("الأبعاد بعد التدوير كبيرة جدًا؛ استخدم دقة أقل")
        matrix[0, 2] += (new_width - width) / 2
        matrix[1, 2] += (new_height - height) / 2
        image = cv2.warpAffine(image, matrix, (new_width, new_height), borderValue=(255, 255, 255))
    height, width = image.shape[:2]
    left, right = (round(width * value / 100) for value in horizontal)
    top, bottom = (round(height * value / 100) for value in vertical)
    if min(right - left, bottom - top) < 100:
        raise ValueError("منطقة القص صغيرة جدًا؛ أبقِ الورقة وعلاماتها واضحة")
    result = png_bytes(np.ascontiguousarray(image[top:bottom, left:right]))
    if len(result) > MAX_FILE_BYTES:
        raise ValueError("الصورة بعد القص أكبر من 10 MB؛ استخدم دقة أقل")
    return result
