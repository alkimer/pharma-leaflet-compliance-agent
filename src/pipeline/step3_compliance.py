"""
Step 3 — Compliance check.

Runs the compliance graph with:
    leaflet  ← clean text from step 2
    rules    ← RULES FOLDER from step 1 (disposiciones-explotadas/<timestamp>/reglas-extraidas)
    output   → corridas/<timestamp>/resultado  (JSON + MD + HTML + PDF)
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from core import console
from core.run_context import RunContext
from pipeline.compliance_graph import run_compliance_check
from pipeline import step1_rules, step2_prospect

logger = logging.getLogger(__name__)

STEP = "paso3_compliance"


def run(ctx: RunContext) -> Dict[str, Any]:
    """
    Run step 3, taking its inputs from the run's manifest.

    Returns:
        The graph's final state (includes `report` and `report_files`).

    Raises:
        ValueError: If the artifacts of step 1 or 2 are missing.
    """
    rules_dir = ctx.get_path(step1_rules.STEP, "rules_dir") or ctx.rules_dir
    prospect_file = ctx.get_path(step2_prospect.STEP, "clean_text_file")

    if prospect_file is None:
        raise ValueError("El paso 2 no dejó el texto limpio del prospecto; corré el paso 2 primero")
    if not rules_dir.is_dir():
        raise ValueError(f"La CARPETA-REGLAS no existe: {rules_dir}. Corré el paso 1 primero.")

    console.kv("Fecha-hora de la corrida", ctx.stamp, console.BRIGHT_CYAN)

    final_state = run_compliance_check(
        prospect_file_path=prospect_file,
        rules_dir=rules_dir,
        output_dir=ctx.result_dir,
    )

    report_files = final_state.get("report_files", {})
    ctx.record(
        STEP,
        rules_dir=rules_dir,
        prospect_file=prospect_file,
        result_dir=ctx.result_dir,
        report_json=report_files.get("json"),
        report_markdown=report_files.get("markdown"),
        report_html=report_files.get("html"),
        report_pdf=report_files.get("pdf"),
        summary=final_state.get("report", {}).get("summary", {}),
        overall_statistics=final_state.get("report", {}).get("overall_statistics", {}),
        errors=final_state.get("errors", []),
        status="incompleto",
    )

    if "json" not in report_files:
        raise RuntimeError("El paso 3 no generó el informe JSON que necesita el paso 4")

    _fail_on_unusable_report(final_state)

    # Only with a complete report can step 4 adequate over something trustworthy.
    ctx.record(STEP, status="ok")
    return final_state


def _fail_on_unusable_report(final_state: Dict[str, Any]) -> None:
    """
    Last safety net: abort if the report does not reflect a complete analysis.

    Classification and checking failures already abort earlier, inside the graph.
    This covers the rest: if the report ends up empty through some unforeseen path,
    step 4 would conclude that the leaflet "already complies", which is precisely
    the wrong conclusion — nothing was verified, it simply failed.
    """
    errors = final_state.get("errors", [])
    if errors:
        raise RuntimeError(
            f"El paso 3 registró {len(errors)} errores, así que el informe no es confiable. "
            f"Primer error: {errors[0]}"
        )

    if final_state.get("compliance_results"):
        return

    applicable = final_state.get("applicable_dispositions", [])
    if applicable:
        raise RuntimeError(
            f"Hay {len(applicable)} disposiciones aplicables pero ninguna pudo verificarse; "
            f"revisá el log de la corrida."
        )

    console.warn(
        "Ninguna disposición resultó aplicable al prospecto. Revisá que el texto limpio sea "
        "correcto y que la CARPETA-REGLAS tenga las normas esperadas."
    )
