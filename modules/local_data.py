"""Read authorized local reference samples without uploading them anywhere."""

import json
from pathlib import Path
from pydantic import Field
from modules.schemas import StrictModel, ExamSpec
from modules.preprocessing import decode_image


class LocalSample(StrictModel):
    sample_id: str = Field(min_length=1, max_length=100)
    image: str
    reference_text: str = Field(min_length=1, max_length=30000)
    exam: ExamSpec
    human_scores: list[float] = Field(default_factory=list)
    split: str = Field(pattern="^(train|validation|test)$", default="test")
    synthetic: bool = False


def import_manifest(manifest: Path):
    manifest = manifest.resolve()
    if manifest.stat().st_size > 5 * 1024 * 1024:
        raise ValueError("ملف العينات يتجاوز 5 MB")
    entries = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(entries, list) or not entries:
        raise ValueError("يجب أن يحتوي الملف على قائمة عينات")
    seen, image_splits, samples = set(), {}, []
    for entry in entries:
        sample = LocalSample.model_validate(entry)
        if sample.sample_id in seen:
            raise ValueError("معرف عينة مكرر")
        seen.add(sample.sample_id)
        path = (manifest.parent / sample.image).resolve()
        if not path.is_relative_to(manifest.parent) or path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
            raise ValueError("الصورة يجب أن تكون داخل مجلد ملف العينات")
        import hashlib

        raw = path.read_bytes()
        decode_image(raw)
        digest = hashlib.sha256(raw).hexdigest()
        if digest in image_splits and image_splits[digest] != sample.split:
            raise ValueError("الصورة نفسها موجودة في أكثر من تقسيم؛ تسرب بيانات")
        image_splits[digest] = sample.split
        if any(not 0 <= score <= sample.exam.maximum for score in sample.human_scores):
            raise ValueError("درجة بشرية خارج حدود السؤال")
        samples.append(sample)
    return samples
