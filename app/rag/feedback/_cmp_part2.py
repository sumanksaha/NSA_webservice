# Build workflow_comparator.py part 2

path = r"C:/github/NSA_webservice/app/rag/feedback/workflow_comparator.py"

parts = [
    "",
    "@dataclass(frozen=True)",
    "class WorkflowAnswerComparison:",
    '    """Full comparison of user vs system workflow answer."""',
    "    query: str",
    "    system_answer: str",
    "    user_answer: str",
    "    gold_answer: str | None = None",
    "    missing_forms: list[str] = None",
    "    extra_forms: list[str] = None",
    "    missing_steps: list[str] = None",
    "    extra_steps: list[str] = None",
    "    citation_issues: list[str] = None",
    "    factual_discrepancies: list[str] = None",
    '    overall_rating: str = "neutral"',
    "",
    "    @property",
    "    def has_discrepancies(self) -> bool:",
    "        return any([",
    "            self.missing_forms or [],",
    "            self.extra_forms or [],",
    "            self.missing_steps or [],",
    "            self.extra_steps or [],",
    "            self.citation_issues or [],",
    "            self.factual_discrepancies or [],",
    "        ])",
    "",
    "    def to_dict(self) -> dict[str, Any]:",
    "        return {",
    '            "query": self.query,',
    '            "missing_forms": self.missing_forms or [],',
    '            "extra_forms": self.extra_forms or [],',
    '            "missing_steps": self.missing_steps or [],',
    '            "extra_steps": self.extra_steps or [],',
    '            "citation_issues": self.citation_issues or [],',
    '            "factual_discrepancies": self.factual_discrepancies or [],',
    '            "overall_rating": self.overall_rating,',
    '            "has_discrepancies": self.has_discrepancies,',
    "        }",
]

with open(path, "a", encoding="utf-8") as f:
    f.write("\n".join(parts))
