p = "tests/probe_classification.py"
s = open(p).read()
s = s.replace(
    "from app.rag.retrieval.identifier import detect_clause, identify_query",
    "from app.rag.retrieval.identifier import detect_clause, identifier_query",
)
s = s.replace(
    "identify_query={identify_query(q)}",
    "identifier_query={identifier_query(q)}",
)
open(p, "w").write(s)
print("patched OK")
