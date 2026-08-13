"""Compact question builder used by the content modules.

Expands a compact tuple into the full frozen JSONL schema (§20 of the spec).
"""


def Q(
    qid,
    q,
    domains,
    qtype,
    diff,
    jur,
    prim=None,
    supp=None,
    alt=None,
    concepts=None,
    auth=None,
    cond=None,
    exc=None,
    temp=None,
    chunks=None,
    concl="",
    insuf=False,
    traps=None,
    conf="HIGH",
):
    """Return a full-schema question dict.

    ``domains`` uses the canonical KG domain vocabulary
    (FOOD_SAFETY, ANIMAL_SLAUGHTER, ENVIRONMENT_POLLUTION, MUNICIPAL,
    PUBLIC_HEALTH, LAND_PREMISES, BUSINESS_CIVIL, CRIMINAL).
    """
    return {
        "question_id": qid,
        "version": "1.0",
        "question": q,
        "jurisdiction": jur,
        "domains": list(domains),
        "question_type": list(qtype),
        "difficulty": diff,
        "primary_provisions": list(prim or []),
        "supporting_provisions": list(supp or []),
        "acceptable_alternatives": list(alt or []),
        "gold_concepts": list(concepts or []),
        "gold_authorities": list(auth or []),
        "required_conditions": list(cond or []),
        "exceptions": list(exc or []),
        "temporal_constraints": list(temp or []),
        "gold_source_chunks": list(chunks or []),
        "acceptable_conclusion": concl,
        "insufficient_evidence": bool(insuf),
        "common_traps": list(traps or []),
        "gold_confidence": conf,
    }


def P(pid, act, section, title, domain, doc_id, collection, chunk_id=None):
    """Return a gold provision record (compact)."""
    return {
        "id": pid,
        "act": act,
        "section": section,
        "title": title,
        "domain": domain,
        "chunk_id": chunk_id,
        "document_id": doc_id,
        "collection": collection,
    }
