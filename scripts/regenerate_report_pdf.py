"""
Regenerate the PDF (and the HTML) of an existing compliance report, without
calling the LLM again.

Usage:
    python scripts/regenerate_report_pdf.py <json_path> [pdf_path] [rules_folder]

When omitted:
    pdf_path      → the JSON's own path with a .pdf extension
    rules_folder  → the project's base rules folder (it only enriches each
                    disposition's metadata in the report)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core import console  # noqa: E402
from core.config import settings  # noqa: E402
from core.console import setup_logging  # noqa: E402
from reporting.report_generator import ComplianceReportGenerator  # noqa: E402


def regenerate(
    json_path: Path,
    pdf_path: Path | None = None,
    rules_dir: Path | None = None,
) -> Path:
    """
    Regenerate the PDF and the HTML from the report's JSON.

    Args:
        json_path: JSON of the compliance report.
        pdf_path: PDF output; defaults to sitting next to the JSON.
        rules_dir: RULES FOLDER used for the dispositions' metadata.

    Returns:
        The path of the generated PDF.

    Raises:
        FileNotFoundError: If the JSON does not exist.
        RuntimeError: If no engine managed to produce the PDF.
    """
    json_path = Path(json_path)
    if not json_path.exists():
        raise FileNotFoundError(f"JSON no encontrado: {json_path}")

    data = json.loads(json_path.read_text(encoding="utf-8-sig"))
    out_pdf = Path(pdf_path) if pdf_path else json_path.with_suffix(".pdf")

    generator = ComplianceReportGenerator(
        output_dir=out_pdf.parent,
        dispositions_dir=rules_dir or settings.base_rules_dir,
    )

    html_out = out_pdf.with_suffix(".html")
    try:
        html_out.write_text(generator.build_html(data), encoding="utf-8")
        console.ok(f"HTML generado: {console.path_link(html_out)}")
    except Exception as e:
        console.warn(f"No se pudo generar el HTML: {e}")

    if not generator.render_pdf(data, out_pdf):
        raise RuntimeError("No se pudo generar el PDF (ver los logs anteriores)")

    console.ok(f"PDF generado: {console.path_link(out_pdf)}")
    return out_pdf


def main() -> None:
    parser = argparse.ArgumentParser(description="Regenera el PDF de un informe de compliance")
    parser.add_argument("json", type=Path, help="JSON del informe de compliance")
    parser.add_argument("pdf", type=Path, nargs="?", help="Ruta de salida del PDF")
    parser.add_argument("reglas", type=Path, nargs="?", help="CARPETA-REGLAS de las disposiciones")
    args = parser.parse_args()

    setup_logging()
    regenerate(args.json, args.pdf, args.reglas)


if __name__ == "__main__":
    main()
