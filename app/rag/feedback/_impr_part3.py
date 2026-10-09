# Build workflow_improvement_engine.py part 3

path = r"C:/github/NSA_webservice/app/rag/feedback/workflow_improvement_engine.py"

parts = [
    "",
    "class WorkflowImprovementEngine:",
    '    """Complete workflow RAG improvement loop orchestrator."""',
    "    ",
    "    def __init__(self):",
    "        self.recommendation_count = 0",
    "        self.training_entry_count = 0",
    "    ",
    "    def analyze_query(self, query: str, system_answer: str, user_answer: str, gold_answer: str | None = None) -> WorkflowImprovementResult:",
    '        """Run the full improvement analysis for one query."""',
    "        eval_result = evaluate_workflow_answer(query, system_answer, gold_answer)",
    "        comparison = compare_workflow_answers(query, system_answer, user_answer, gold_answer)",
    "        recommendations = generate_improvement_recommendations(comparison, eval_result)",
    "        training_entries = generate_training_entry(query, system_answer, user_answer, gold_answer, comparison, eval_result)",
    "        summary = build_improvement_summary(comparison, eval_result)",
    "        self.recommendation_count += len(recommendations)",
    "        self.training_entry_count += len(training_entries)",
    "        return WorkflowImprovementResult(",
    "            query=query, system_answer=system_answer, user_answer=user_answer, gold_answer=gold_answer,",
    "            evaluation=eval_result, comparison=comparison, recommendations=recommendations,",
    "            training_entries=training_entries, summary=summary,",
    "        )",
    "    ",
    "    def analyze_batch(self, results: list[dict[str, Any]]) -> list[WorkflowImprovementResult]:",
    '        """Analyze multiple queries in batch."""',
    '        return [self.analyze_query(r["query"], r["system_answer"], r["user_answer"], r.get("gold_answer")) for r in results]',
]

with open(path, "a", encoding="utf-8") as f:
    f.write("\n".join(parts))
