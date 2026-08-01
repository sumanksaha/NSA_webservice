"""Unit tests for the calibrated confidence scoring contract (T-29)."""

import unittest

from legal_paragraph_detection_engine import LegalParagraphEngine, ProcessingConfig

SAMPLE_TEXT = """\
    Section 3

    3(1) In addition to the provisions of this Act, the following shall apply:

    3(1)(a) For the purposes of this section, "concerned food" shall mean any food
    3(1)(b) Any person who violates this provision shall be liable for penalties

    Explanation:

    The above provisions are meant to ensure compliance with food safety standards.

    Note: This is a sample legal document for demonstration purposes.

    Schedule I

    Table 1: Classification of Food Items
"""


class TestConfidenceCalibration(unittest.TestCase):
    """Contract tests for the T-29 calibrated confidence scoring."""

    def setUp(self) -> None:
        self.engine = LegalParagraphEngine()
        self.result = self.engine.process_document(SAMPLE_TEXT)

    def _paragraph_by_type(self, para_type: str) -> list[dict]:
        return [p for p in self.result if p["paragraph_type"] == para_type]

    def test_all_scores_within_unit_interval(self) -> None:
        """Every confidence component must stay within [0, 1]."""
        for para in self.result:
            scores = para["confidence_scores"]
            for key in ("structure_detection", "content_quality", "citation_presence", "overall"):
                with self.subTest(para=para["paragraph_id"], key=key):
                    self.assertGreaterEqual(scores[key], 0.0)
                    self.assertLessEqual(scores[key], 1.0)

    def test_structural_paragraphs_never_score_zero_structure(self) -> None:
        """F-13: top-level sections must not score 0.0 on structure detection."""
        structural_types = {
            "section",
            "subsection",
            "clause",
            "subclause",
            "explanation",
            "note",
            "schedule",
            "table",
        }
        for para_type in structural_types:
            for para in self._paragraph_by_type(para_type):
                with self.subTest(para_type=para_type, para=para["paragraph_id"]):
                    self.assertGreater(
                        para["confidence_scores"]["structure_detection"],
                        0.0,
                        f"{para_type} paragraph scored 0.0 structure_detection",
                    )

    def test_top_level_section_has_strong_structure_score(self) -> None:
        """A depth-1 section should score at least the 0.70 base."""
        sections = self._paragraph_by_type("section")
        self.assertTrue(sections, "expected at least one section paragraph")
        for para in sections:
            if para["hierarchy_depth"] <= 1:
                self.assertGreaterEqual(para["confidence_scores"]["structure_detection"], 0.70)

    def test_prose_paragraphs_get_nonzero_structure_floor(self) -> None:
        """F-13: free prose must never score 0.0 on structure detection."""
        normal = self._paragraph_by_type("normal")
        self.assertTrue(normal, "expected at least one normal paragraph")
        for para in normal:
            with self.subTest(para=para["paragraph_id"]):
                self.assertGreater(para["confidence_scores"]["structure_detection"], 0.0)
                self.assertLessEqual(para["confidence_scores"]["structure_detection"], 0.60)

    def test_meets_confidence_threshold_marker(self) -> None:
        """The per-paragraph marker must agree with the configured threshold."""
        for para in self.result:
            with self.subTest(para=para["paragraph_id"]):
                overall = para["confidence_scores"]["overall"]
                self.assertEqual(
                    para["meets_confidence_threshold"],
                    overall >= self.engine.config.confidence_threshold,
                )

    def test_processing_config_emits_confidence_weights(self) -> None:
        """The exported processing config must include the weights (JSON-safe)."""
        first = self.result[0]
        emitted = first["metadata"]["processing_config"]["confidence_weights"]
        self.assertEqual(emitted, self.engine.config.confidence_weights)

    def test_custom_weights_change_overall_blend(self) -> None:
        """A config with citation-only weight must reflect it in the overall."""
        engine = LegalParagraphEngine(
            ProcessingConfig(confidence_weights={"structure": 0.0, "quality": 0.0, "citation": 1.0})
        )
        para = engine.process_document(SAMPLE_TEXT)[0]
        scores = para["confidence_scores"]
        self.assertAlmostEqual(scores["overall"], scores["citation_presence"], places=6)


if __name__ == "__main__":
    unittest.main()
