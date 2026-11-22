"""Compare supplied proposals to explicit reference labels, never infer model quality from schema checks."""
from modules.schemas import parse_essay_grade


def reference_proposal(spec, case):
    return {"needs_review": True, "questions": [{
        "question_id": q.id, "criteria": [{
            "criterion_id": c.id, "score": score,
            "evidence": case["answers"][q.id] if score else "",
            "rationale": "درجة مرجعية لهذه العينة الاصطناعية", "missing": "",
        } for c, score in zip(q.criteria, case["scores"][i], strict=True)],
    } for i, q in enumerate(spec.questions)]}


def compare_proposal(spec, case, value):
    proposed, proposed_total = parse_essay_grade(value, spec, "", {"essay_answers": case["answers"]})
    by_question = {q.question_id: {c.criterion_id: c.score for c in q.criteria} for q in proposed.questions}
    rows, questions = [], []
    for index, question in enumerate(spec.questions):
        predicted = by_question[question.id]
        expected_scores = case["scores"][index]
        total = sum(predicted.values())
        expected_total = sum(expected_scores)
        questions.append({"number": index + 1, "reference": expected_total, "proposed": total,
                          "absolute_error": abs(total - expected_total), "maximum": question.maximum})
        for number, (criterion, expected) in enumerate(zip(question.criteria, expected_scores, strict=True), 1):
            actual = predicted[criterion.id]
            rows.append({"question": index + 1, "criterion": number, "reference": expected,
                         "proposed": actual, "difference": actual - expected, "maximum": criterion.maximum})
    return {"reference_total": case["expected_total"], "proposed_total": proposed_total,
            "absolute_total_error": abs(proposed_total - case["expected_total"]),
            "question_mae": sum(q["absolute_error"] for q in questions) / len(questions),
            "criterion_mae": sum(abs(r["difference"]) for r in rows) / len(rows),
            "questions": questions, "criteria": rows}
