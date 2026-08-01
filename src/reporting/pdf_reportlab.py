"""
Renderer that turns compliance reports into PDF using ReportLab.

It rebuilds the report's look by hand (navy theme, rounded cards, pill-shaped
badges, running header) to produce a high-quality PDF without depending on a
browser or on external libraries.
"""
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.colors import HexColor, Color
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
    KeepTogether, Flowable,
)

logger = logging.getLogger(__name__)

# ---- Palette (same one as the HTML report) ----
NAVY = HexColor("#1e3a8a")
SLATE = HexColor("#0f172a")
CARD_BORDER = HexColor("#cbd5e1")
LIGHT_BG = HexColor("#f8fafc")
MUTED = HexColor("#64748b")
DIVIDER = HexColor("#e2e8f0")
WHITE = HexColor("#ffffff")

# ---- Per-status colours (background, border, text) ----
BADGE_COLORS = {
    "ok":             ("#dcfce7", "#15803d", "#14532d"),
    "missing":        ("#fee2e2", "#b91c1c", "#7f1d1d"),
    "not_applicable": ("#f1f5f9", "#475569", "#0f172a"),
    "not_evaluable":  ("#fef3c7", "#d97706", "#78350f"),
    "ready":          ("#e0f2fe", "#0284c7", "#0c4a6e"),
    "error":          ("#fee2e2", "#b91c1c", "#7f1d1d"),
}

STATUS_TEXT = {
    "ok": "CUMPLE", "missing": "INCUMPLE", "not_applicable": "NO APLICA",
    "not_evaluable": "NO EVALUABLE", "ready": "LISTO", "error": "ERROR",
}

# Number/label colour of each summary card
SUMMARY_ACCENT = {
    "total": NAVY, "ok": HexColor("#15803d"), "missing": HexColor("#b91c1c"),
    "not_evaluable": HexColor("#d97706"), "not_applicable": HexColor("#475569"),
}


def _style(name, **kw):
    base = dict(fontName="Helvetica", fontSize=9.5, leading=13, textColor=SLATE)
    base.update(kw)
    return ParagraphStyle(name, **base)


STYLES = {
    "h1": _style("h1", fontName="Helvetica-Bold", fontSize=22, leading=26, textColor=SLATE),
    "h2": _style("h2", fontName="Helvetica-Bold", fontSize=15, leading=19, textColor=SLATE),
    "card_title": _style("card_title", fontName="Helvetica-Bold", fontSize=11, leading=15, textColor=NAVY),
    "rule_title": _style("rule_title", fontName="Helvetica-Bold", fontSize=10.5, leading=14, textColor=SLATE),
    "normal": _style("normal"),
    "small": _style("small", fontSize=9.5, leading=13),
    "meta": _style("meta", fontName="Helvetica-Bold", fontSize=9, leading=12, textColor=NAVY),
    "label": _style("label", fontName="Helvetica-Bold", fontSize=9, leading=12, textColor=NAVY),
    "evidence": _style("evidence", fontName="Helvetica-Oblique", fontSize=9.5, leading=13, leftIndent=10),
    "bold": _style("bold", fontName="Helvetica-Bold"),
    "hdr_key": _style("hdr_key", fontName="Helvetica-Bold", fontSize=9.5, leading=12),
    "hdr_val": _style("hdr_val", fontSize=9.5, leading=12),
    "sum_num": _style("sum_num", fontName="Helvetica-Bold", fontSize=20, leading=22, alignment=TA_CENTER),
    "sum_label": _style("sum_label", fontName="Helvetica-Bold", fontSize=7.5, leading=10, alignment=TA_CENTER, textColor=SLATE),
    "badge": _style("badge", fontName="Helvetica-Bold", fontSize=7.5, leading=10, alignment=TA_CENTER),
    "footer": _style("footer", fontName="Helvetica-Bold", fontSize=8, leading=11, alignment=TA_CENTER, textColor=SLATE),
}


