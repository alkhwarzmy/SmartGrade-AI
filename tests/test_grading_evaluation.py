"""Distinguish contract validity from incorrect scoring and from tests that never called a model."""
import json
from pathlib import Path
import pytest
from modules.schemas import ExamSpec
from modules.grading_evaluation import reference_proposal, compare_proposal
from scripts.diagnose_grading import diagnose, normalized

FIXTURE = Path(__file__).resolve().parents[1] / "assets/evaluation/arabic_essay.json"


def test_valid_json_can_still_have_bad_marks():
    fixture = json.loads(FIXTURE.read_text())
    spec = ExamSpec.model_validate(fixture["exam"])
    partial = next(c for c in fixture["cases"] if c["id"] == "student-c")
    grade = reference_proposal(spec, partial)
    for question in grade["questions"]:
        for criterion in question["criteria"]:
            criterion["score"] = 2
            criterion["evidence"] = partial["answers"][question["question_id"]]
    compared = compare_proposal(spec, partial, grade)
    assert compared["reference_total"] == 17 and compared["proposed_total"] == 30
    assert compared["absolute_total_error"] == 13
    assert compared["question_mae"] == pytest.approx(2.6)


def test_offline_diagnosis_never_reports_fake_accuracy_or_calls_a_model(monkeypatch):
    fixture = json.loads(FIXTURE.read_text())
    def forbidden(*args, **kwargs):
        pytest.fail("Offline diagnosis must not call a provider")
    monkeypatch.setattr("scripts.diagnose_grading.propose_grade", forbidden)
    report = diagnose(fixture)
    assert len(report["reference_contracts"]) == 6
    assert report["model_evaluated"] is False and report["external_calls"] == 0
    assert report["summary"]["mean_absolute_total_error"] is None
    assert sum(c["status"] == "not_run" for c in report["cases"]) == 5


def test_arabic_normalization_does_not_remove_negation():
    assert normalized("لا يحتاج الماء") != normalized("يحتاج الماء")
    assert normalized("أَكْسِجين") == normalized("اكسجين")
