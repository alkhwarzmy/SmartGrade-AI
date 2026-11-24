"""Embed only synthetic OMR and printed Arabic fixtures into native integration tests."""
import base64
import json
from pathlib import Path
import cv2
from modules.mcq_grader import template, bubble_center, geometry

ROOT = Path(__file__).resolve().parents[1]


def main():
    values = {}
    for count in [1, 7, 20, 49, 50]:
        marks = {i: ["ABCD"[i % 4]] for i in range(count)}
        image = template(marks, synthetic=True, question_count=count)
        success, encoded = cv2.imencode(".png", image)
        assert success
        values[f"mcq{count}"] = base64.b64encode(encoded).decode()
    image = template({i: ["A"] for i in range(50)}, synthetic=True, question_count=50)
    _, _, radius = geometry(50)
    for q in [0, 1, 2]:
        for c in range(4):
            cv2.circle(image, bubble_center(q, c, 50), radius - 3, (255, 255, 255), -1)
    for c in [0, 1]:
        cv2.circle(image, bubble_center(1, c, 50), radius - 3, (0, 0, 0), -1)
    cv2.circle(image, bubble_center(2, 0, 50), radius - 3, (175, 175, 175), -1)
    success, encoded = cv2.imencode(".png", image)
    assert success
    values["uncertain50"] = base64.b64encode(encoded).decode()
    values["arabic"] = base64.b64encode((ROOT / "assets/evaluation/student-c.png").read_bytes()).decode()
    target = ROOT / "flutter_app/integration_test/fixtures.dart"
    target.write_text("// Synthetic fixtures only; generated in native CI.\nconst offlineFixtures = <String,String>"
                      + json.dumps(values, ensure_ascii=True) + ";\n")
    print("Prepared native synthetic fixtures:", list(values))


if __name__ == "__main__":
    main()
