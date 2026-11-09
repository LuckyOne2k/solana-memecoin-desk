import pytest

from desk.debate import (
    Objection,
    Rebuttal,
    evaluate_debate,
    evaluate_debate_with_config,
)


def make_objections(count):
    return [
        Objection(objection=f"Einwand {i}", evidence=f"Beleg {i}")
        for i in range(count)
    ]


def make_rebuttals(objections):
    return [
        Rebuttal(
            objection=o.objection,
            response="Antwort",
            evidence="Gegenbeleg",
        )
        for o in objections
    ]


def test_complete_debate_passes_with_config(desk_config):
    objections = make_objections(3)

    passed, problems = evaluate_debate_with_config(
        objections,
        make_rebuttals(objections),
        desk_config,
    )

    assert passed is True
    assert problems == []


def test_too_few_objections_fail_with_config(desk_config):
    objections = make_objections(2)

    passed, problems = evaluate_debate_with_config(
        objections,
        make_rebuttals(objections),
        desk_config,
    )

    assert passed is False
    assert any("At least 3" in p for p in problems)


def test_unanswered_objection_is_veto():
    objections = make_objections(3)
    rebuttals = make_rebuttals(objections)[:2]

    passed, problems = evaluate_debate(
        objections,
        rebuttals,
        min_objections=3,
    )

    assert passed is False
    assert "Objection 3 has no rebuttal." in problems


def test_rebuttal_without_evidence_is_veto():
    objections = make_objections(3)
    rebuttals = make_rebuttals(objections)
    rebuttals[0].evidence = " "

    passed, problems = evaluate_debate(
        objections,
        rebuttals,
        min_objections=3,
    )

    assert passed is False
    assert "Objection 1 has no evidence-based rebuttal." in problems


@pytest.mark.parametrize("value", [None, -1, 2.5, True])
def test_invalid_minimum_is_rejected(value):
    with pytest.raises(ValueError):
        evaluate_debate([], [], min_objections=value)
