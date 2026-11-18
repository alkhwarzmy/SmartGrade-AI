"""Offline reference evaluation with writer/source split checks; never uploads or trains on input."""

import argparse
import json
from pathlib import Path
import re
import unicodedata
from modules.evaluation import ocr_metrics, essay_metrics


def normalize_arabic(text):
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[\u064b-\u065f\u0670\u0640]", "", text)
    text = re.sub("[إأآٱ]", "ا", text).replace("ى", "ي")
    return " ".join(text.split())


def evaluate(data):
    if data.get("authorized") is not True or data.get("source_kind") not in {"synthetic", "real_authorized"}:
        raise ValueError("يلزم إعلان نوع المصدر وإذن الاستخدام؛ هذا الإعلان لا يثبت الإذن قانونيًا")
    samples = data["samples"]
    if not samples:
        raise ValueError("لا توجد عينات")
    identifiers = set()
    groups = {}
    for sample in samples:
        if sample["id"] in identifiers or sample["split"] not in {"train", "validation", "test"}:
            raise ValueError("معرف مكرر أو تقسيم غير صالح")
        identifiers.add(sample["id"])
        keys = [("writer", sample["writer_group"])]
        if data.get("source_disjoint", False):
            keys.append(("source", sample["source_group"]))
        for key in keys:
            if key in groups and groups[key] != sample["split"]:
                raise ValueError("تسرب مجموعة بين التدريب والاختبار")
            groups[key] = sample["split"]
    selected = [s for s in samples if s["split"] == "test"]
    if not selected:
        raise ValueError("مجموعة اختبار مستقلة مطلوبة")
    report = {
        "source_kind": data["source_kind"],
        "authorization": "operator declaration, not independently verified",
        "test_samples": len(selected),
        "split_checks_passed": True,
        "metrics": {},
        "thresholds": data.get("preregistered_thresholds", {}),
        "accuracy_certified": False,
    }
    ocr = [s for s in selected if s["kind"] == "ocr"]
    if ocr:
        references, predictions = [s["reference"] for s in ocr], [s["prediction"] for s in ocr]
        report["metrics"]["ocr"] = {
            "raw": ocr_metrics(references, predictions),
            "normalized": ocr_metrics(
                list(map(normalize_arabic, references)), list(map(normalize_arabic, predictions))
            ),
        }
    essays = [s for s in selected if s["kind"] == "essay"]
    if essays:
        maximums = {s["maximum"] for s in essays}
        if len(maximums) != 1:
            raise ValueError("قيّم كل مقياس درجات في ملف مستقل")
        maximum = next(iter(maximums))
        report["metrics"]["essay"] = {
            "model_vs_rater1": essay_metrics(
                [s["reference"] for s in essays], [s["prediction"] for s in essays], maximum
            ),
            "human_baseline": essay_metrics(
                [s["reference"] for s in essays], [s["rater2"] for s in essays], maximum
            ),
        }
    mcq = [s for s in selected if s["kind"] == "mcq"]
    if mcq:
        accepted = [s for s in mcq if s["prediction"] is not None]
        pairs = []
        for s in accepted:
            if len(s["reference"]) != 20 or len(s["prediction"]) != 20:
                raise ValueError("القالب المعتمد 20 سؤالًا")
            pairs.extend(zip(s["reference"], s["prediction"]))
        correct = sum(a == b for a, b in pairs)
        report["metrics"]["mcq"] = {
            "papers": len(mcq),
            "accepted_papers": len(accepted),
            "rejected_papers": len(mcq) - len(accepted),
            "accepted_answer_accuracy": correct / len(pairs) if pairs else None,
            "wrong_accepted_answers": len(pairs) - correct,
        }
    if any(s["kind"] not in {"ocr", "essay", "mcq"} for s in selected):
        raise ValueError("نوع عينة غير معروف")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate(json.loads(args.input.read_text()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print("Reference evaluation complete. Aggregate metrics only; no source answers copied.")


if __name__ == "__main__":
    main()
