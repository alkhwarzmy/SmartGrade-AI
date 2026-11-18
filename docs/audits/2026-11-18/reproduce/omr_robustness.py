"""Controlled synthetic perturbations: diagnostic, not a real-exam accuracy estimate."""

from pathlib import Path
import sys

ROOT = next(
    p
    for p in Path(__file__).resolve().parents
    if (p / "api.py").is_file() and (p / "pyproject.toml").is_file()
)
sys.path.insert(0, str(ROOT))


def run():
    import json
    import cv2
    import numpy as np
    from modules.mcq_grader import template, read_answers

    out = ROOT / "work/audit-2026-11-18"
    out.mkdir(parents=True, exist_ok=True)
    reference = list("ABCD" * 5)
    base = template({q: [a] for q, a in enumerate(reference)}, True)
    variants = [("baseline", base)]
    for scale in [0.45, 0.6, 0.8, 1.25]:
        variants.append((f"resize_{scale}", cv2.resize(base, None, fx=scale, fy=scale)))
    for brightness in [0.45, 0.65, 0.85]:
        variants.append((f"brightness_{brightness}", (base * brightness).astype(np.uint8)))
    for sigma in [1, 2, 3, 5]:
        variants.append((f"gaussian_blur_sigma_{sigma}", cv2.GaussianBlur(base, (0, 0), sigma)))
    for quality in [15, 35, 60, 85]:
        ok, encoded = cv2.imencode(".jpg", base, [cv2.IMWRITE_JPEG_QUALITY, quality])
        assert ok
        variants.append((f"jpeg_quality_{quality}", cv2.imdecode(encoded, cv2.IMREAD_COLOR)))
    for angle in [-12, -7, -3, 3, 7, 12]:
        padded = cv2.copyMakeBorder(base, 180, 180, 180, 180, cv2.BORDER_CONSTANT, value=(255,) * 3)
        h, w = padded.shape[:2]
        rotated = cv2.warpAffine(
            padded, cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1), (w, h), borderValue=(255,) * 3
        )
        variants.append((f"rotation_{angle}", rotated))
    rng = np.random.default_rng(20261118)
    for noise in [10, 25, 45]:
        variants.append(
            (
                f"noise_std_{noise}",
                np.clip(base.astype(float) + rng.normal(0, noise, base.shape), 0, 255).astype(np.uint8),
            )
        )
    # Different supported/rejected geometries, preserving test intent and avoiding any real student data.
    variants.append(("upside_down_expected_rejection", cv2.rotate(base, cv2.ROTATE_180)))
    variants.append(("cropped_markers_expected_rejection", base[100:-100, 100:-100]))
    rows = []
    for name, image in variants:
        try:
            _, readings = read_answers(image)
            correct = sum(
                r["state"] == "single" and r["answer"] == reference[i] for i, r in enumerate(readings)
            )
            wrong_single = sum(
                r["state"] == "single" and r["answer"] != reference[i] for i, r in enumerate(readings)
            )
            rows.append(
                {
                    "case": name,
                    "accepted": True,
                    "correct_single": correct,
                    "wrong_single": wrong_single,
                    "flagged_for_review": sum(r["state"] != "single" for r in readings),
                }
            )
        except ValueError as exc:
            rows.append({"case": name, "accepted": False, "message": str(exc)})
    report = {
        "source": "CONTROLLED SYNTHETIC PERTURBATIONS; same template generator, not independent real data",
        "cases": len(rows),
        "accepted_cases": sum(r["accepted"] for r in rows),
        "rejected_cases": sum(not r["accepted"] for r in rows),
        "wrong_single_count": sum(r.get("wrong_single", 0) for r in rows),
        "results": rows,
    }
    (out / "omr-robustness.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run()
