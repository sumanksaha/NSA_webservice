"""
Example usage of the Legal Paragraph Detection Engine.

This file contains practical examples demonstrating how to use the engine
to process various types of legal documents.
"""

from legal_paragraph_detection_engine import (
    DocumentTypeClassifier,
    LegalParagraphEngine,
    ParagraphExporter,
    ProcessingConfig,
    ProcessingMode,
)


def basic_example():
    """Basic example of processing a legal document."""
    # Initialize engine with default settings
    engine = LegalParagraphEngine()

    # Sample legal text with hierarchical structure
    legal_text = """
    Section 3

    3(1) In addition to the provisions of this Act, the following shall apply:

    3(1)(a) For the purposes of this section, "concerned food" shall mean any food
    3(1)(b) Any person who violates this provision shall be liable for penalties

    Explanation:

    The above provisions are meant to ensure compliance with food safety standards.
    Provided that exceptions may be made for emergency situations.

    Note: This is a sample legal document for demonstration purposes.

    Schedule I

    Table 1: Classification of Food Items
    """

    # Process the document
    result = engine.process_document(legal_text)

    print(f"Processed {len(result)} paragraphs from legal document")
    print("\nSample output:")

    for i, paragraph in enumerate(result[:3], 1):
        print(f"\n{i}. Section {paragraph['section']}, Clause {paragraph['clause']}")
        print(f"   Type: {paragraph['paragraph_type']}")
        print(f"   Text: {paragraph['text'][:150]}...")

    return result


def batch_processing_example():
    """Example of processing multiple legal documents."""
    # Initialize engine
    engine = LegalParagraphEngine()

    # Sample legal texts
    documents = [
        """
        Section 5

        5(1) The following shall apply to all food businesses.
        5(1)(a) Registration requirements for food operators.
        """,
        """
        Section 10

        10(1) This section deals with inspection procedures.
        10(1)(a) Inspection frequency and requirements.
        """,
        """
        Section 15

        15(1) Licensing provisions for food safety.
        15(1)(a) Conditions for license issuance.
        """,
    ]

    # Process each document
    all_results = []
    for i, text in enumerate(documents):
        result = engine.process_document(text)
        all_results.extend(result)

        # Export each result
        exporter = ParagraphExporter()
        filename = f"document_{i + 1}_output.json"
        exporter.export_to_json(result, filename)
        print(f"Document {i + 1} saved to {filename}")

    print(f"\nProcessed {len(all_results)} total paragraphs across {len(documents)} documents")
    return all_results


def configuration_example():
    """Example of using custom configuration."""
    # Create custom configuration
    config = ProcessingConfig(
        mode=ProcessingMode.COMPREHENSIVE,
        max_depth=20,
        confidence_threshold=0.8,
        preserve_citations=True,
        normalize_text=True,
        detect_special_patterns=True,
        output_format="json",
        export_path="./custom_output",
    )

    # Initialize engine with custom config
    engine = LegalParagraphEngine(config)

    # Process a complex legal document
    complex_text = """
    Section 3(1)(a)(i)

    3(1)(a)(i) The following shall apply to all food businesses registered under this Act.

    Explanation:

    This provision establishes the framework for food business regulation and oversight.

    Provided that:
    - Registration is mandatory for all food operators
    - Compliance inspections shall be conducted semi-annually
    - Penalties shall apply for non-compliance

    Note: This section shall come into effect from the date of publication.

    Schedule II

    Table 2: Compliance Checklist

    The following table outlines the required compliance measures:
    """

    result = engine.process_document(complex_text)

    print(f"Processed {len(result)} paragraphs with comprehensive configuration")
    print("Custom output saved to './custom_output/' directory")

    return result


def document_type_detection_example():
    """Example of automatic document type detection."""
    # Initialize document type classifier
    classifier = DocumentTypeClassifier()

    # Sample legal documents (Act, Rule, Notification, Circular)
    documents = [
        ("An Act to make provision for food safety", "act"),
        ("Rules under the Food Safety Act", "rule"),
        ("Public Notification: License Renewal Deadline", "notification"),
        ("Department Circular: Inspection Protocol Update", "circular"),
    ]

    print("Document Type Detection Examples:")
    print("=" * 50)

    for text, expected_type in documents:
        doc = classifier.classify_document(text)
        print(f"Text: {text[:50]}...")
        print(f"Detected type: {doc.type.value}")
        print(f"Expected type: {expected_type}")
        print(f"Match: {doc.type.value == expected_type}")
        print("-" * 50)

    return documents


def export_example():
    """Example of different export formats."""
    # Initialize engine and process document
    engine = LegalParagraphEngine()

    legal_text = """
    Section 3(1)

    3(1) This section governs food labeling requirements.
    3(1)(a) Labeling frequency and format.
    """

    result = engine.process_document(legal_text)

    # Initialize exporter
    exporter = ParagraphExporter()

    # Export in different formats
    json_output = exporter.export_to_json(result, "full_output.json")
    print(f"Full JSON output saved to: {json_output}")

    compact_output = exporter.export_to_compact_json(result, "compact_output.json")
    print(f"Compact JSON output saved to: {compact_output}")

    hierarchy_output = exporter.export_hierarchy_report(result, "hierarchy_output.json")
    print(f"Hierarchy report saved to: {hierarchy_output}")

    metadata_output = exporter.export_with_metadata(result, "metadata_output.json")
    print(f"Metadata output saved to: {metadata_output}")

    return json_output, compact_output, hierarchy_output, metadata_output


def cli_example():
    """Example of CLI usage pattern."""
    print("CLI Usage Examples:")
    print("=" * 50)

    print("1. Process single document:")
    print("   legal-parser process legal_text.txt --output result.json")

    print("\n2. Process multiple documents:")
    print("   legal-parser batch legal_docs/ --output-dir batch_results")

    print("\n3. Run demo:")
    print("   legal-parser demo")

    print("\n4. Show statistics:")
    print("   legal-parser stats")

    print("\n5. Custom configuration:")
    print("   legal-parser process -f json -o custom.json --engine accurate")


def main():
    """Run all examples."""
    print("Legal Paragraph Detection Engine - Examples")
    print("=" * 60)

    examples = [
        ("Basic Processing", basic_example),
        ("Batch Processing", batch_processing_example),
        ("Configuration Example", configuration_example),
        ("Document Type Detection", document_type_detection_example),
        ("Export Example", export_example),
        ("CLI Usage Pattern", cli_example),
    ]

    results = {}

    for name, example_func in examples:
        print(f"\n{'=' * 60}")
        print(f"Example: {name}")
        print(f"{'=' * 60}")

        try:
            results[name] = example_func()
            print(f"✓ {name} completed successfully")
        except Exception as e:
            print(f"✗ {name} failed with error: {e}")
            results[name] = None

    print(f"\n{'=' * 60}")
    print("All examples completed!")
    print(f"{'=' * 60}")

    return results


if __name__ == "__main__":
    main()
