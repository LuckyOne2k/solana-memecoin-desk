from dataclasses import dataclass


@dataclass
class Objection:
    objection: str
    evidence: str


@dataclass
class Rebuttal:
    objection: str
    response: str
    evidence: str


def evaluate_debate(
    objections: list[Objection],
    rebuttals: list[Rebuttal]
) -> tuple[bool, list[str]]:
    problems = []

    if len(objections) < 3:
        problems.append("At least 3 objections are required.")

    for index, objection in enumerate(objections, start=1):
        if not objection.objection.strip():
            problems.append(f"Objection {index} is empty.")
        if not objection.evidence.strip():
            problems.append(f"Objection {index} has no evidence.")

        matching = [
            r for r in rebuttals
            if r.objection == objection.objection
        ]

        if not matching:
            problems.append(f"Objection {index} has no rebuttal.")
            continue

        if not any(r.response.strip() and r.evidence.strip() for r in matching):
            problems.append(f"Objection {index} has no evidence-based rebuttal.")

    return not problems, problems
