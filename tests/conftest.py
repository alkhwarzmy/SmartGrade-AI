import pytest
from modules.schemas import ExamSpec, Criterion


@pytest.fixture
def essay_spec():
    return ExamSpec(
        title="الماء",
        kind="essay",
        question="لماذا الماء مهم؟",
        model_answer="الماء ضروري للحياة",
        maximum=4,
        criteria=[Criterion(id="c1", description="ذكر أهمية الماء", maximum=4)],
    )


@pytest.fixture
def valid_proposal():
    return {
        "criteria": [
            {
                "criterion_id": "c1",
                "score": 3,
                "evidence": "الماء",
                "rationale": "ذكر الأهمية",
                "missing": "مثال توضيحي",
            }
        ],
        "needs_review": True,
    }
