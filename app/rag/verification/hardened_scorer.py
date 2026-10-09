"""Shadow hardened verification (RAG-TR-001 section 5).

Pure functions: compute hardened groundedness/claim scores alongside the
live values. Never routes; callers log both.
"""

from __future__ import annotations

from typing import Any

from app.rag.generation.abstention import is_abstention
from app.rag.verification.claim_extractor import _AMOUNT_RE, _PERCENT_RE
from app.rag.verification.evidence_verifier import (
    _PERMISSION_RE,
    _PROHIBITION_RE,
)

__all__ = [
    "hardened_citation_ratio",
    "hardened_claim_ratio",
    "shadow_report",
    "verify_claim_strict",
]


def hardened_claim_ratio(verifications: list[Any], response_text: str) -> tuple[float, str | None]:
    """Empty claims on a non-empty answer -> 0.50 + flag (else live ratio)."""
    if not verifications:
        if response_text and response_text.strip():
            return 0.50, "empty_claims"
        return 1.0, None
    verified = sum(1 for v in verifications if getattr(v, "verified", False))
    return verified / len(verifications), None


def hardened_citation_ratio(citation_result: Any, response_text: str) -> tuple[float, str | None]:
    """No citations on a non-empty answer -> 0.50 + flag (else live score)."""
    if citation_result is None or not getattr(citation_result, "detail", None):
        if response_text and response_text.strip():
            return 0.50, "no_citations"
        return 1.0, None
    try:
        return float(citation_result.score), None
    except (TypeError, ValueError):
        return 0.50, "citation_score_unparseable"


def _claim_sections(claim: Any) -> list[str]:
    try:
        nums = claim.section_numbers
        if nums:
            return [str(n) for n in nums]
    except AttributeError:
        pass
    try:
        return [str(n) for n in (claim.entities.get("section", []))]
    except (AttributeError, TypeError):
        return []


def _claim_numerics(text: str) -> set[str]:
    out = {f"{m.group(1)}{m.group(2)}" for m in _AMOUNT_RE.finditer(text or "")}
    out |= {m.group(1) for m in _PERCENT_RE.finditer(text or "")}
    return out


def verify_claim_strict(claim: Any, chunks: list[Any]) -> tuple[bool, str]:
    """Section-exact + polarity + numeric gates; fuzz only as tiebreak."""
    sections = _claim_sections(claim)
    claim_text = str(getattr(claim, "text", "") or "")
    if sections:
        hit = False
        for c in chunks:
            sec = str(getattr(c, "section_number", "") or "")
            if sec and any(sec.strip() == s.strip() for s in sections):
                hit = True
                break
        if not hit:
            return False, "section_mismatch"
    claim_prohibits = bool(_PROHIBITION_RE.search(claim_text))
    claim_permits = bool(_PERMISSION_RE.search(claim_text))
    if claim_prohibits or claim_permits:
        support_ok = False
        for c in chunks:
            text = str(getattr(c, "text", "") or "")
            chunk_prohibits = bool(_PROHIBITION_RE.search(text))
            chunk_permits = bool(_PERMISSION_RE.search(text))
            if (claim_prohibits and chunk_prohibits) or (claim_permits and chunk_permits):
                support_ok = True
                break
        if not support_ok:
            return False, "polarity_mismatch"
    claim_nums = _claim_numerics(claim_text)
    if claim_nums:
        pool_nums: set[str] = set()
        for c in chunks:
            pool_nums |= _claim_numerics(str(getattr(c, "text", "") or ""))
        if not (claim_nums & pool_nums):
            return False, "numeric_mismatch"
    # Tiebreak: legacy fuzzy support.
    try:
        from rapidfuzz import fuzz as _fuzz

        best = 0
        for c in chunks:
            try:
                best = max(best, _fuzz.token_set_ratio(claim_text, str(getattr(c, "text", "") or "")))
            except Exception:
                continue
        if best >= 70:
            return True, "text"
    except Exception:
        pass
    if not sections and not claim_nums and not (claim_prohibits or claim_permits):
        return False, "no_support"
    return True, "gated"


def shadow_report(answer: str, chunks: list[Any], claim_verifications: list[Any], citation_result: Any) -> dict[str, Any]:
    """Compute the shadow score bundle for logging (never routes)."""
    claim_ratio, claim_flag = hardened_claim_ratio(claim_verifications, answer)
    cite_ratio, cite_flag = hardened_citation_ratio(citation_result, answer)
    score = round(min(1.0, max(0.0, 0.6 * claim_ratio + 0.4 * cite_ratio)), 4)
    flags = [f for f in (claim_flag, cite_flag) if f]
    abstained = is_abstention(answer)
    strict: list[dict[str, Any]] = []
    try:
        from app.rag.verification.claim_extractor import ClaimExtractor

        claims = ClaimExtractor().extract(answer or "")
        for cl in claims:
            ok, reason = verify_claim_strict(cl, chunks)
            strict.append({"text": cl.text[:160], "strict_verified": ok, "reason": reason})
    except Exception:
        strict = []
    return {
        "groundedness_shadow": score,
        "claim_groundedness_shadow": round(claim_ratio, 4),
        "citation_ratio_shadow": round(cite_ratio, 4),
        "shadow_flags": flags,
        "strict_claims": strict,
        "abstained": abstained,
    }
