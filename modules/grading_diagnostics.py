"""Read-only diagnosis with counts and safe codes, excluding answers, prompts and credentials."""
from modules.schemas import answer_texts, parse_essay_grade
from modules.essay_grader import provider_blockers
from modules.processing import message_for, safe_code


def essay_diagnostics(spec, row, *, organization_allowed=True, visible_text=None,
                      unsaved_answers=False, ocr=None):
    if spec.kind != "essay":
        raise ValueError("الفحص مخصص للاختبارات المقالية")
    questions = spec.essay_questions()
    answers = answer_texts(spec, row.text, row.current)
    mapped = len(questions) == 1 or isinstance(row.current.get("essay_answers"), dict)
    reasons = []
    if visible_text is not None and visible_text != row.text:
        reasons.append("source_unsaved")
    if not mapped or unsaved_answers:
        reasons.append("answers_unsaved")
    if not any(text.strip() for text in answers.values()):
        reasons.append("answers_empty")
    input_reasons = list(reasons)
    if not organization_allowed:
        reasons.append("organization_ai_disabled")
    reasons.extend(provider_blockers())
    latest = {}
    for stage in ["ocr", "llm"]:
        attempt = row.original.get("processing_status", {}).get(stage)
        if attempt:
            code = attempt.get("code")
            latest[stage] = {
                "state": attempt.get("state"), "code": code if code == "ok" else safe_code(code),
                "message": "اكتملت المعالجة" if code == "ok" else message_for(code),
                "input_changed_since_attempt": row.revision != attempt.get("input_revision", -2) + 1,
            }
    proposal_total = None
    if row.proposal:
        try:
            _, proposal_total = parse_essay_grade(row.proposal, spec, row.text, row.current)
        except ValueError:
            proposal_total = None
    return {
        "version": 1, "revision": row.revision, "question_count": len(questions), "maximum": spec.maximum,
        "source_characters": len(row.text), "answers_saved": mapped,
        "questions": [{
            "number": number, "maximum": question.maximum,
            "answer_characters": len(answers[question.id]),
            "state": "unassigned" if not mapped else "answered" if answers[question.id].strip() else "blank",
        } for number, question in enumerate(questions, 1)],
        "ready_for_external_proposal": not reasons,
        "ready_for_manual_review": not any(c in input_reasons for c in ["source_unsaved", "answers_unsaved"]),
        "blockers": [{"code": code, "message": message_for(code)} for code in dict.fromkeys(reasons)],
        "ocr": {"state": "unchecked" if ocr is None else "available" if ocr[0] else "unavailable",
                "message": "الفحص اختياري؛ يمكن إدخال الإجابات يدويًا" if ocr is None else ocr[1]},
        "grade": {
            "source": "teacher_approved" if row.approved else "model_proposal" if row.proposal else "none",
            "approved_total": row.total if row.approved else None, "proposal_total": proposal_total,
        },
        "last_attempts": latest, "external_call_performed_by_this_check": False,
    }
