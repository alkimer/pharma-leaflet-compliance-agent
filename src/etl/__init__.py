"""Document ETL: clean-text extraction and local OCR."""

from .document_text import ExtractionResult, extract_document, extract_text

__all__ = ["ExtractionResult", "extract_document", "extract_text"]
