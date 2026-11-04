from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable


_CITATION_RE = re.compile(r"\[(\d+)\]")


@dataclass
class AnswerContract:
    claim: str
    citations: list[str] = field(default_factory=list)
    verdict: str = ""
    raw: str = ""

    def is_valid(self) -> bool:
        return bool(self.claim.strip()) and bool(self.verdict.strip())


def parse_answer(text: str) -> AnswerContract:
    claim = ""
    citations: list[str] = []
    verdict = ""
    for line in text.splitlines():
        stripped = line.strip()
        lower = stripped.lower()
        if lower.startswith("claim:"):
            claim = stripped[len("claim:"):].strip()
        elif lower.startswith("citations:"):
            citations = [c.strip() for c in stripped[len("citations:"):].split(",") if c.strip()]
        elif lower.startswith("verdict:"):
            verdict = stripped[len("verdict:"):].strip()
    if not citations:
        citations = _CITATION_RE.findall(text)
    return AnswerContract(claim=claim, citations=citations, verdict=verdict, raw=text)


def extract_citations(text: str) -> list[str]:
    return _CITATION_RE.findall(text)


def verify_citations(answer: AnswerContract | str, prompt_window: Iterable[str]) -> dict:
    if isinstance(answer, str):
        answer = parse_answer(answer)
    window_ids = set()
    for chunk in prompt_window:
        window_ids.update(_CITATION_RE.findall(chunk))
    used = set(answer.citations) or set(extract_citations(answer.raw))
    missing = sorted(used - window_ids, key=lambda x: int(x) if x.isdigit() else x)
    return {
        "valid": not missing,
        "used": sorted(used, key=lambda x: int(x) if x.isdigit() else x),
        "window": sorted(window_ids, key=lambda x: int(x) if x.isdigit() else x),
        "missing": missing,
    }


def build_contract_prompt(question: str, context: str) -> str:
    return (
        "Answer using the contract: Claim: <one sentence>; "
        "Citations: <comma-separated citation ids>; Verdict: <short verdict>.\n"
        f"Question: {question}\nContext:\n{context}"
    )


__all__ = [
    "AnswerContract",
    "parse_answer",
    "extract_citations",
    "verify_citations",
    "build_contract_prompt",
]
