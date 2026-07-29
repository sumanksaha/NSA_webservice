"""
Legal Document Loader — production-grade document ingestion for NSA Webservice.

Supports PDF, Microsoft Word (.docx), and plain text (.txt) files with
page-boundary preservation, metadata extraction, batch processing, and
parallel execution.

Typical usage::

    from app.document_loader import DocumentLoaderFactory, BatchProcessor

    # Single document
    doc = DocumentLoaderFactory.load("path/to/document.pdf")
    print(doc.model_dump_json(indent=2))

    # Batch of 100k+ documents
    processor = BatchProcessor(input_dir="/data/invoices", output_dir="/data/output")
    summary = processor.run()
    print(f"Processed {summary['success_count']} documents")
"""

__version__ = "0.1.0"

from app.document_loader.base import BaseLoader
from app.document_loader.batch import BatchProcessor
from app.document_loader.loader import DocumentLoaderFactory
from app.document_loader.models import DocumentResult, FileMetadata, PageResult

__all__ = [
    "BaseLoader",
    "BatchProcessor",
    "DocumentLoaderFactory",
    "DocumentResult",
    "FileMetadata",
    "PageResult",
]