class HRule(Flowable):
    """Horizontal rule (used to underline the headings, like the HTML report)."""

    def __init__(self, width, thickness=2, color=NAVY, space_before=2, space_after=10):
        super().__init__()
        self.width = width
        self.thickness = thickness
        self.color = color
        self.space_before = space_before
        self.space_after = space_after
        self.height = thickness + space_before + space_after

    def wrap(self, availWidth, availHeight):
        self.width = availWidth
        return availWidth, self.height

    def draw(self):
        self.canv.setStrokeColor(self.color)
        self.canv.setLineWidth(self.thickness)
        y = self.space_after
        self.canv.line(0, y, self.width, y)


def _esc(val: Any) -> str:
    """Escape text for ReportLab Paragraph's mini-markup."""
    if val is None:
        return ""
    s = str(val)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _fmt_pct(value) -> str:
    if value is None:
        return "0%"
    s = str(value)
    return s if s.endswith("%") else f"{s}%"


def _badge(status: str) -> Table:
    """Build the rule's status badge (coloured rounded pill)."""
    bg, border, txt = BADGE_COLORS.get(status, BADGE_COLORS["not_applicable"])
    label = STATUS_TEXT.get(status, status.upper())
    p = Paragraph(_esc(label), _style("badge_txt", fontName="Helvetica-Bold",
                                      fontSize=7.5, leading=10, alignment=TA_CENTER,
                                      textColor=HexColor(txt)))
    t = Table([[p]], colWidths=[len(label) * 5.2 + 16])
    t.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.8, HexColor(border)),
        ("ROUNDEDCORNERS", [5, 5, 5, 5]),
        ("BACKGROUND", (0, 0), (-1, -1), HexColor(bg)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _card(inner: List, width: float, border=CARD_BORDER, bg=WHITE,
          border_w=1, radius=6, pad=12) -> Table:
    """Wrap a list of flowables in a rounded card."""
    t = Table([[inner]], colWidths=[width])
    t.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), border_w, border),
        ("ROUNDEDCORNERS", [radius, radius, radius, radius]),
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), pad),
        ("BOTTOMPADDING", (0, 0), (-1, -1), pad),
        ("LEFTPADDING", (0, 0), (-1, -1), pad + 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), pad + 2),
    ]))
    return t


