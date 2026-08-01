"""Unit tests for TestIntegration (moved out of tests/unit/__init__.py)."""

import json
import os
import time
import unittest

from legal_paragraph_detection_engine import (
    LegalParagraphEngine,
    ParagraphExporter,
)


class TestIntegration(unittest.TestCase):
    """Integration tests for the Legal Paragraph Detection Engine."""

    def test_full_pipeline(self):
        """Test full processing pipeline."""
        engine = LegalParagraphEngine()

        complex_text = """
        Section 3(1)(a)

        3(1)(a) This section governs food labeling requirements.
        3a This provision establishes registration framework.
        3a(i) Registration conditions.
        3a(ii) Compliance requirements.

        Explanation:

        The provisions outlined above ensure proper food safety standards.

        Provided that all food businesses must comply with these requirements.

        Note: Violations shall be subject to penalties.

        Schedule I

        Table 1: Registration Requirements

        Effective from January 1, 2024.
        """

        result = engine.process_document(complex_text)

        # Validate result structure
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 5)

        # Check all required fields
        required_fields = [
            "paragraph_id",
            "section",
            "clause",
            "subclause",
            "paragraph_type",
            "text",
            "citations",
            "parent_id",
            "children",
            "hierarchy_depth",
            "word_count",
            "document_type",
            "extraction_timestamp",
            "confidence_scores",
            "metadata",
        ]

        for paragraph in result:
            for field in required_fields:
                self.assertIn(
                    field, paragraph, f"Missing field: {field} in paragraph {paragraph.get('paragraph_id', 'unknown')}"
                )

        # Check hierarchy structure
        for paragraph in result:
            self.assertIsInstance(paragraph["hierarchy_depth"], int)
            self.assertGreaterEqual(paragraph["hierarchy_depth"], 0)
            self.assertIsInstance(paragraph["word_count"], int)
            self.assertGreaterEqual(paragraph["word_count"], 0)

    def test_real_world_document(self):
        """Test with a real-world style legal document."""
        engine = LegalParagraphEngine()

        # Simulated real-world legal document
        real_document = """
        The Food Safety Act, 2020

        Section 3(1)

        3(1)(a) Registration shall be mandatory for all food businesses.
        3(1)(b) Registration applications shall be submitted to the Registrar.

        Explanation:

        This section establishes the regulatory framework for food business registration.
        Registration ensures compliance with food safety standards and protects public health.

        Provided that:
        - All established food businesses shall register within 6 months
        - Registration applications shall include food safety audit reports
        - The Registrar may impose additional conditions for registration

        Note: Non-compliance shall be subject to penalties under this Act.

        Schedule I: Registration Procedures

        Table 1: Required Documents

        Effective from April 1, 2024.
        """

        result = engine.process_document(real_document)

        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)

        # Check for diversity in paragraph types
        paragraph_types = set(p["paragraph_type"] for p in result)
        self.assertGreater(len(paragraph_types), 1)

        # Check for citations (if any in the document)
        # The current document may not have citations, that's okay

    def test_performance_with_complex_document(self):
        """Test performance with a complex hierarchical document."""

        engine = LegalParagraphEngine()

        # Create a complex nested document
        complex_text = "Section 3(1)(a)(i)\n\n"
        for i in range(10):
            section = f"3(1)(a)(i).{i}"
            clause = f"3(1)(a)(i).{i}(a)"
            text = f"{section} Complex clause with nested pattern. {clause} Subclause content."
            complex_text += text + "\n\n"

        start_time = time.time()
        result = engine.process_document(complex_text)
        processing_time = time.time() - start_time

        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 10)

        # Should process reasonably quickly
        self.assertLess(processing_time, 5.0, f"Processing took too long: {processing_time} seconds")

    def test_export_serialization(self):
        """Test JSON export and serialization."""
        engine = LegalParagraphEngine()
        exporter = ParagraphExporter()

        text = """
        Section 3(1)

        3(1)(a) Simple test clause.
        (a) Subclause here.
        """

        # Process document
        result = engine.process_document(text)

        # Export to JSON
        output_path = exporter.export_to_json(result, "integration_test.json")

        # Read and parse JSON
        with open(output_path) as f:
            loaded_result = json.load(f)

        # Verify structure
        self.assertIsInstance(loaded_result, list)
        self.assertEqual(len(loaded_result), len(result))

        # Verify data integrity
        for original, loaded in zip(result, loaded_result, strict=True):
            self.assertEqual(original["paragraph_id"], loaded["paragraph_id"])
            self.assertEqual(original["text"], loaded["text"])

        # Clean up
        os.remove(output_path)

    def test_cache_effectiveness(self):
        """Test cache effectiveness with repeated processing."""
        engine = LegalParagraphEngine()

        text = "Section 3(1)(a)"

        # Process same document multiple times
        results = []
        for _ in range(5):
            result = engine.process_document(text)
            results.append(result)

        # All results should be identical
        for i in range(1, len(results)):
            self.assertEqual(len(results[i]), len(results[0]))
            self.assertEqual(results[i][0]["paragraph_id"], results[0][0]["paragraph_id"])

    def test_multithreading_consistency(self):
        """Test consistency across multiple threads."""
        import concurrent.futures

        engine = LegalParagraphEngine()
        text = "Section 3(1)(a)\n3(1)(b) Second clause."

        def process_in_thread(thread_id):
            return engine.process_document(text)

        # Process in multiple threads
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(process_in_thread, i) for i in range(5)]
            results = [future.result() for future in concurrent.futures.as_completed(futures)]

        # All results should be identical
        for result in results:
            self.assertIsInstance(result, list)
            self.assertGreater(len(result), 0)

        # Check consistency
        for i in range(1, len(results)):
            self.assertEqual(len(results[i]), len(results[0]))

    def test_error_resilience(self):
        """Test error handling and resilience.

        T-44 audit: the previous version swallowed exceptions with an
        unconditional ``self.assertTrue(True)`` pass-branch, so it could never
        fail. Every edge case below must complete and return a list.
        """
        engine = LegalParagraphEngine()

        # Test with various problematic inputs
        test_cases = [
            "",  # Empty string
            "   ",  # Whitespace only
            "Section 3(1)(a)\n\n\n\n\n3(1)(b)\n",  # Multiple newlines
            "3(1)(a) \n (b) \n (c)",  # Mixed formatting
            "1. Simple clause. 2. Another clause. 3. Third clause.",  # Different formatting
        ]

        for text in test_cases:
            with self.subTest(text=text[:50]):
                result = engine.process_document(text)
                self.assertIsInstance(result, list)

    def test_large_document_processing(self):
        """Test processing of large documents."""

        engine = LegalParagraphEngine()

        # Create a large document with many sections
        large_text = "Section 3(1)\n\n"
        for i in range(50):
            large_text += f"{i + 1}. Section {i + 1}. " * 10 + "\n\n"

        start_time = time.time()
        result = engine.process_document(large_text)
        processing_time = time.time() - start_time

        self.assertIsInstance(result, list)
        # Should have processed sections
        section_count = len([p for p in result if p["section"]])
        self.assertGreater(section_count, 0)

        # Performance should be reasonable
        self.assertLess(processing_time, 30.0, f"Large document processing took too long: {processing_time} seconds")

    def test_memory_usage(self):
        """Test memory usage with multiple processing operations."""
        import os

        import psutil

        engine = LegalParagraphEngine()
        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss

        # Process multiple documents
        texts = [
            "Section 3(1)",
            "Section 4(1)(a)",
            "Section 5(1)(b)(i)",
            "Section 6(1).",
        ]

        for text in texts:
            result = engine.process_document(text)
            self.assertIsInstance(result, list)

        final_memory = process.memory_info().rss
        memory_increase = (final_memory - initial_memory) / (1024 * 1024)  # MB

        # Memory increase should be reasonable (less than 100MB)
        self.assertLess(memory_increase, 100, f"Memory usage too high: {memory_increase:.2f} MB")


if __name__ == "__main__":
    # Run all tests
    unittest.main(verbosity=2)
