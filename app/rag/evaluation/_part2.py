# Build workflow_evaluator.py part 2

path = r"C:/github/NSA_webservice/app/rag/evaluation/workflow_evaluator.py"

parts = [
    "",
    "@dataclass(frozen=True)",
    "class WorkflowAnswerEvaluation:",
    "    query: str",
    "    answer: str",
    "    gold_answer: str | None = None",
    "    retrieved_chunks: list = None",
    "    cited_chunk_ids: list | None = None",
    "    faithfulness: EvalScore = None",
    "    completeness: EvalScore = None",
    "    citation_quality: EvalScore = None",
    "    structure: EvalScore = None",
    "    overall: EvalScore = None",
    "    fa_form_coverage: float = 0.0",
    "    gold_form_coverage: float = 0.0",
    "    steps_count: int = 0",
    "",
    "    @property",
    "    def is_workflow(self) -> bool:",
    "        return self.faithfulness.score >= 0.0",
]

with open(path, "a", encoding="utf-8") as f:
    f.write("\n".join(parts))
