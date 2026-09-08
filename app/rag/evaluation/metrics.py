class SeparateConfidenceMetrics:
    """Tracks separate confidence metrics: R (retrieval), E (evidence coverage), C (citation correctness),
    G (claim groundedness), and A (answer completeness)."""

    def __init__(self) -> None:
        self.R = 0.0  # retrieval confidence
        self.E = 0.0  # evidence coverage
        self.C = 0.0  # citation correctness
        self.G = 0.0  # claim groundedness
        self.A = 0.0  # answer completeness

    def update(
        self,
        retrieval_confidence: float,
        evidence_coverage: float,
        citation_correctness: bool,
        claim_groundedness: float,
        answer_completeness: bool,
    ) -> EvalScore:
        """Update metrics and return a summary score."""
        self.R = retrieval_confidence
        self.E = evidence_coverage
        self.C = 1.0 if citation_correctness else 0.0
        self.G = claim_groundedness
        self.A = 1.0 if answer_completeness else 0.0

        # Weighted average (equal weights for simplicity)
        final_score = (self.R + self.E + self.C + self.G + self.A) / 5.0

        # Detailed explanation
        explanation = f"R={self.R:.2f}, E={self.E:.2f}, C={self.C:.2f}, G={self.G:.2f}, A={self.A:.2f}"

        return EvalScore(
            name="separate_confidence_metrics",
            score=final_score,
            explanation=explanation,
            detail={
                "R": self.R,
                "E": self.E,
                "C": self.C,
                "G": self.G,
                "A": self.A,
            },
        )
