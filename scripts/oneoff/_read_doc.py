with open("docs/RAG_EVALUATION_ENLARGEMENT.md", encoding="utf-8") as f:
    content = f.read()
with open("out_rag_enlargement.md", "w", encoding="utf-8") as out:
    out.write(content)
print("wrote", len(content), "chars")
