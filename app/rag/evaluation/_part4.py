# Build workflow_evaluator.py part 4

path = r"C:/github/NSA_webservice/app/rag/evaluation/workflow_evaluator.py"

parts = [
    "",
    "# ---------------------------------------------------------------------------",
    "# Faithfulness",
    "# ---------------------------------------------------------------------------",
    "",
    "",
    'def score_faithfulness(answer: str, chunks: list, query: str = "") -> EvalScore:',
    "    if not chunks:",
    '        return EvalScore(name="faithfulness", score=0.0, explanation="No evidence retrieved.", detail={})',
    '    claims = ClaimExtractor().extract(answer or "")',
    "    if not claims:",
    '        return EvalScore(name="faithfulness", score=1.0, explanation="No claims to verify.", detail={"claims": 0})',
    "    verifier = EvidenceVerifier()",
    "    chunk_objs = []",
    "    for c in chunks:",
    '        chunk_objs.append(c if hasattr(c, "text") else type("Chunk", (), {"text": str(c)})())',
    "    verifications = verifier.verify_claims(claims, chunk_objs)",
    "    supported = sum(1 for v in verifications if v.confidence >= 0.5)",
    "    score = supported / len(claims)",
    '    return EvalScore(name="faithfulness", score=round(score, 4), explanation=f"{supported}/{len(claims)} claims grounded", detail={"claims": len(claims), "grounded": supported})',
]

with open(path, "a", encoding="utf-8") as f:
    f.write("\n".join(parts))
