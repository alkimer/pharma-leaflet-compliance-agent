"""
Compliance report generation in JSON, Markdown, HTML and PDF.

PDF engines:
    1. ReportLab (default): self-contained, reproduces the report's look and does
       not depend on external binaries.
    2. xhtml2pdf (fallback): only used if ReportLab fails.

The high-fidelity HTML is always written as well; it can be opened in a browser
and printed to PDF when the exact render is wanted.
"""
from __future__ import annotations

import html as _html
import json
import logging
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Human-readable labels for each compliance status.
STATUS_LABELS = {
    "ok": "Cumple",
    "missing": "Incumple",
    "not_applicable": "No Aplica",
    "not_evaluable": "No Evaluable",
    "error": "Error",
}

# Rule groups of the Markdown report: (status, section title).
MARKDOWN_GROUPS = (
    ("ok", "REGLAS CUMPLIDAS"),
    ("missing", "REGLAS NO CUMPLIDAS"),
    ("not_applicable", "REGLAS NO APLICAN"),
    ("not_evaluable", "REGLAS NO EVALUABLES (requieren información externa)"),
    ("error", "REGLAS CON ERROR DE VERIFICACIÓN"),
)

# Order of the rules within each disposition in the PDF/HTML.
_PDF_STATUS_ORDER = {"missing": 0, "ok": 1, "not_evaluable": 2, "not_applicable": 3}


