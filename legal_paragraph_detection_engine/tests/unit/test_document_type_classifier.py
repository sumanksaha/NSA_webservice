"""Unit tests for TestDocumentTypeClassifier (moved out of tests/unit/__init__.py)."""

import threading
import unittest

from legal_paragraph_detection_engine import (
    DocumentTypeClassifier,
)


class TestDocumentTypeClassifier(unittest.TestCase):
    """Test document type classification functionality."""

    def setUp(self):
        self.classifier = DocumentTypeClassifier()

    def test_classify_act(self):
        """Test Act classification."""
        text = "An Act to make provision for food safety."
        doc = self.classifier.classify_document(text)
        self.assertEqual(doc.type.name, "ACT")
        self.assertIn("food safety", doc.title.lower())

    def test_classify_rule(self):
        """Test Rule classification."""
        text = "Rules under the Food Safety Act."
        doc = self.classifier.classify_document(text)
        self.assertEqual(doc.type.name, "RULE")

    def test_classify_notification(self):
        """Test Notification classification."""
        text = "Public Notification: License Renewal."
        doc = self.classifier.classify_document(text)
        self.assertEqual(doc.type.name, "NOTIFICATION")

    def test_classify_circular(self):
        """Test Circular classification."""
        text = "Department Circular: Update procedure."
        doc = self.classifier.classify_document(text)
        self.assertEqual(doc.type.name, "CIRCULAR")

    def test_classify_government_order(self):
        """Test Government Order classification."""
        text = "G.O. No. 123. Government order for implementation."
        doc = self.classifier.classify_document(text)
        self.assertEqual(doc.type.name, "GOVERNMENT_ORDER")

    def test_classify_ordinance(self):
        """Test Ordinance classification."""
        text = "Ordinance: Emergency food regulation."
        doc = self.classifier.classify_document(text)
        self.assertEqual(doc.type.name, "ORDINANCE")

    def test_classify_bill(self):
        """Test Bill classification."""
        text = "Bill for food safety amendment."
        doc = self.classifier.classify_document(text)
        self.assertEqual(doc.type.name, "BILL")

    def test_classify_amendment(self):
        """Test Amendment classification."""
        text = "Amendment to Section 5 of the Act."
        doc = self.classifier.classify_document(text)
        self.assertEqual(doc.type.name, "AMENDMENT")

    def test_classify_panchayati_raj_act(self):
        """Test Panchayati Raj Act classification."""
        text = "Panchayati Raj Act, 1959. Rural development law."
        doc = self.classifier.classify_document(text)
        self.assertEqual(doc.type.name, "PANCHAYATI_RAJ_ACT")

    def test_classify_municipal_act(self):
        """Test Municipal Act classification."""
        text = "Municipal Act, 2023. Urban governance."
        doc = self.classifier.classify_document(text)
        self.assertEqual(doc.type.name, "MUNICIPAL_ACT")

    def test_classify_special_act(self):
        """Test Special Act classification."""
        text = "Special Emergency Food Act."
        doc = self.classifier.classify_document(text)
        self.assertEqual(doc.type.name, "SPECIAL_ACT")

    def test_classify_unknown(self):
        """Test unknown document classification."""
        text = "Random text with no clear document type."
        doc = self.classifier.classify_document(text)
        self.assertEqual(doc.type.name, "UNKNOWN")

    def test_classify_report(self):
        """Test Report classification (T-46d)."""
        text = "Analysis Report: The sample was found to be misbranded."
        doc = self.classifier.classify_document(text)
        self.assertEqual(doc.type.name, "REPORT")

    def test_classify_inspection_report(self):
        """Test Inspection Report classification (T-46d)."""
        text = "Inspection Report: The premises were found unhygienic."
        doc = self.classifier.classify_document(text)
        self.assertEqual(doc.type.name, "INSPECTION_REPORT")

    def test_report_pattern_precedes_act(self):
        """A report quoting the FSS Act must classify as REPORT, not ACT (T-46d)."""
        text = "Analysis Report: sample misbranded under section 3 of the Food Safety and Standards Act, 2006."
        doc = self.classifier.classify_document(text)
        self.assertEqual(doc.type.name, "REPORT")

    def test_normalize_doc_type_aliases(self):
        """Free-form hints map to canonical values (T-46d)."""
        self.assertEqual(self.classifier.normalize_doc_type("Analysis Report"), "report")
        self.assertEqual(self.classifier.normalize_doc_type("Inspection Report"), "inspection_report")
        self.assertEqual(self.classifier.normalize_doc_type("analyst's report"), "report")

    def test_normalize_doc_type_enum_and_custom(self):
        """Exact enum values pass through; unknown labels are kept verbatim."""
        self.assertEqual(self.classifier.normalize_doc_type("act"), "act")
        self.assertEqual(self.classifier.normalize_doc_type("notification"), "notification")
        self.assertEqual(self.classifier.normalize_doc_type("My Custom Label"), "My Custom Label")

    def test_normalize_doc_type_empty_hint(self):
        """Empty/whitespace-only hints map to 'unknown' (T-46d reviewer nit)."""
        self.assertEqual(self.classifier.normalize_doc_type(""), "unknown")
        self.assertEqual(self.classifier.normalize_doc_type("   "), "unknown")

    def test_extract_title(self):
        """Test title extraction."""
        text = "The Food Safety Act: Licensing and Registration."
        doc = self.classifier.classify_document(text)
        self.assertIn("Food Safety Act", doc.title)

    def test_extract_year(self):
        """Test year extraction."""
        text = "Food Act of 2020 with provisions."
        doc = self.classifier.classify_document(text)
        self.assertEqual(doc.year, 2020)

    def test_extract_jurisdiction(self):
        """Test jurisdiction extraction."""
        text = "Government of India: Central Act."
        doc = self.classifier.classify_document(text)
        self.assertEqual(doc.jurisdiction, "central")

    def test_cache_functionality(self):
        """Test document classifier cache."""
        text = "The Food Safety Act."
        result1 = self.classifier.classify_document(text)
        result2 = self.classifier.classify_document(text)
        self.assertEqual(result1.type, result2.type)

    def test_clean_cache(self):
        """Test cache clearing."""
        text = "Food Safety Act."
        self.classifier.classify_document(text)
        self.classifier.clear_cache()
        result = self.classifier.classify_document(text)
        self.assertIsNotNone(result.type)

    def test_classifer_thread_safety(self):
        """Test thread safety."""

        text = "Food Safety Act: Licensing and Registration."

        results = []
        errors = []

        def worker():
            try:
                result = self.classifier.classify_document(text)
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

    def test_detector_cache(self):
        """Test detector cache.

        This test was causing an error before the fix."""
        text = "Food Safety Act: Licensing and Registration."

        result1 = self.classifier.classify_document(text)
        result2 = self.classifier.classify_document(text)

        self.assertEqual(result1.type, result2.type)
        self.assertEqual(result1.title, result2.title)
