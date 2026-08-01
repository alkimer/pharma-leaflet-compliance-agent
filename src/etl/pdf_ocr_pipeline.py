"""
Local OCR pipeline for scanned PDFs: PDF → images → markdown text.

Only used as a fallback, when the PDF has no usable text layer (see
`etl.document_text`). Requires the DeepSeek-OCR model downloaded locally
(`OCR_MODEL_DIR` in .env) and it is slow: on the order of minutes per page.

Output layout:

    <output_base_dir>/<pdf_name>/
        pagina_0001/
            page_0001.png      rendered image of the page
            result.mmd         recognised text of that page
        <pdf_name>.md          merged markdown of every page
        _summary.json          processing summary
"""
from __future__ import annotations

import io
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from core import console
from core.config import settings

logger = logging.getLogger(__name__)


class PDFToOCRPipeline:
    """Converts a PDF to images and applies OCR page by page."""

    def __init__(
        self,
        pdf_path: str | Path,
        output_base_dir: str | Path,
        model_dir: Optional[str | Path] = None,
        dpi: Optional[int] = None,
        target_height: Optional[int] = None,
        device: Optional[str] = None,
    ):
        """
        Args:
            pdf_path: Input PDF.
            output_base_dir: Base output folder.
            model_dir: DeepSeek-OCR model; defaults to `settings.ocr_model_dir`.
            dpi: Rendering resolution; defaults to `settings.ocr_dpi`.
            target_height: Target height of the images; defaults to `settings.ocr_target_height`.
            device: "mps" | "cuda" | "cpu"; defaults to `settings.ocr_device`.
        """
        self.pdf_path = Path(pdf_path)
        self.output_base_dir = Path(output_base_dir)
        self.model_dir = Path(model_dir) if model_dir else settings.ocr_model_dir
        self.dpi = dpi or settings.ocr_dpi
        self.target_height = target_height or settings.ocr_target_height
        self.device = device or settings.ocr_device

        if not self.pdf_path.exists():
            raise FileNotFoundError(f"PDF no encontrado: {self.pdf_path}")
        if not self.model_dir.exists():
            raise FileNotFoundError(
                f"Modelo de OCR no encontrado: {self.model_dir}. "
                f"Descargalo o ajustá OCR_MODEL_DIR en el .env."
            )

        self.main_output_dir = self.output_base_dir / self.pdf_path.stem
        self.main_output_dir.mkdir(parents=True, exist_ok=True)

        self.ocr_engine = None  # initialised on demand

    def _init_ocr(self) -> None:
        """Load the OCR model the first time it is needed."""
        if self.ocr_engine is not None:
            return
        from etl.deepseek_ocr import DeepSeekOCR

        console.info(f"Cargando el modelo de OCR en {self.device}")
        self.ocr_engine = DeepSeekOCR(model_dir=str(self.model_dir), device=self.device)

    def _render_pages(self, first_page: int, last_page: Optional[int]) -> List[Path]:
        """Render the PDF's page range to PNG, one folder per page."""
        import fitz

        image_files: List[Path] = []
        with fitz.open(str(self.pdf_path)) as document:
            total_pages = document.page_count
            start_idx = max(0, first_page - 1)
            end_idx = min(total_pages - 1, last_page - 1) if last_page else total_pages - 1

            console.detail(f"{total_pages} páginas en el PDF; procesando {start_idx + 1}-{end_idx + 1}")
            matrix = fitz.Matrix(self.dpi / 72, self.dpi / 72)

            for page_num in range(start_idx, end_idx + 1):
                page_folder = self.main_output_dir / f"pagina_{page_num + 1:04d}"
                page_folder.mkdir(parents=True, exist_ok=True)

                pixmap = document[page_num].get_pixmap(matrix=matrix)

                from PIL import Image

                image = Image.open(io.BytesIO(pixmap.tobytes("png")))
                aspect_ratio = image.size[0] / image.size[1]
                resized = image.resize(
                    (int(self.target_height * aspect_ratio), self.target_height),
                    Image.Resampling.LANCZOS,
                )

                output_file = page_folder / f"page_{page_num + 1:04d}.png"
                resized.save(str(output_file), "PNG", optimize=True)
                image_files.append(output_file)

        console.ok(f"{len(image_files)} páginas renderizadas a imagen")
        return image_files

    def process(
        self,
        first_page: int = 1,
        last_page: Optional[int] = None,
        prompt: Optional[str] = None,
    ) -> Dict:
        """
        Run the full pipeline: PDF → images → OCR → merged markdown.

        Pages that already have a `result.mmd` are skipped, so re-running the
        pipeline over the same PDF picks up where it left off.

        Args:
            first_page: First page to process (1-indexed).
            last_page: Last page (None = all of them).
            prompt: Alternative OCR prompt (None = the model's default).

        Returns:
            {"success": bool, "merged_markdown_file": str, "output_dir": str, "summary": {...}}
        """
        start_time = time.time()
        console.section(f"OCR de {self.pdf_path.name}")

        try:
            image_files = self._render_pages(first_page, last_page)
        except Exception as e:
            logger.exception("Error convirtiendo el PDF a imágenes")
            return {"success": False, "error": str(e), "stage": "pdf_conversion"}

        if not image_files:
            return {"success": False, "error": "No se generaron imágenes", "stage": "pdf_conversion"}

        self._init_ocr()

        page_results: List[Dict] = []
        markdown_files: List[Path] = []
        successful = skipped = failed = 0
        total = len(image_files)

        for idx, image_path in enumerate(image_files, start=1):
            output_dir = image_path.parent
            result_file = output_dir / "result.mmd"

            if result_file.exists():
                skipped += 1
                markdown_files.append(result_file)
                console.progress(idx, total, "ya procesada, se reutiliza")
                page_results.append({
                    "page_num": idx, "success": True, "skipped": True,
                    "result_file": str(result_file),
                })
                continue

            console.progress(idx, total, f"OCR de {image_path.name}")
            try:
                result = self.ocr_engine.recognize(
                    image_path=str(image_path),
                    output_dir=str(output_dir),
                    prompt=prompt,
                )
                if result.get("success"):
                    successful += 1
                    if result_file.exists():
                        markdown_files.append(result_file)
                    elapsed = result.get("stats", {}).get("total_time_seconds", 0)
                    console.detail(f"ok en {elapsed:.1f}s")
                    page_results.append({
                        "page_num": idx, "success": True, "skipped": False,
                        "result_file": str(result_file), "stats": result.get("stats"),
                    })
                else:
                    failed += 1
                    console.error(f"página {idx}: {result.get('error')}")
                    page_results.append({
                        "page_num": idx, "success": False, "error": result.get("error"),
                    })
            except Exception as e:
                failed += 1
                logger.exception(f"Error procesando la página {idx}")
                console.error(f"página {idx}: {e}")
                page_results.append({"page_num": idx, "success": False, "error": str(e)})

        merged_md_file = self.main_output_dir / f"{self.pdf_path.stem}.md"
        with open(merged_md_file, "w", encoding="utf-8") as outfile:
            for idx, md_file in enumerate(markdown_files, start=1):
                outfile.write(f"\n## Página {idx}\n\n")
                outfile.write(md_file.read_text(encoding="utf-8"))
                outfile.write("\n\n---\n\n")

        total_time = time.time() - start_time
        summary = {
            "pdf_name": self.pdf_path.name,
            "total_pages": total,
            "successful": successful,
            "skipped": skipped,
            "failed": failed,
            "total_time_seconds": round(total_time, 2),
            "avg_time_per_page": round(total_time / total, 2) if total else 0,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "merged_markdown_file": str(merged_md_file),
            "config": {"dpi": self.dpi, "target_height": self.target_height, "device": self.device},
            "results": page_results,
        }
        summary_file = self.main_output_dir / "_summary.json"
        summary_file.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

        console.summary_table(
            [
                ("Páginas totales", total),
                ("Procesadas con OCR", successful),
                ("Reutilizadas", skipped),
                ("Fallidas", failed),
                ("Tiempo total", f"{total_time:.1f}s"),
                ("Markdown fusionado", merged_md_file),
            ],
            title="Resumen del OCR",
        )

        return {
            "success": True,
            "summary": summary,
            "summary_file": str(summary_file),
            "merged_markdown_file": str(merged_md_file),
            "output_dir": str(self.main_output_dir),
        }
