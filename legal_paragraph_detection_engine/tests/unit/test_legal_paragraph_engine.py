"""Unit tests for TestLegalParagraphEngine (moved out of tests/unit/__init__.py)."""

import contextlib
import json
import os
import threading
import unittest
from typing import cast

from legal_paragraph_detection_engine import (
    LegalParagraphEngine,
    ParagraphExporter,
    ProcessingConfig,
    ProcessingMode,
)


class TestLegalParagraphEngine(unittest.TestCase):
    """Test the main LegalParagraphEngine."""

    def setUp(self):
        self.engine = LegalParagraphEngine()

    def test_process_simple_document(self):
        """Test processing a simple legal document."""
        text = """
        Section 3(1)

        3(1)(a) First clause.
        3(1)(b) Second clause.
        """

        result = self.engine.process_document(text)

        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)

        # Check basic structure
        for paragraph in result:
            self.assertIn("paragraph_id", paragraph)
            self.assertIn("section", paragraph)
            self.assertIn("clause", paragraph)
            self.assertIn("paragraph_type", paragraph)

    def test_process_with_document_type(self):
        """Test processing document with type information."""
        text = "An Act to make provision for food safety."

        doc_type_info = {"type": "act", "title": "Food Safety Act"}
        result = self.engine.process_document(text, doc_type_info)

        self.assertIsInstance(result, list)
        for paragraph in result:
            self.assertEqual(paragraph["document_type"], "act")

    def test_auto_detect_document_type_without_hint(self):
        """Without a hint the engine auto-detects the document type (T-46d)."""
        act_text = "An Act to make provision for food safety."
        result = self.engine.process_document(act_text)
        self.assertIsInstance(result, list)
        self.assertTrue(result)
        self.assertEqual(result[0]["document_type"], "act")

        notification_text = "Public Notification: License Renewal."
        result = self.engine.process_document(notification_text)
        self.assertTrue(result)
        self.assertEqual(result[0]["document_type"], "notification")

    def test_report_hint_normalized(self):
        """A report hint maps to the canonical 'report' value (T-46d)."""
        text = "The sample of Taaja Jalpan Nilgiri Chanachur was found to be misbranded."
        result = self.engine.process_document(text, {"type": "Analysis Report"})
        self.assertTrue(result)
        self.assertEqual(result[0]["document_type"], "report")

    def test_inspection_report_hint_normalized(self):
        """An inspection-report hint maps to 'inspection_report' (T-46d)."""
        text = "The premises were found to be maintained in an unhygienic condition."
        result = self.engine.process_document(text, {"type": "Inspection Report"})
        self.assertTrue(result)
        self.assertEqual(result[0]["document_type"], "inspection_report")

    def test_auto_detect_never_fails_parse(self):
        """Auto-detection is best-effort; classification errors never fail the parse."""
        text = "Section 3(1)(a) First clause."
        result = self.engine.process_document(text)
        self.assertIsInstance(result, list)
        for paragraph in result:
            self.assertIn("document_type", paragraph)

    def test_process_complex_document(self):
        """Test processing a complex legal document."""
        text = """
        Section 3(1)(a)

        3(1)(a) The following shall apply to all food businesses.

        Explanation:

        This provision establishes the framework for food business regulation.

        Provided that:
        - Registration is mandatory
        - Compliance inspections required
        - Penalties for non-compliance

        Note: This section applies from publication date.

        Schedule I
        """

        result = self.engine.process_document(text)

        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 5)  # Should have multiple paragraphs

        # Check hierarchy
        for paragraph in result:
            self.assertIn("parent_id", paragraph)
            self.assertIn("hierarchy_depth", paragraph)

    def test_engine_cache(self):
        """Test engine cache functionality."""
        text = "Section 3(1)(a)"
        result1 = self.engine.process_document(text)
        result2 = self.engine.process_document(text)

        self.assertEqual(len(result1), len(result2))

    def test_clean_engine_cache(self):
        """Test engine cache cleaning."""
        text = "Section 3(1)(a)"
        self.engine.process_document(text)
        self.engine.clear_cache()
        result = self.engine.process_document(text)
        self.assertGreater(len(result), 0)

    def test_engine_statistics(self):
        """Test engine statistics tracking."""
        text = "Section 3(1)(a)"

        # Initial stats
        stats = self.engine.get_processing_stats()
        initial_documents = stats["total_documents"]

        # Process document
        self.engine.process_document(text)

        # Check updated stats
        stats = self.engine.get_processing_stats()
        self.assertEqual(stats["total_documents"], initial_documents + 1)
        self.assertEqual(stats["successful_extractions"], 1)

    def test_engine_thread_safety(self):
        """Test engine thread safety."""

        text = "Section 3(1)(a)\n3(1)(b) Second clause."

        results = []
        errors = []

        def worker():
            try:
                result = self.engine.process_document(text)
                results.append(result)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(3)]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        self.assertEqual(len(errors), 0)
        self.assertEqual(len(results), 3)

    def test_engine_error_handling(self):
        """Test error handling."""
        # Test with invalid input
        with self.assertRaises(RuntimeError):
            self.engine.process_document(cast(str, None))

    def test_engine_configuration(self):
        """Test engine configuration."""
        config = ProcessingConfig(
            mode=ProcessingMode.ACCURATE,
            max_depth=15,
            preserve_citations=True,
        )

        engine = LegalParagraphEngine(config)
        self.assertEqual(engine.config.mode, ProcessingMode.ACCURATE)
        self.assertEqual(engine.config.max_depth, 15)

    def test_engine_export_functionality(self):
        """Test engine export functionality."""
        text = "Section 3(1)(a)"
        result = self.engine.process_document(text)

        exporter = ParagraphExporter()
        output_path = exporter.export_to_json(result, "test_output.json")

        self.assertTrue(os.path.exists(output_path))

        # Read and verify output
        try:
            with open(output_path) as f:
                saved_result = json.load(f)
        except OSError:
            self.fail(f"Could not read output file: {output_path}")

        self.assertEqual(len(saved_result), len(result))

        # Clean up
        with contextlib.suppress(OSError):
            os.remove(output_path)
