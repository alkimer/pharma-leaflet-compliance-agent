"""
Paso 5 — Verificación final (opcional).

Toma todo lo que produjo la corrida y se lo da a Claude para que revise el
trabajo de los agentes anteriores:

    reglas     ← las disposiciones que el paso 3 encontró aplicables
    informe    ← el JSON de cumplimiento del paso 3
    original   ← el texto limpio del paso 2
    adecuado   ← el prospecto que reescribió el paso 4

Devuelve dos cosas: si cada adecuación resuelve de verdad la regla que la
motivó, y qué reglas necesitan que las mire una persona.

Está apagado por defecto. Es el paso más caro de la corrida —una sola llamada
puede costar más que los 100+ pedidos de los pasos 3 y 4 juntos—, así que se
activa a pedido: `--verificar` en la terminal, o el checkbox en la web.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from agents.disposition_classifier import load_disposition
from agents.final_verifier import FinalVerifier, write_outputs
from core import console
from core.run_context import RunContext
from pipeline import step1_rules, step2_prospect, step3_compliance, step4_adequation

logger = logging.getLogger(__name__)

STEP = "paso5_verificacion"

# Etiquetas de consola por veredicto: color y símbolo.
_VEREDICTOS = {
    "correcta": ("ok", "La adecuación es correcta"),
    "correcta_con_observaciones": ("warn", "Correcta, con observaciones"),
    "incorrecta": ("error", "La adecuación NO es correcta"),
}


def run(ctx: RunContext) -> Dict[str, Any]:
    """
    Ejecuta el paso 5 tomando las entradas del manifest de la corrida.

    Returns:
        El informe de verificación, con `output_files`.

    Raises:
        ValueError: Si faltan los artefactos de los pasos 2, 3 o 4.
    """
    report_json = ctx.get_path(step3_compliance.STEP, "report_json")
    prospect_file = ctx.get_path(step2_prospect.STEP, "clean_text_file")
    rules_dir = ctx.get_path(step1_rules.STEP, "rules_dir")
    adequated_files: Dict[str, str] = ctx.get(step4_adequation.STEP, "output_files") or {}
    adequated_txt = adequated_files.get("txt")

    if report_json is None:
        raise ValueError("El paso 3 no dejó el informe JSON; corré el paso 3 primero")
    if prospect_file is None:
        raise ValueError("El paso 2 no dejó el texto limpio del prospecto; corré el paso 2 primero")
    if adequated_txt is None:
        raise ValueError("El paso 4 no dejó el prospecto adecuado; corré el paso 4 primero")
    if rules_dir is None or not rules_dir.is_dir():
        raise ValueError("No se encontró la CARPETA-REGLAS de la corrida; corré el paso 1 primero")

    console.kv("Informe de compliance", report_json)
    console.kv("Prospecto adecuado", adequated_txt)
    console.kv("Salida", ctx.verification_dir)

    report = json.loads(report_json.read_text(encoding="utf-8"))
    rules = _applicable_rules(rules_dir, report)
    console.info(f"Revisando {len(rules)} disposiciones aplicables con el verificador final")

    verifier = FinalVerifier()
    result = verifier.verify(
        rules=rules,
        compliance_report=report,
        original_prospect=prospect_file.read_text(encoding="utf-8-sig"),
        adequated_prospect=_read_adequated(adequated_txt),
    )

    output_files = write_outputs(result, ctx.verification_dir)
    _print_summary(result, output_files)

    ctx.record(
        STEP,
        model=result.get("model"),
        veredicto=result.get("veredicto"),
        confianza=result.get("confianza"),
        resumen=result.get("resumen"),
        adecuaciones_revisadas=len(result.get("adecuaciones") or []),
        requiere_revision_humana=len(result.get("requiere_revision_humana") or []),
        riesgos=len(result.get("riesgos") or []),
        output_files={kind: str(path) for kind, path in output_files.items()},
    )
    return result


def _applicable_rules(rules_dir, report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Carga las disposiciones que el paso 3 verificó.

    Se mandan solo esas y no las 15: el informe únicamente habla de ellas, y el
    resto sería contexto que se paga y no se usa.
    """
    ids = [d.get("disposition_id") for d in report.get("compliance_details", [])]
    disposiciones: List[Dict[str, Any]] = []
    for disposition_id in ids:
        if not disposition_id:
            continue
        disposition = load_disposition(rules_dir, disposition_id)
        if disposition is None:
            console.warn(f"No se encontró el JSON de {disposition_id}; queda fuera de la revisión")
            continue
        disposiciones.append(disposition)
    return disposiciones


def _read_adequated(path: str) -> str:
    """Lee el prospecto adecuado tal cual, con sus marcas de adecuación."""
    from pathlib import Path

    return Path(path).read_text(encoding="utf-8-sig")


def _print_summary(result: Dict[str, Any], output_files: Dict[str, Any]) -> None:
    """Muestra el veredicto y lo que necesita una persona."""
    veredicto = result.get("veredicto", "")
    nivel, titulo = _VEREDICTOS.get(veredicto, ("warn", f"Veredicto desconocido: {veredicto}"))
    confianza = float(result.get("confianza") or 0) * 100

    console.section("Verificación final")
    getattr(console, nivel)(f"{titulo}  (confianza {confianza:.0f}%)")
    if result.get("resumen"):
        console.detail(result["resumen"])

    adecuaciones = result.get("adecuaciones") or []
    # Un [COMPLETAR ACÁ!] bien puesto no es un reparo: es el paso 4 dejándole el
    # hueco a quien tiene el dato.
    pendientes = [a for a in adecuaciones if a.get("evaluacion") == "pendiente_de_dato"]
    problematicas = [
        a for a in adecuaciones
        if a.get("evaluacion") in ("parcial", "no_resuelta", "introduce_error")
    ]
    humanas = result.get("requiere_revision_humana") or []
    riesgos = result.get("riesgos") or []

    console.summary_table(
        [
            ("Adecuaciones revisadas", len(adecuaciones)),
            ("  a completar por una persona", len(pendientes)),
            ("  con reparos", len(problematicas)),
            ("Requieren intervención humana", len(humanas)),
            ("Riesgos señalados", len(riesgos)),
            ("Modelo verificador", result.get("model", "s/d")),
        ]
    )

    for item in problematicas:
        console.status_line(
            "missing" if item.get("evaluacion") in ("no_resuelta", "introduce_error") else "not_evaluable",
            f"{item.get('disposition_id')} · regla {item.get('rule_id')}: {item.get('evaluacion')}",
        )
    for item in humanas:
        console.detail(
            f"⚑ {item.get('disposition_id')} · regla {item.get('rule_id')} "
            f"({item.get('motivo')}): {item.get('que_debe_decidir_la_persona', '')}"
        )

    for kind, path in output_files.items():
        console.ok(f"{kind.upper():<9}{console.path_link(path)}")
