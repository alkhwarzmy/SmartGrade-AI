"""Metrics operate only on supplied reference labels; no quality claims without references."""

import numpy as np


def edit_distance(a, b):
    previous = list(range(len(b) + 1))
    for i, left in enumerate(a, 1):
        current = [i]
        for j, right in enumerate(b, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (left != right)))
        previous = current
    return previous[-1]


def ocr_metrics(references, predictions):
    if not references or len(references) != len(predictions):
        raise ValueError("يلزم نص مرجعي وتنبؤ لكل عينة")
    chars, words = sum(map(len, references)), sum(len(t.split()) for t in references)
    if not chars or not words:
        raise ValueError("النص المرجعي فارغ")
    return {
        "n": len(references),
        "CER": sum(edit_distance(a, b) for a, b in zip(references, predictions)) / chars,
        "WER": sum(edit_distance(a.split(), b.split()) for a, b in zip(references, predictions)) / words,
    }


def essay_metrics(references, predictions, maximum: int):
    if not references or len(references) != len(predictions) or maximum < 1:
        raise ValueError("درجات مرجعية ومقترحة وحد أعلى مطلوبة")
    a, b = np.array(references, dtype=float), np.array(predictions, dtype=float)
    if (
        not (np.isfinite(a).all() and np.isfinite(b).all())
        or min(a.min(), b.min()) < 0
        or max(a.max(), b.max()) > maximum
    ):
        raise ValueError("درجات خارج الحدود")
    diff = np.abs(a - b)
    qwk = None
    # QWK is reported only for integer score categories; no silent rounding of fractional grades.
    if np.all(a == np.floor(a)) and np.all(b == np.floor(b)):
        counts = np.zeros((maximum + 1, maximum + 1))
        for x, y in zip(a.astype(int), b.astype(int)):
            counts[x, y] += 1
        weights = (np.arange(maximum + 1)[:, None] - np.arange(maximum + 1)[None, :]) ** 2 / maximum**2
        expected = np.outer(counts.sum(1), counts.sum(0)) / len(a)
        denominator = np.sum(weights * expected)
        if denominator:
            qwk = float(1 - np.sum(weights * counts) / denominator)
    return {
        "n": len(a),
        "MAE": float(diff.mean()),
        "QWK": qwk,
        "exact_agreement": float(np.mean(diff == 0)),
        "within_one_point": float(np.mean(diff <= 1)),
        "severe_disagreement_rate": float(np.mean(diff > max(1, 0.2 * maximum))),
        "severe_disagreement_threshold": max(1, 0.2 * maximum),
    }
