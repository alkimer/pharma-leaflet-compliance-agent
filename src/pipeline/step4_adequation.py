"""
Step 4 — Leaflet adequation.

Takes step 3's compliance report and step 2's clean text, and generates the
adequated leaflet in `corridas/<timestamp>/documento-adecuado` (JSON with the full
trace, readable TXT and DOCX with the adequations highlighted).
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from agents.prospect_adequator import ProspectAdequator
from core import console
from core.run_context import RunContext
from pipeline import step2_prospect, step3_compliance

logger = logging.getLogger(__name__)

STEP = "paso4_adecuacion"


def run(ctx: RunContext) -> Dict[str, Any]:
    """
    Run step 4, taking its inputs from the run's manifest.

    Returns:
        The adequation result, with `output_files`.

    Raises:
        ValueError: If the artifacts of step 2 or 3 are missing, or if step 3 did
            not finish cleanly (adequating over an incomplete report would lead to
            concluding that the leaflet already complies when in fact nothing was
            verified).
    """
    report_json = ctx.get_path(step3_compliance.STEP, "report_json")
    prospect_file = ctx.get_path(step2_prospect.STEP, "clean_text_file")

    if report_json is None:
        raise ValueError("El paso 3 no dejó el informe JSON; corré el paso 3 primero")
    if prospect_file is None:
        raise ValueError("El paso 2 no dejó el texto limpio del prospecto; corré el paso 2 primero")

    status = ctx.get(step3_compliance.STEP, "status")
    if status != "ok":
        raise ValueError(
            f"El paso 3 no terminó correctamente (estado: {status or 'desconocido'}), así que su "
            f"informe no sirve para adecuar. Volvé a correr el paso 3 de esta corrida: "
            f"python run_pipeline.py --corrida {ctx.stamp} --desde 3"
        )

    console.kv("Informe de compliance", report_json)
    console.kv("Prospecto original", prospect_file)
    console.kv("Salida", ctx.adequated_dir)

    adequator = ProspectAdequator()
    result = adequator.run(
        compliance_report_path=report_json,
        prospect_path=prospect_file,
        output_dir=ctx.adequated_dir,
    )

    summary = result["summary"]
    console.summary_table(
        [
            ("Reglas adecuadas", summary["total_missing_rules"]),
            ("Disposiciones involucradas", summary["total_dispositions"]),
            ("Se aplicaron cambios", "sí" if summary["adequation_performed"] else "no"),
            ("Carpeta de salida", ctx.adequated_dir),
        ],
        title="Resumen del paso 4",
    )

    ctx.record(
        STEP,
        compliance_report=report_json,
        prospect_file=prospect_file,
        output_dir=ctx.adequated_dir,
        summary=summary,
        output_files=result.get("output_files", {}),
    )
    return result
