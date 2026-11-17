"""Reference-file metrics or a deterministic SYNTHETIC MCQ benchmark; never fabricated real accuracy."""

import argparse
import json
from pathlib import Path
from time import perf_counter
import cv2
from modules.evaluation import ocr_metrics, essay_metrics
from modules.mcq_grader import template, read_answers, bubble_center


def synthetic_mcq():
    questions, correct, review, elapsed = 0, 0, 0, 0
    per_state = {s: [0, 0] for s in ["single", "blank", "multiple", "unclear"]}
    for variant in range(6):
        marks = {q: ["ABCD"[(q + variant) % 4]] for q in range(20) if q % 7 != 0}
        marks[2] = ["B", "C"]
        image = template(marks, True)
        cv2.circle(image, bubble_center(3, 1), 19, (175,) * 3, -1)
        marks[3] = ["B"]  # overwrite the underlying fill for the uncertain case
        x, y = bubble_center(3, (3 + variant) % 4)
        if (3 + variant) % 4 != 1:
            cv2.circle(image, (x, y), 19, (255,) * 3, -1)
        started = perf_counter()
        _, rows = read_answers(image)
        elapsed += perf_counter() - started
        for q, row in enumerate(rows):
            expected = "unclear" if q == 3 else "multiple" if q == 2 else "blank" if q % 7 == 0 else "single"
            answer = marks[q][0] if expected == "single" else None
            ok = row["state"] == expected and row["answer"] == answer
            questions += 1
            correct += ok
            review += row["state"] != "single"
            per_state[expected][0] += ok
            per_state[expected][1] += 1
    return {
        "source": "SYNTHETIC ONLY; not a real-exam accuracy estimate",
        "sheets": 6,
        "questions": questions,
        "question_level_accuracy": correct / questions,
        "blank_detection_accuracy": per_state["blank"][0] / per_state["blank"][1],
        "multiple_mark_detection_accuracy": per_state["multiple"][0] / per_state["multiple"][1],
        "review_rate": review / questions,
        "processing_seconds": elapsed,
        "state_correct_and_total": per_state,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["mcq-synthetic", "ocr", "essay"])
    parser.add_argument(
        "--input", type=Path, help="JSON containing references and predictions; essay also maximum"
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.mode == "mcq-synthetic":
        result = synthetic_mcq()
    else:
        if not args.input:
            parser.error("--input is required for reference-based evaluation")
        data = json.loads(args.input.read_text(encoding="utf-8"))
        result = (
            ocr_metrics(data["references"], data["predictions"])
            if args.mode == "ocr"
            else essay_metrics(data["references"], data["predictions"], data["maximum"])
        )
    report = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
