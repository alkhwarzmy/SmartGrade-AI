"""Run from project root: python -m scripts.generate_omr --output outputs/demo"""

import argparse
import json
from pathlib import Path
from modules.mcq_grader import template, template_pdf
from modules.preprocessing import png_bytes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("outputs/demo"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    key = list("ABCD" * 5)
    (args.output / "omr-v1.pdf").write_bytes(template_pdf())
    (args.output / "omr-v1.png").write_bytes(png_bytes(template()))
    (args.output / "synthetic-complete.png").write_bytes(
        png_bytes(template({i: [x] for i, x in enumerate(key)}, True))
    )
    marks = {i: [x] for i, x in enumerate(key) if i != 2}
    marks[1] = ["A", "B"]
    (args.output / "synthetic-review.png").write_bytes(png_bytes(template(marks, True)))
    (args.output / "answer-key.json").write_text(json.dumps({"synthetic": True, "answer_key": key}, indent=2))
    print(f"Created OMR v1 and explicitly synthetic samples in {args.output}")


if __name__ == "__main__":
    main()