class ComplianceReportGenerator:
    """Writes the compliance report in every format."""

    def __init__(self, output_dir: str | Path, dispositions_dir: Optional[str | Path] = None):
        """
        Args:
            output_dir: Output folder (corridas/<timestamp>/resultado).
            dispositions_dir: RULES FOLDER, used to enrich the report with each
                disposition's metadata (title, type, objective).
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.dispositions_dir = Path(dispositions_dir) if dispositions_dir else None

    # ========================================================================
    # Markdown
    # ========================================================================

    @staticmethod
    def _bold(text: Any) -> str:
        return f"**{text}**"

    @staticmethod
    def _italic(text: Any) -> str:
        return f"*{text}*"

    def _rule_markdown(self, rule: Dict[str, Any]) -> List[str]:
        """Markdown block of one rule."""
        lines = [
            "  " + self._bold(f"Regla {rule.get('rule_id')}:") + " " + self._italic(rule.get("objective", "")),
            "",
        ]
        if rule.get("verification_procedure"):
            lines.append(f"  {self._bold('Procedimiento de verificación:')} {rule['verification_procedure']}  ")
        if rule.get("acceptance_criteria"):
            lines.append(f"  {self._bold('Criterio de aceptación:')} {rule['acceptance_criteria']}  ")
        if rule.get("evidence_snippets"):
            lines.append(f"  {self._bold('Evidencia:')}  ")
            lines.extend(f"  - {self._italic(e)}" for e in rule["evidence_snippets"])
            # Two blank lines: they close the list and force a new paragraph.
            lines.extend(["", ""])
        if rule.get("checker_notes"):
            lines.append(f"  {self._bold('Notas del verificador:')} {rule['checker_notes']}  ")
            lines.append("")
        if rule.get("must_include_phrases"):
            lines.append(f"  {self._bold('Frases obligatorias:')}  ")
            lines.extend(f"  - {self._italic(p)}" for p in rule["must_include_phrases"])
        if rule.get("article_reference"):
            lines.append(f"  {self._bold('Referencia legal:')} {rule['article_reference']}  ")
        if rule.get("attach_reference"):
            lines.append(f"  {self._bold('Anexos:')} {', '.join(rule['attach_reference'])}  ")
        lines.append("")
        return lines

    def json_to_markdown(self, json_data: Dict[str, Any]) -> str:
        """Convert the report to Markdown."""
        metadata = json_data["metadata"]
        summary = json_data["summary"]
        md: List[str] = [
            self._bold(f"REPORTE DE ADECUACIÓN DE PROSPECTO: {metadata['prospect_name']}"),
            "",
            self._bold("ARCHIVO ANALIZADO:") + f" {metadata['prospect_file']}",
            self._bold("FECHA DE ANÁLISIS:") + f" {metadata['analysis_date']}",
            "",
            f"\n\n{self._bold('RESUMEN GENERAL')}\n",
            f"- {self._bold('Disposiciones evaluadas:')} {summary['total_dispositions_evaluated']}",
            f"- {self._bold('Disposiciones aplicables:')} {summary['applicable_dispositions']}",
            f"- {self._bold('Disposiciones verificadas:')} {summary['verified_dispositions']}",
            f"- {self._bold('Total de reglas verificadas:')} {summary['total_rules_checked']}",
            "",
        ]

        details_by_id = {d["disposition_id"]: d for d in json_data["compliance_details"]}

        for counter, disposition in enumerate(json_data["classification_results"], start=1):
            disposition_id = disposition["disposition_id"]
            kind = "Circular" if "CIRCULAR" in str(disposition_id) else "Disposición"
            md.append(f"\n\n{self._bold(f'{counter}. {kind}: {disposition_id}')}\n")
            md.append(f"{self._bold('Aplica:')} {self._bold('SÍ' if disposition.get('applies') else 'NO')}  ")
            md.append(f"{self._bold('Score de coincidencia:')} {disposition.get('match_score')}  ")
            md.append(f"{self._bold('Motivo:')} {self._italic(disposition.get('reason', ''))}  ")

            details = details_by_id.get(disposition_id)
            if not details:
                continue

            md.append(f"{self._bold('Total de reglas evaluadas:')} {details['total_rules']}  ")
            md.append(f"{self._bold('Porcentaje de cumplimiento:')} {details['compliance_percentage']}%  ")
            md.append("")

            for status, title in MARKDOWN_GROUPS:
                rules = [r for r in details["rules"] if r.get("status") == status]
                if not rules:
                    continue
                md.append("- " + self._italic(f"{title}:"))
                md.append("")
                for rule in rules:
                    md.extend(self._rule_markdown(rule))

            status_summary = details["status_summary"]
            md.append(self._bold("Resumen de reglas:"))
            md.extend(
                f"- {label}: {status_summary.get(status, 0)}"
                for status, label in (
                    ("ok", "Cumplidas"),
                    ("missing", "No cumplidas"),
                    ("not_applicable", "No aplican"),
                    ("not_evaluable", "No evaluables"),
                )
            )
            md.append("")

        stats = json_data["overall_statistics"]
        md.append(f"\n\n{self._bold('RESUMEN GLOBAL DE CUMPLIMIENTO')}\n")
        md.extend(
            f"- {self._bold(label)}: {stats.get(status, 0)}"
            for status, label in (
                ("ok", "Reglas cumplidas"),
                ("missing", "Reglas no cumplidas"),
                ("not_applicable", "No aplican"),
                ("not_evaluable", "No evaluables (requieren info externa)"),
                ("error", "Errores de verificación"),
            )
        )
        return "\n".join(md)

    # ========================================================================
    # HTML
    # ========================================================================

    @staticmethod
    def _esc(value: Any) -> str:
        """Escape a value so it can be inserted into HTML."""
        return "" if value is None else _html.escape(str(value), quote=False)

    def _load_dispositions_metadata(self) -> Dict[str, Dict[str, str]]:
        """
        Load title, type, sale condition and objective of every disposition.

        Used for the report's metadata cards. If no `dispositions_dir` is
        configured, returns an empty map.
        """
        metadata_map: Dict[str, Dict[str, str]] = {}
        if not self.dispositions_dir or not self.dispositions_dir.is_dir():
            return metadata_map

        for json_file in sorted(self.dispositions_dir.glob("*.json")):
            try:
                # utf-8-sig: the disposition JSONs may carry a BOM.
                content = json.loads(json_file.read_text(encoding="utf-8-sig"))
            except Exception as e:
                logger.warning(f"No se pudieron leer los metadatos de {json_file.name}: {e}")
                continue
            disposition_id = content.get("disposition_id")
            if disposition_id:
                metadata_map[disposition_id] = {
                    "title": content.get("title", "N/D"),
                    "source_type": content.get("source_type", "N/D"),
                    "sale_condition": content.get("sale_condition", "N/D"),
                    "objective": content.get("objective", "N/D"),
                }
        return metadata_map

    def build_html(self, json_data: Dict[str, Any]) -> str:
        """
        Build the report's high-fidelity HTML.

        Meant to be rendered in a browser (open and print to PDF) and to act as a
        visual fallback for the PDF generated with ReportLab.
        """
        e = self._esc
        metadata = json_data.get("metadata", {})
        summary = json_data.get("summary", {})
        compliance_details = json_data.get("compliance_details", [])

        classification_map = {}
        non_applicable = []
        for result in json_data.get("classification_results", []):
            disposition_id = result.get("disposition_id")
            if not disposition_id:
                continue
            applies = result.get("applies", False)
            classification_map[disposition_id] = {
                "applies": applies,
                "reason": result.get("reason", ""),
            }
            if not applies:
                non_applicable.append({
                    "disposition_id": disposition_id,
                    "reason": result.get("reason", ""),
                })

        disp_metadata = self._load_dispositions_metadata()
        counts = _count_statuses(compliance_details)

        parts = [
            '<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8">',
            "<title>Reporte de Adecuación de Prospecto</title>",
            f"<style>{_HTML_CSS}</style></head><body>",
            f'<div class="running-header"><span class="rh-date">'
            f'{e(metadata.get("analysis_date", ""))}</span>'
            '<div class="rh-title">Reporte de Adecuación de Prospecto</div></div>',
            "<h1>Reporte de Adecuación de Prospecto</h1>",
            '<table class="header-table">',
            f'<tr><td>PRODUCTO ANALIZADO:</td><td class="value">'
            f'{e(str(metadata.get("prospect_name", "")).replace("_", " "))}</td>'
            f'<td>FECHA DE ANÁLISIS:</td><td class="value">{e(metadata.get("analysis_date", ""))}</td></tr>',
            f'<tr><td>DISPOSICIONES EVALUADAS:</td><td class="value">'
            f'{e(summary.get("total_dispositions_evaluated", 0))}</td>'
            f'<td>DISPOSICIONES APLICABLES:</td><td class="value">'
            f'{e(summary.get("applicable_dispositions", 0))}</td></tr>',
            f'<tr><td>REGLAS VERIFICADAS:</td><td class="value">'
            f'{e(summary.get("total_rules_checked", 0))}</td>'
            f'<td>ESTADO GENERAL:</td><td class="value"><strong>Verificación Completa</strong></td></tr>',
            "</table>",
            "<h2>Resumen General de Cumplimiento</h2>",
            '<div class="summary-container">',
            f'<div class="summary-card"><div class="number">{counts["total"]}</div>'
            '<div class="label">Total Reglas</div></div>',
        ]
        for key, label, color in (
            ("ok", "Cumplen", "#15803d"),
            ("missing", "Incumplen", "#b91c1c"),
            ("not_evaluable", "No Evaluables", "#d97706"),
            ("not_applicable", "No Aplican", "#475569"),
        ):
            parts.append(
                f'<div class="summary-card" style="border-color:{color};">'
                f'<div class="number" style="color:{color};">{counts[key]}</div>'
                f'<div class="label" style="color:{color};">{label}</div></div>'
            )
        parts.append("</div>")

        for disposition in compliance_details:
            disposition_id = disposition.get("disposition_id", "")
            classification = classification_map.get(disposition_id, {})

            parts.append('<div class="page-break"></div>')
            parts.append(f"<h2>Disposición: {e(disposition_id)}</h2>")

            extra = disp_metadata.get(disposition_id)
            if extra:
                parts.append(
                    '<div class="disp-info-card">'
                    f'<div class="disp-info-title">{e(extra.get("title"))}</div>'
                    f'<div class="disp-line"><strong>Tipo:</strong> {e(extra.get("source_type"))} | '
                    f'<strong>Condición de venta:</strong> {e(extra.get("sale_condition") or "N/A")}</div>'
                    f'<div class="disp-line"><strong>Objetivo:</strong> {e(extra.get("objective"))}</div>'
                    f'<div class="disp-line disp-sep">'
                    f'<strong>Aplica:</strong> {"SÍ" if classification.get("applies", True) else "NO"}<br>'
                    f'<strong>Motivo de aplicación:</strong> {e(classification.get("reason", "N/D"))}</div>'
                    "</div>"
                )

            parts.append(
                '<div class="disp-stats">'
                f'<strong>Porcentaje de cumplimiento:</strong> '
                f'{e(_fmt_pct(disposition.get("compliance_percentage")))}<br>'
                f'<strong>Total de reglas evaluadas:</strong> {e(disposition.get("total_rules", 0))}</div>'
            )

            rules = sorted(
                disposition.get("rules", []),
                key=lambda r: _PDF_STATUS_ORDER.get(r.get("status", ""), 4),
            )
            for rule in rules:
                parts.append(self._rule_html(rule))

        if non_applicable:
            parts.append('<div class="page-break"></div>')
            parts.append("<h2>Disposiciones y Circulares No Aplicables</h2>")
            parts.append(
                '<div class="disp-stats">A continuación se listan las normativas evaluadas que '
                "no aplican al prospecto bajo análisis y el motivo correspondiente.</div>"
            )
            for item in non_applicable:
                extra = disp_metadata.get(item["disposition_id"], {})
                parts.append(
                    '<div class="disp-info-card" style="border-color:#475569;">'
                    f'<div class="disp-info-title" style="color:#475569;">'
                    f'{e(item["disposition_id"])}: {e(extra.get("title", "N/D"))}</div>'
                    f'<div class="disp-line"><strong>Tipo:</strong> '
                    f'{e(extra.get("source_type", "N/D"))} | <strong>Aplica:</strong> NO</div>'
                    f'<div class="disp-line disp-sep"><strong>Motivo de exclusión:</strong> '
                    f'{e(item["reason"])}</div>'
                    "</div>"
                )

        parts.append('<div class="footer">Documento de conformidad generado automáticamente.</div>')
        parts.append("</body></html>")
        return "".join(parts)

    def _rule_html(self, rule: Dict[str, Any]) -> str:
        """HTML card of one rule."""
        e = self._esc
        rule_id = rule.get("rule_id")
        label = f"Regla {rule_id}" if rule_id is not None else "Regla Especial"
        status = rule.get("status", "")

        parts = [
            '<div class="rule-card">',
            '<div class="rule-header">'
            f'<span class="rule-title">{e(label)}: {e(rule.get("objective", ""))}</span>'
            f'<span class="badge badge-{e(status)}">{e(STATUS_LABELS.get(status, status))}</span>'
            "</div>",
        ]

        attach = rule.get("attach_reference") or []
        attach_str = " | Anexos: " + e(", ".join(attach)) if attach else ""
        parts.append(
            f'<div class="rule-meta">Referencia legal: '
            f'{e(rule.get("article_reference", "N/D"))}{attach_str}</div>'
        )

        for field, title in (
            ("verification_procedure", "Procedimiento de verificación"),
            ("acceptance_criteria", "Criterio de aceptación"),
        ):
            if rule.get(field):
                parts.append(
                    f'<div class="rule-section"><div class="section-label">{title}:</div>'
                    f'<div class="section-content">{e(rule[field])}</div></div>'
                )

        for field, title in (
            ("evidence_snippets", "Evidencia encontrada"),
            ("must_include_phrases", "Frases obligatorias"),
        ):
            if rule.get(field):
                items = "".join(f'<li class="evidence-item">{e(v)}</li>' for v in rule[field])
                parts.append(
                    f'<div class="rule-section"><div class="section-label">{title}:</div>'
                    f'<ul class="evidence-list">{items}</ul></div>'
                )

        if rule.get("checker_notes"):
            parts.append(
                '<div class="rule-section"><div class="section-label">Notas del verificador:</div>'
                f'<div class="section-content" style="font-weight:600;">'
                f'{e(rule["checker_notes"])}</div></div>'
            )

        parts.append("</div>")
        return "".join(parts)

    # ========================================================================
    # PDF
    # ========================================================================

    def render_pdf(self, json_data: Dict[str, Any], pdf_path: Path) -> bool:
        """
        Generate the PDF with ReportLab and, if that fails, with xhtml2pdf.

        Returns:
            True if either engine produced the PDF.
        """
        pdf_path = Path(pdf_path)
        try:
            from reporting.pdf_reportlab import build_pdf

            build_pdf(json_data, str(pdf_path), self._load_dispositions_metadata())
            if pdf_path.exists() and pdf_path.stat().st_size > 0:
                logger.info(f"PDF generado con ReportLab: {pdf_path}")
                return True
            logger.error("ReportLab no produjo un PDF válido")
        except Exception as e:
            logger.error(f"Error generando el PDF con ReportLab: {e}")
            logger.debug(traceback.format_exc())

        logger.info("Usando el motor de respaldo (xhtml2pdf)")
        return self._render_pdf_from_html(json_data, pdf_path)

    def _render_pdf_from_html(self, json_data: Dict[str, Any], pdf_path: Path) -> bool:
        """Fallback: convert the report's HTML to PDF with xhtml2pdf."""
        try:
            from xhtml2pdf import pisa
        except ImportError as e:
            logger.warning(f"xhtml2pdf no está disponible: {e}")
            return False

        try:
            html_content = self.build_html(json_data)
            with open(pdf_path, "wb") as pdf_file:
                status = pisa.CreatePDF(html_content.encode("utf-8"), dest=pdf_file, encoding="utf-8")
            if status.err:
                logger.error(f"xhtml2pdf devolvió errores: {status.err}")
                return False
            return pdf_path.exists() and pdf_path.stat().st_size > 0
        except Exception as e:
            logger.error(f"Error generando el PDF con xhtml2pdf: {e}")
            logger.debug(traceback.format_exc())
            return False

    # ========================================================================
    # Orchestration
    # ========================================================================

    def generate_reports(self, json_data: Dict[str, Any], prospect_name: str) -> Dict[str, Path]:
        """
        Write the report as JSON, Markdown, HTML and PDF.

        Args:
            json_data: Compliance report assembled by the graph.
            prospect_name: Leaflet name, used in the filename.

        Returns:
            Map of format → path for the files that were actually generated.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = f"compliance_report_{prospect_name}_{timestamp}"
        paths: Dict[str, Path] = {}

        json_path = self.output_dir / f"{base}.json"
        json_path.write_text(json.dumps(json_data, ensure_ascii=False, indent=2), encoding="utf-8")
        paths["json"] = json_path

        markdown_path = self.output_dir / f"{base}.md"
        markdown_path.write_text(self.json_to_markdown(json_data), encoding="utf-8")
        paths["markdown"] = markdown_path

        html_path = self.output_dir / f"{base}.html"
        try:
            html_path.write_text(self.build_html(json_data), encoding="utf-8")
            paths["html"] = html_path
        except Exception as e:
            logger.warning(f"No se pudo generar el HTML: {e}")

        pdf_path = self.output_dir / f"{base}.pdf"
        if self.render_pdf(json_data, pdf_path):
            paths["pdf"] = pdf_path
        else:
            logger.warning(
                "No se pudo generar el PDF; el JSON, el Markdown y el HTML sí están disponibles"
            )

        return paths


def _count_statuses(compliance_details: List[Dict[str, Any]]) -> Dict[str, int]:
    """Count the rules per status across every disposition."""
    counts = {"total": 0, "ok": 0, "missing": 0, "not_evaluable": 0, "not_applicable": 0}
    for disposition in compliance_details:
        for rule in disposition.get("rules", []):
            counts["total"] += 1
            status = rule.get("status", "")
            if status in counts:
                counts[status] += 1
    return counts


def _fmt_pct(value: Any) -> str:
    """Format the compliance percentage, which may arrive as a number or a string."""
    if value is None:
        return "0%"
    text = str(value)
    return text if text.endswith("%") else f"{text}%"


_HTML_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;700&display=swap');
@page { size: A4; margin: 1.6cm; }
body { font-family: 'Outfit', sans-serif; color: #0f172a; background:#fff; margin:0; padding:0; line-height:1.5; font-size:10pt; }
h1,h2,h3 { color:#0f172a; font-weight:700; margin-top:0; }
h1 { font-size:24pt; border-bottom:3px solid #1e3a8a; padding-bottom:8px; margin-bottom:20px; }
h2 { font-size:16pt; border-bottom:2px solid #1e3a8a; padding-bottom:6px; margin-top:30px; margin-bottom:15px; page-break-after:avoid; }
.running-header { position:fixed; top:-1.15cm; left:0; right:0; font-size:9pt; color:#64748b; }
.running-header .rh-date { float:left; }
.running-header .rh-title { text-align:center; }
.header-table { width:100%; border-collapse:collapse; margin-bottom:25px; }
.header-table td { padding:6px 12px; border:1px solid #1e3a8a; font-size:10pt; font-weight:600; }
.header-table td.value { font-weight:400; color:#0f172a; }
.summary-container { display:flex; flex-wrap:wrap; gap:15px; margin-bottom:30px; }
.summary-card { flex:1; min-width:120px; border:2px solid #1e3a8a; border-radius:8px; padding:15px; text-align:center; background:#f8fafc; }
.summary-card .number { font-size:22pt; font-weight:700; color:#1e3a8a; margin-bottom:5px; }
.summary-card .label { font-size:9pt; font-weight:600; color:#0f172a; text-transform:uppercase; }
.disp-info-card { border:2px solid #1e3a8a; border-radius:6px; padding:12px 16px; margin-bottom:20px; background:#f8fafc; page-break-inside:avoid; }
.disp-info-title { font-size:11pt; font-weight:700; color:#1e3a8a; margin-bottom:8px; }
.disp-line { font-size:9.5pt; margin-bottom:6px; }
.disp-sep { border-top:1px solid #cbd5e1; padding-top:6px; margin-top:6px; }
.disp-stats { margin-bottom:20px; font-size:11pt; font-weight:500; }
.rule-card { border:1px solid #cbd5e1; border-radius:6px; padding:12px 16px; margin-bottom:15px; page-break-inside:avoid; background:#fff; }
.rule-header { display:flex; justify-content:space-between; align-items:baseline; margin-bottom:8px; border-bottom:1px solid #e2e8f0; padding-bottom:4px; }
.rule-title { font-weight:700; font-size:11pt; color:#0f172a; }
.badge { font-size:8pt; font-weight:700; padding:3px 8px; border-radius:4px; text-transform:uppercase; letter-spacing:0.5px; white-space:nowrap; }
.badge-ok { background:#dcfce7; color:#14532d; border:1px solid #15803d; }
.badge-missing { background:#fee2e2; color:#7f1d1d; border:1px solid #b91c1c; }
.badge-not_applicable { background:#f1f5f9; color:#0f172a; border:1px solid #475569; }
.badge-not_evaluable { background:#fef3c7; color:#78350f; border:1px solid #d97706; }
.badge-error { background:#fee2e2; color:#7f1d1d; border:1px solid #b91c1c; }
.rule-meta { font-size:9pt; font-weight:600; margin-bottom:8px; color:#1e3a8a; }
.rule-section { margin-bottom:8px; }
.section-label { font-weight:700; font-size:9pt; color:#1e3a8a; margin-bottom:2px; }
.section-content { font-size:9.5pt; color:#0f172a; }
.evidence-list { margin:5px 0 0 0; padding-left:20px; }
.evidence-item { font-style:italic; color:#0f172a; }
.page-break { page-break-before:always; }
.footer { margin-top:40px; border-top:1px solid #1e3a8a; padding-top:10px; font-size:8pt; text-align:center; font-weight:600; color:#0f172a; }
"""
