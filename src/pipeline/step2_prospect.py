"""
Step 2 — Leaflet to analyse → clean text.

Asks for the leaflet, copies it verbatim into `corridas/<timestamp>/documento-subido`
and leaves the clean text that steps 3 and 4 consume next to it.

For .md or .txt there is no conversion. For a PDF the native text layer is
extracted (instant, lossless) and only if the PDF is scanned does it fall back to
local OCR.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Optional

from core import console
from core.run_context import RunContext
from etl.document_text import SUPPORTED_SUFFIXES, extract_document

logger = logging.getLogger(__name__)

STEP = "paso2_prospecto"

CLEAN_TEXT_SUFFIX = ".md"


def run(
    ctx: RunContext,
    prospect_path: Optional[Path] = None,
    interactive: bool = True,
    force_ocr: bool = False,
) -> Path:
    """
    Run step 2 and return the path of the leaflet's clean text.

    Args:
        ctx: The run's context.
        prospect_path: Leaflet to analyse; if omitted and `interactive`, it is asked for.
        interactive: When False, `prospect_path` is mandatory.
        force_ocr: Force local OCR even when the PDF has a text layer.

    Returns:
        Path of the .md holding the clean text, inside `documento-subido/`.
    """
    if prospect_path is None:
        if not interactive:
            raise ValueError("Falta el prospecto a analizar (--prospecto)")
        prospect_path = _ask_for_prospect(ctx)

    prospect_path = Path(prospect_path)
    if prospect_path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError(
            f"Extensión no soportada: {prospect_path.suffix}. "
            f"Soportadas: {', '.join(sorted(SUPPORTED_SUFFIXES))}"
        )

    ctx.uploaded_dir.mkdir(parents=True, exist_ok=True)

    # 1. Preserve the uploaded original.
    original_copy = ctx.uploaded_dir / prospect_path.name
    if prospect_path.resolve() != original_copy.resolve():
        shutil.copy2(prospect_path, original_copy)
    console.ok(f"Prospecto original: {console.path_link(original_copy)}")

    # 2. Get the clean text (OCR works inside the run's folder).
    extraction = extract_document(
        original_copy,
        ocr_output_dir=ctx.uploaded_dir / "ocr",
        force_ocr=force_ocr,
    )

    clean_path = ctx.uploaded_dir / f"{prospect_path.stem}_texto_limpio{CLEAN_TEXT_SUFFIX}"
    clean_path.write_text(extraction.text, encoding="utf-8")

    console.summary_table(
        [
            ("Archivo subido", original_copy.name),
            ("Método de extracción", extraction.method),
            ("Páginas", extraction.pages if extraction.pages else "n/d"),
            ("Caracteres", extraction.char_count),
            ("Texto limpio", clean_path),
        ],
        title="Resumen del paso 2",
    )

    if extraction.char_count < 500:
        console.warn(
            "El texto extraído es muy corto: revisá el archivo antes de seguir, "
            "el análisis puede ser incompleto."
        )

    ctx.record(
        STEP,
        original_file=original_copy,
        clean_text_file=clean_path,
        extraction_method=extraction.method,
        pages=extraction.pages,
        char_count=extraction.char_count,
        ocr_output_dir=extraction.ocr_output_dir,
    )
    return clean_path


def _ask_for_prospect(ctx: RunContext) -> Path:
    """Ask for the leaflet, offering the repo's examples as a shortcut."""
    examples_dir = ctx.settings.project_root / "ejemplos" / "prospectos"
    suggestions = sorted(p for p in examples_dir.glob("*.md")) if examples_dir.is_dir() else []

    return console.ask_existing_path(
        "Prospecto a analizar (PDF, MD, TXT o DOCX) — podés arrastrar el archivo a la terminal",
        must_be="file",
        suggestions=suggestions,
    )