class ReportLabReport:
    """Builds the compliance report's PDF with ReportLab."""

    def __init__(self, disp_metadata_map: Optional[Dict[str, Dict[str, str]]] = None):
        self.disp_metadata_map = disp_metadata_map or {}
        self.title = "Reporte de Adecuación de Prospecto"
        self.header_date = ""

    # ---- Running header / footer on every page ----
    def _on_page(self, canvas, doc):
        canvas.saveState()
        w, h = A4
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(1.6 * cm, h - 1.0 * cm, self.header_date)
        canvas.drawCentredString(w / 2.0, h - 1.0 * cm, self.title)
        # Footer: page number
        canvas.drawRightString(w - 1.6 * cm, 1.0 * cm, str(canvas.getPageNumber()))
        canvas.restoreState()

    def build(self, json_data: Dict[str, Any], pdf_path: str) -> str:
        metadata = json_data.get("metadata", {})
        summary = json_data.get("summary", {})
        compliance_details = json_data.get("compliance_details", [])
        classification_results = json_data.get("classification_results", [])

        self.header_date = metadata.get("analysis_date", "") or datetime.now().strftime("%d/%m/%y, %H:%M")

        classification_map, non_applicable = {}, []
        for res in classification_results:
            disp_id = res.get("disposition_id")
            if not disp_id:
                continue
            classification_map[disp_id] = {"applies": res.get("applies", False), "reason": res.get("reason", "")}
            if not res.get("applies", False):
                non_applicable.append({"disposition_id": disp_id, "reason": res.get("reason", "")})

        doc = SimpleDocTemplate(
            pdf_path, pagesize=A4,
            leftMargin=1.6 * cm, rightMargin=1.6 * cm,
            topMargin=1.6 * cm, bottomMargin=1.6 * cm,
            title=self.title,
        )
        W = doc.width  # content width

        story: List = []

        # ---- Title + header table ----
        story.append(Paragraph(self.title, STYLES["h1"]))
        story.append(HRule(W, thickness=3, space_after=14))
        story.append(self._header_table(metadata, summary, W))
        story.append(Spacer(1, 16))

        # ---- Overall summary ----
        story.append(Paragraph("Resumen General de Cumplimiento", STYLES["h2"]))
        story.append(HRule(W, thickness=2, space_after=12))
        story.append(self._summary_cards(compliance_details, W))

        # ---- Detail per disposition ----
        for disp in compliance_details:
            story.append(PageBreak())
            story.extend(self._disposition_section(disp, classification_map, W))

        # ---- Non-applicable ones ----
        if non_applicable:
            story.append(PageBreak())
            story.extend(self._non_applicable_section(non_applicable, W))

        story.append(Spacer(1, 20))
        story.append(HRule(W, thickness=1, color=NAVY, space_after=6))
        story.append(Paragraph("Documento de conformidad generado automáticamente.", STYLES["footer"]))

        doc.build(story, onFirstPage=self._on_page, onLaterPages=self._on_page)
        logger.info(f"✅ PDF (ReportLab) generado: {pdf_path}")
        return pdf_path

    # ---- Components ----
    def _header_table(self, metadata, summary, W):
        def cell(txt, style):
            return Paragraph(_esc(txt), STYLES[style])

        prospect = str(metadata.get("prospect_name", "")).replace("_", " ")
        data = [
            [cell("PRODUCTO ANALIZADO:", "hdr_key"), cell(prospect, "hdr_val"),
             cell("FECHA DE ANÁLISIS:", "hdr_key"), cell(metadata.get("analysis_date", ""), "hdr_val")],
            [cell("DISPOSICIONES EVALUADAS:", "hdr_key"), cell(summary.get("total_dispositions_evaluated", 0), "hdr_val"),
             cell("DISPOSICIONES APLICABLES:", "hdr_key"), cell(summary.get("applicable_dispositions", 0), "hdr_val")],
            [cell("REGLAS VERIFICADAS:", "hdr_key"), cell(summary.get("total_rules_checked", 0), "hdr_val"),
             cell("ESTADO GENERAL:", "hdr_key"), Paragraph("<b>Verificación Completa</b>", STYLES["hdr_val"])],
        ]
        col = W / 4.0
        t = Table(data, colWidths=[col * 1.15, col * 0.85, col * 1.15, col * 0.85])
        t.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 1, NAVY),
            ("BACKGROUND", (0, 0), (0, -1), LIGHT_BG),
            ("BACKGROUND", (2, 0), (2, -1), LIGHT_BG),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ]))
        return t

    def _summary_cards(self, compliance_details, W):
        counts = {"total": 0, "ok": 0, "missing": 0, "not_evaluable": 0, "not_applicable": 0}
        for disp in compliance_details:
            for r in disp.get("rules", []):
                counts["total"] += 1
                st = r.get("status", "")
                if st in counts:
                    counts[st] += 1

        cards_spec = [
            ("total", "Total Reglas"), ("ok", "Cumplen"), ("missing", "Incumplen"),
            ("not_evaluable", "No Evaluables"), ("not_applicable", "No Aplican"),
        ]
        gap = 10
        cw = (W - gap * 4) / 5.0
        cells = []
        for key, label in cards_spec:
            accent = SUMMARY_ACCENT[key]
            num = Paragraph(str(counts[key]),
                            _style("n", fontName="Helvetica-Bold", fontSize=20, leading=22,
                                   alignment=TA_CENTER, textColor=accent))
            lbl = Paragraph(label.upper(),
                            _style("l", fontName="Helvetica-Bold", fontSize=7.5, leading=10,
                                   alignment=TA_CENTER, textColor=accent))
            inner = Table([[num], [lbl]], colWidths=[cw - 4])
            inner.setStyle(TableStyle([
                ("BOX", (0, 0), (-1, -1), 2, accent),
                ("ROUNDEDCORNERS", [8, 8, 8, 8]),
                ("BACKGROUND", (0, 0), (-1, -1), LIGHT_BG),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("TOPPADDING", (0, 0), (-1, 0), 14),
                ("BOTTOMPADDING", (0, 1), (-1, 1), 12),
                ("TOPPADDING", (0, 1), (-1, 1), 2),
            ]))
            cells.append(inner)

        # Outer table that spaces the cards apart
        row, widths = [], []
        for i, c in enumerate(cells):
            row.append(c)
            widths.append(cw)
            if i < len(cells) - 1:
                row.append("")
                widths.append(gap)
        outer = Table([row], colWidths=widths)
        outer.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        return outer

    def _disposition_section(self, disp, classification_map, W):
        out = []
        disp_id = disp.get("disposition_id", "")
        out.append(Paragraph(f"Disposición: {_esc(disp_id)}", STYLES["h2"]))
        out.append(HRule(W, thickness=2, space_after=12))

        classif = classification_map.get(disp_id, {})
        applies_text = "SÍ" if classif.get("applies", True) else "NO"
        reason_text = classif.get("reason", "N/D")

        extra = self.disp_metadata_map.get(disp_id)
        if extra:
            inner = [
                Paragraph(_esc(extra.get("title")), STYLES["card_title"]),
                Spacer(1, 4),
                Paragraph(f"<b>Tipo:</b> {_esc(extra.get('source_type'))} &nbsp;|&nbsp; "
                          f"<b>Condición de venta:</b> {_esc(extra.get('sale_condition') or 'N/A')}", STYLES["small"]),
                Spacer(1, 3),
                Paragraph(f"<b>Objetivo:</b> {_esc(extra.get('objective'))}", STYLES["small"]),
                Spacer(1, 6),
                HRule(W, thickness=0.6, color=CARD_BORDER, space_before=0, space_after=6),
                Paragraph(f"<b>Aplica:</b> {_esc(applies_text)}", STYLES["small"]),
                Paragraph(f"<b>Motivo de aplicación:</b> {_esc(reason_text)}", STYLES["small"]),
            ]
            out.append(_card(inner, W, border=NAVY, bg=LIGHT_BG, border_w=2))
            out.append(Spacer(1, 14))

        out.append(Paragraph(
            f"<b>Porcentaje de cumplimiento:</b> {_esc(_fmt_pct(disp.get('compliance_percentage')))}<br/>"
            f"<b>Total de reglas evaluadas:</b> {_esc(disp.get('total_rules', 0))}",
            _style("pct", fontSize=11, leading=15)))
        out.append(Spacer(1, 12))

        order = {"missing": 0, "ok": 1, "ready": 2, "not_evaluable": 3}
        rules = sorted(disp.get("rules", []), key=lambda r: order.get(r.get("status", ""), 4))
        for rule in rules:
            out.append(self._rule_card(rule, W))
            out.append(Spacer(1, 10))
        return out

    def _rule_card(self, rule, W):
        rule_id = rule.get("rule_id")
        rule_label = f"Regla {rule_id}" if rule_id is not None else "Regla Especial"
        status = rule.get("status", "")
        inner_w = W - 28  # usable width inside the card (~14 of padding per side)

        # Header: title (left) + badge (right)
        title_p = Paragraph(f"{_esc(rule_label)}: {_esc(rule.get('objective', ''))}", STYLES["rule_title"])
        badge = _badge(status)
        badge_w = 95
        header = Table([[title_p, badge]], colWidths=[inner_w - badge_w, badge_w])
        header.setStyle(TableStyle([
            ("VALIGN", (0, 0), (0, 0), "MIDDLE"),
            ("VALIGN", (1, 0), (1, 0), "TOP"),
            ("ALIGN", (1, 0), (1, 0), "RIGHT"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ("LINEBELOW", (0, 0), (-1, -1), 0.6, DIVIDER),
        ]))

        inner = [header, Spacer(1, 6)]

        attach = rule.get("attach_reference") or []
        attach_str = " | Anexos: " + _esc(", ".join(attach)) if attach else ""
        inner.append(Paragraph(f"Referencia legal: {_esc(rule.get('article_reference', 'N/D'))}{attach_str}", STYLES["meta"]))

        def section(label, content, bold=False):
            inner.append(Spacer(1, 4))
            inner.append(Paragraph(_esc(label), STYLES["label"]))
            st = STYLES["bold"] if bold else STYLES["normal"]
            inner.append(Paragraph(_esc(content), st))

        if rule.get("verification_procedure"):
            section("Procedimiento de verificación:", rule["verification_procedure"])
        if rule.get("acceptance_criteria"):
            section("Criterio de aceptación:", rule["acceptance_criteria"])
        if rule.get("evidence_snippets"):
            inner.append(Spacer(1, 4))
            inner.append(Paragraph("Evidencia encontrada:", STYLES["label"]))
            for snip in rule["evidence_snippets"]:
                inner.append(Paragraph(f"•&nbsp; {_esc(snip)}", STYLES["evidence"]))
        if rule.get("checker_notes"):
            section("Notas del verificador:", rule["checker_notes"], bold=True)
        if rule.get("must_include_phrases"):
            inner.append(Spacer(1, 4))
            inner.append(Paragraph("Frases obligatorias:", STYLES["label"]))
            for phrase in rule["must_include_phrases"]:
                inner.append(Paragraph(f"•&nbsp; {_esc(phrase)}", STYLES["evidence"]))

        # KeepTogether stops a card from breaking awkwardly across pages
        return KeepTogether(_card(inner, W, border=CARD_BORDER, bg=WHITE, border_w=1, pad=12))

    def _non_applicable_section(self, non_applicable, W):
        out = [Paragraph("Disposiciones y Circulares No Aplicables", STYLES["h2"]),
               HRule(W, thickness=2, space_after=12),
               Paragraph("A continuación se listan las normativas evaluadas que no aplican al prospecto "
                         "bajo análisis y el motivo correspondiente.", _style("na", fontSize=11, leading=15)),
               Spacer(1, 14)]
        for item in non_applicable:
            d_id = item["disposition_id"]
            extra = self.disp_metadata_map.get(d_id, {})
            inner = [
                Paragraph(f"{_esc(d_id)}: {_esc(extra.get('title', 'N/D'))}",
                          _style("na_title", fontName="Helvetica-Bold", fontSize=11, leading=15, textColor=HexColor("#475569"))),
                Spacer(1, 4),
                Paragraph(f"<b>Tipo:</b> {_esc(extra.get('source_type', 'N/D'))} &nbsp;|&nbsp; <b>Aplica:</b> NO", STYLES["small"]),
                Spacer(1, 4),
                HRule(W, thickness=0.6, color=CARD_BORDER, space_before=0, space_after=6),
                Paragraph(f"<b>Motivo de exclusión:</b> {_esc(item['reason'])}", STYLES["small"]),
            ]
            out.append(_card(inner, W, border=HexColor("#475569"), bg=LIGHT_BG, border_w=2))
            out.append(Spacer(1, 12))
        return out


def build_pdf(json_data: Dict[str, Any], pdf_path: str,
              disp_metadata_map: Optional[Dict[str, Dict[str, str]]] = None) -> str:
    """Entry point: generate the report's PDF with ReportLab."""
    return ReportLabReport(disp_metadata_map).build(json_data, str(pdf_path))
