"""Offline by default: validate reference cases and optionally measure printed OCR or supplied model outputs."""
import argparse
import json
from pathlib import Path
import re
import unicodedata
from config import ROOT
from modules.essay_grader import propose_grade, provider_blockers, ProviderError
from modules.schemas import ExamSpec, parse_essay_grade
from modules.grading_evaluation import reference_proposal, compare_proposal
from modules.handwriting_ocr import recognize, OCRUnavailable
from modules.evaluation import ocr_metrics


def normalized(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    text = "".join(c for c in text if unicodedata.category(c) != "Mn" and c != "ـ")
    for character in "أإآٱ":
        text = text.replace(character, "ا")
    for index, character in enumerate("٠١٢٣٤٥٦٧٨٩"):
        text = text.replace(character, str(index))
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


def printed_reference(fixture, case):
    lines = ["اختبار العلوم المقالي", "المرحلة المتوسطة | خمسة أسئلة | الدرجة الكاملة: 30",
             "رمز الورقة الاصطناعية: SYNTHETIC-C", "أجب إجابة علمية واضحة عن كل سؤال."]
    for number, question in enumerate(fixture["exam"]["questions"], 1):
        lines.extend([f"السؤال {number}: " + question["question"], case["answers"][question["id"]]])
    return "\n".join(lines)


def diagnose(fixture, *, predictions=None, live=False, consent=False, ocr_sample=False):
    spec = ExamSpec.model_validate(fixture["exam"])
    cases = fixture["cases"]
    reference_checks, results = [], []
    predictions = predictions or {}
    calls = 0
    for case in cases:
        reference = reference_proposal(spec, case)
        _, total = parse_essay_grade(reference, spec, "", {"essay_answers": case["answers"]})
        if total != case["expected_total"]:
            raise ValueError("الدرجة المرجعية لا تطابق معايير العينة")
        reference_checks.append({"case": case["id"], "valid": True, "reference_total": total})
        if not any(a.strip() for a in case["answers"].values()):
            results.append({"case": case["id"], "status": "skipped_empty_answers"})
            continue
        value = predictions.get(case["id"])
        if live:
            if not consent:
                raise ValueError("التشغيل الخارجي يحتاج --consent-to-external-processing-and-cost")
            try:
                calls += 1
                value = propose_grade(spec, "", current={"essay_answers": case["answers"]}, consent=True).model_dump()
            except ProviderError as exc:
                calls -= int(not exc.attempted)
                results.append({"case": case["id"], "status": "failed", "code": exc.code})
                continue
        if value is None:
            results.append({"case": case["id"], "status": "not_run"})
            continue
        if isinstance(value, dict) and "proposal" in value:
            value = value["proposal"]
        try:
            results.append({"case": case["id"], "status": "evaluated", **compare_proposal(spec, case, value)})
        except ValueError:
            results.append({"case": case["id"], "status": "invalid_proposal"})
    evaluated = [r for r in results if r["status"] == "evaluated"]
    report = {
        "fixture": "synthetic_arabic_science_5q", "maximum": spec.maximum,
        "reference_labels_independently_validated": False,
        "reference_contracts": reference_checks,
        "evaluation_mode": "live_opt_in" if live else "supplied_outputs_unverified" if predictions else "offline",
        "model_evaluated": bool(evaluated), "external_calls": calls,
        "provider_blockers": provider_blockers(), "cases": results,
        "summary": {"evaluated_cases": len(evaluated),
                    "mean_absolute_total_error": sum(r["absolute_total_error"] for r in evaluated) / len(evaluated)
                        if evaluated else None,
                    "mean_question_error": sum(r["question_mae"] for r in evaluated) / len(evaluated)
                        if evaluated else None},
        "ocr": {"tested": False},
    }
    if ocr_sample:
        case = next(c for c in cases if c["id"] == "student-c")
        reference = printed_reference(fixture, case)
        try:
            text = recognize((ROOT / "assets/evaluation/student-c.png").read_bytes())
            report["ocr"] = {"tested": True, "sample": "synthetic_printed_student_c",
                             "raw": ocr_metrics([reference], [text]),
                             "normalized": ocr_metrics([normalized(reference)], [normalized(text)]),
                             "reference_characters": len(reference), "recognized_characters": len(text),
                             "handwriting_evaluated": False}
        except OCRUnavailable as exc:
            report["ocr"] = {"tested": False, "code": exc.code, "handwriting_evaluated": False}
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("outputs/grading-diagnostic.json"))
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--ocr-sample", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--consent-to-external-processing-and-cost", action="store_true")
    args = parser.parse_args()
    if args.live and not args.consent_to_external_processing_and_cost:
        parser.error("التشغيل الخارجي يحتاج موافقة صريحة؛ الوضع الافتراضي محلي دون تكلفة API")
    fixture = json.loads((ROOT / "assets/evaluation/arabic_essay.json").read_text())
    predictions = json.loads(args.predictions.read_text()) if args.predictions else None
    report = diagnose(fixture, predictions=predictions, live=args.live,
                      consent=args.consent_to_external_processing_and_cost, ocr_sample=args.ocr_sample)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"report": str(args.output), "reference_cases": len(report["reference_contracts"]),
                      "model_evaluated": report["model_evaluated"], "external_calls": report["external_calls"],
                      "provider_blockers": report["provider_blockers"], "ocr": report["ocr"]}, ensure_ascii=False))
    if args.live and any(c["status"] in {"failed", "invalid_proposal"} for c in report["cases"]):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
