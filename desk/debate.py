"""Formale Prüfung einer Bear/Analyst-Debatte.

Status: noch nicht angeschlossen. Prüft nur die Form (Anzahl Einwände,
jeweils Beleg und belegte Antwort), nicht ob die Belege stimmen.
Ein unbeantworteter Einwand ist immer ein Veto (config.yaml:
debate.unresolved_objection_veto muss true sein).

Hinweis: Wird die Debatte später von einer KI geführt, kann diese
"Belege" erfinden. Die Debatte darf deshalb nur bremsen, nie einen
Einstieg freigeben.
"""

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
    rebuttals: list[Rebuttal],
    *,
    min_objections: int,
) -> tuple[bool, list[str]]:
    if (
        isinstance(min_objections, bool)
        or not isinstance(min_objections, int)
        or min_objections < 0
    ):
        raise ValueError("min_objections must be a non-negative integer")

    problems = []

    if len(objections) < min_objections:
        problems.append(
            f"At least {min_objections} objections are required."
        )

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


def evaluate_debate_with_config(objections, rebuttals, config):
    return evaluate_debate(
        objections,
        rebuttals,
        min_objections=config["debate"]["bear_objections_required"],
    )
