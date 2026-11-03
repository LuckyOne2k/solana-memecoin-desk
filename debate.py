from dataclasses import dataclass

@dataclass
class Objection:
    claim: str
    evidence_url: str
    evidence_detail: str

@dataclass
class Rebuttal:
    objection_claim: str
    refutation: str
    evidence_url: str
    evidence_detail: str

def debate_gate(objections: list[Objection], rebuttals: list[Rebuttal], required: int = 3) -> dict:
    # This is a structural gate only. It does not verify truth or source quality.
    if len(objections) < required:
        return {"approved": False, "reason": "bear_objections_insufficient", "unresolved": []}
    unresolved = []
    for o in objections:
        matches = [r for r in rebuttals if r.objection_claim == o.claim and r.refutation.strip()
                   and r.evidence_url.strip() and r.evidence_detail.strip()]
        if not matches:
            unresolved.append(o.claim)
    return {"approved": not unresolved, "reason": "debate_complete" if not unresolved else "unresolved_objections",
            "unresolved": unresolved}
