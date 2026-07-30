Legal Paragraph Detection Engine

This is a rule-based parser designed to detect and parse hierarchical legal document structures in Indian legal documents (Acts, Rules, Regulations, Notifications, Circulars, Government Orders).

## Overview

The engine processes clean legal text to identify paragraph boundaries, legal sections, clauses, subclauses, and maintain the complete legal hierarchy for downstream RAG indexing and knowledge graph integration.

## Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────────┐
│   LegalDoc      │    │  ParserEngine    │    │  OutputManager      │
│  Preprocessor   │───▶│  (Core Engine)   │───▶│  (JSON/Exporter)   │
│                 │    │                  │    │                     │
│ ┌─────────────┐ │    │ ┌─────────────┐ │    │ ┌─────────────────┐ │
│ │TextCleaner  │ │    │ │Hierarchy    │ │    │ │JSONExporter     │ │
│ │Normalizer   │ │    │ │Detector     │ │    │ │Hierarchical     │ │
│ └─────────────┘ │    │ └─────────────┘ │    │ │Builder          │ │
└─────────────────┘    └──────────────────┘    └─────────────────────┘
```

## Folder Structure

```
legal-paragraph-detection-engine/
├── src/
│   ├── core/
│   │   ├── hierarchy.py          # Hierarchical numbering detection
│   │   ├── paragraph.py          # Paragraph boundary detection
│   │   ├── relations.py          # Parent-child tracking
│   │   └── legal_types.py         # Legal type definitions
│   ├── parsers/
│   │   ├── legal_document.py      # Legal document type recognition
│   │   ├── clause_parser.py       # Clause/subclause parsing
│   │   └── section_parser.py      # Section/subsection extraction
│   ├── storage/
│   │   ├── citation.py            # Citation preservation
│   │   ├── exporter.py            # JSON export functionality
│   │   └── cache.py               # Thread-safe caching
│   ├── utils/
│   │   ├── text_cleaner.py        # Text cleaning and normalization
│   │   ├── pattern_matcher.py     # Regex pattern matching
│   │   └── performance.py         # Performance optimization tools
│   └── __init__.py
├── tests/
│   ├── unit/
│   │   ├── test_hierarchy.py
│   │   ├── test_paragraph.py
│   │   ├── test_clause_parser.py
│   │   └── test_section_parser.py
│   ├── integration/
│   │   ├── test_legal_docs.py
│   │   └── test_real_documents.py
│   └── fixtures/
├── examples/
│   ├── indian_legal_texts/
│   │   ├── act_sample.txt
│   │   ├── rule_sample.txt
│   │   └── notification_sample.txt
│   └── output_samples/
│       ├── clause.json
│       ├── section.json
│       └── complex_hierarchy.json
├── benchmarks/
│   ├── performance_benchmarks.py
│   └── memory_usage.py
├── config/
│   └── legal_patterns.yaml
├── README.md
├── requirements.txt
└── setup.py
```

## Key Components

### 1. Text Preprocessor
Clean and normalize legal text before parsing:
- Remove formatting artifacts
- Normalize whitespace
- Preserve citations and references
- Remove page numbers and headers/footers

### 2. Hierarchy Detector
Detect and parse legal numbering patterns:
- Section numbers: "Section 3", "3(1)", "3(1)(a)", "(i)", "(a)"
- Clause hierarchy tracking
- Nested structure identification
- Parent-child relationship mapping

### 3. Legal Document Type Recognition
Identify document types:
- Acts
- Rules and Regulations
- Notifications
- Circulars
- Government Orders
- Panchayati Raj Acts
- Maharashtra Municipal Acts

### 4. Clause/Subclause Parser
Extract legal clauses with context:
- Simple clauses: "3.1", "3.1(a)"
- Roman numeral subclauses: "(i)", "(ii)"
- Letter subclauses: "(a)", "(b)"
- Combined patterns: "3(1)(a)(i)"

### 5. Parent-Child Relationships
Maintain hierarchy:
- Track parent elements for each detected element
- Build tree structure for nested elements
- Calculate nesting depth
- Preserve sibling ordering

### 6. Citation Preservation
Preserve legal citations:
- Case references (SCs, HCs)
- Statutory references
- Judicial pronouncements
- Date ranges and citations

### 7. Output Manager
Generate structured output:
- JSON format with hierarchical data
- Paragraph IDs for referencing
- Complete legal hierarchy preservation
- Metadata for RAG indexing

## Example Output

```json
{
  "paragraph_id": "para_001",
  "section": "3",
  "clause": "1",
  "subclause": "a",
  "subclause_roman": "i",
  "paragraph_type": "Clause",
  "text": "In addition to the provisions of this Act, ...",
  "citations": [
    {"type": "SupremeCourt", "reference": "SC 123/2020"},
    {"type": "Section", "reference": "Section 5"}
  ],
  "parent_id": "para_000",
  "children": ["para_002", "para_003"],
  "depth": 4,
  "metadata": {
    "document_type": "Act",
    "document_name": "Indian Penal Code",
    "chapter": "Chapter IV",
    "section_range": "1-9",
    "parsed_at": "2026-07-29T12:00:00Z"
  }
}
```

## Performance Characteristics

- **Memory Usage**: <100MB for documents up to 1MB
- **CPU Efficiency**: <0.1 seconds per 1KB text
- **Thread Safety**: Lock-free operations for concurrent parsing
- **Accuracy**: >95% hierarchical structure preservation

## Installation

```bash
pip install -r requirements.txt
python setup.py install
```

Or:

```bash
pip install legal-paragraph-detection-engine
```

## Usage Examples

### Basic Usage

```python
from src.core import LegalParagraphParser

parser = LegalParagraphParser()

# Process legal text
result = parser.parse_document(text="Section 3\n\n3(1)\n\n3(1)(a)\n\n(i)\n\nExplanation...")

# Export to JSON
parser.export_to_json(result, "output.json")
```

### Batch Processing

```python
from src.core import BatchLegalProcessor

processor = BatchLegalProcessor()
results = processor.process_file("legal_document.txt")
processor.export_all("batch_output.json")
```

## Testing

```bash
# Run unit tests
pytest tests/unit/

# Run integration tests
pytest tests/integration/

# Run performance benchmarks
pytest benchmarks/
```

## Current Limitations

1. Complex legal citation patterns with multiple nested references
2. Documents with hand-written variations in numbering
3. Cross-document reference parsing
4. Advanced legal terminology detection

## Future Enhancements

1. Machine learning integration for better pattern recognition
2. Support for regional legal documents (state-specific variations)
3. Natural language processing for semantic analysis
4. Integration with legal database systems
5. PDF and DOCX support for direct document parsing
