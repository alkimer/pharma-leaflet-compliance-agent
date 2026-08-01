"""
Regulatory compliance graph (step 3), built with LangGraph.

Flow: load leaflet → classify applicable dispositions → check rules → generate
report (JSON + Markdown + HTML + PDF).

Paths are explicit: `run_compliance_check` receives the leaflet, the RULES FOLDER
and the output directory. There is no global state and no hardcoded paths; the
pipeline (`pipeline.step3_compliance`) hands it the run's paths.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, TypedDict

from langgraph.graph import END, StateGraph

from agents.compliance_checker import ComplianceChecker
from agents.disposition_classifier import DispositionClassifier, load_disposition
from core import console
from reporting.report_generator import ComplianceReportGenerator

logger = logging.getLogger(__name__)

# Compliance statuses counted in the report.
STATUS_KEYS = ("ok", "not_applicable", "not_evaluable", "missing", "error")


class ComplianceState(TypedDict):
    """State that flows through the graph."""

    # Input
    prospect_file_path: str
    rules_dir: str
    output_dir: str

    # Leaflet
    prospect_text: str
    prospect_name: str

    # Classification
    classifications: List[Dict[str, Any]]
    applicable_dispositions: List[str]

    # Checking
    compliance_results: List[Dict[str, Any]]

    # Report
    report: Dict[str, Any]
    report_files: Dict[str, str]

    # Metadata
    timestamp: str
    errors: List[str]


# ============================================================================
# Nodes
# ============================================================================

def load_prospect_node(state: ComplianceState) -> ComplianceState:
    """Node 1: load the leaflet text already converted in step 2."""
    console.section("Nodo 1/4 · Cargando el prospecto")

    prospect_file = Path(state["prospect_file_path"])
    if not prospect_file.exists():
        raise FileNotFoundError(f"Prospecto no encontrado: {prospect_file}")

    prospect_text = prospect_file.read_text(encoding="utf-8-sig")
    console.ok(f"{prospect_file.name}: {len(prospect_text)} caracteres")

    return {
        **state,
        "prospect_text": prospect_text,
        "prospect_name": prospect_file.stem,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }


def classify_dispositions_node(state: ComplianceState) -> ComplianceState:
    """Node 2: work out which dispositions apply to the leaflet."""
    console.section("Nodo 2/4 · Clasificando disposiciones aplicables")

    # No try/except on purpose: if a disposition could not be classified (retries
    # included), the analysis is incomplete and the run has to fail instead of
    # carrying on with a partial report.
    classifier = DispositionClassifier(rules_dir=Path(state["rules_dir"]))
    results = classifier.classify(state["prospect_text"])

    # Keep the applicable ids, dropping duplicates.
    applicable: List[str] = []
    for classification in results["classifications"]:
        if classification.get("applies") is not True:
            continue
        disposition_id = classification.get("disposition_id")
        if not disposition_id or disposition_id == "UNKNOWN":
            raise ValueError(
                f"El clasificador marcó una disposición como aplicable pero con un id inválido "
                f"({disposition_id!r}); no se puede verificar."
            )
        if disposition_id in applicable:
            console.warn(f"Disposición aplicable duplicada, se omite: {disposition_id}")
            continue
        applicable.append(disposition_id)

    console.summary_table(
        [
            ("Disposiciones evaluadas", len(results["classifications"])),
            ("Disposiciones aplicables", len(applicable)),
        ]
    )
    for disposition_id in applicable:
        console.detail(f"→ {disposition_id}")

    return {
        **state,
        "classifications": results["classifications"],
        "applicable_dispositions": applicable,
    }


def check_compliance_node(state: ComplianceState) -> ComplianceState:
    """Node 3: check the applicable dispositions rule by rule."""
    console.section("Nodo 3/4 · Verificando cumplimiento")

    applicable = state.get("applicable_dispositions", [])
    if not applicable:
        console.warn("No hay disposiciones aplicables para verificar")
        return {**state, "compliance_results": []}

    rules_dir = Path(state["rules_dir"])
    checker = ComplianceChecker()
    compliance_results: List[Dict[str, Any]] = []

    # Same as with classification: an applicable disposition that cannot be
    # checked aborts the run, it is not skipped.
    for idx, disposition_id in enumerate(applicable, start=1):
        console.progress(idx, len(applicable), f"Disposición {disposition_id}")

        disposition = load_disposition(rules_dir, disposition_id)
        if not disposition:
            raise FileNotFoundError(
                f"La disposición {disposition_id} se clasificó como aplicable pero no se pudo "
                f"cargar desde {rules_dir}"
            )

        result = checker.check(
            prospect_text=state["prospect_text"],
            disposition=disposition,
        )
        compliance_results.append(result)

        counts: Dict[str, int] = {}
        for evaluation in result["evaluations"]:
            status = evaluation.get("status", "unknown")
            counts[status] = counts.get(status, 0) + 1
        console.detail(f"{disposition_id}: {counts}")

    console.ok(f"{len(compliance_results)} disposiciones verificadas")
    return {**state, "compliance_results": compliance_results}


def generate_report_node(state: ComplianceState) -> ComplianceState:
    """Node 4: assemble the report and write it as JSON, Markdown, HTML and PDF."""
    console.section("Nodo 4/4 · Generando el informe")

    compliance_results = state.get("compliance_results", [])
    classifications = state.get("classifications", [])

    report: Dict[str, Any] = {
        "metadata": {
            "prospect_name": state["prospect_name"],
            "prospect_file": state["prospect_file_path"],
            "timestamp": state["timestamp"],
            "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
        "summary": {
            "total_dispositions_evaluated": len(classifications),
            "applicable_dispositions": len(state.get("applicable_dispositions", [])),
            "verified_dispositions": len(compliance_results),
            "total_rules_checked": sum(r["total_rules"] for r in compliance_results),
        },
        "classification_results": classifications,
        "compliance_details": [],
        "overall_statistics": {},
        "errors": state.get("errors", []),
    }

    overall = {key: 0 for key in STATUS_KEYS}

    for compliance_result in compliance_results:
        per_disposition = {key: 0 for key in STATUS_KEYS}
        rules_details = []

        for evaluation in compliance_result["evaluations"]:
            status = evaluation.get("status", "unknown")
            if status in per_disposition:
                per_disposition[status] += 1
                overall[status] += 1

            rules_details.append({
                "rule_id": evaluation.get("rule_id"),
                "objective": evaluation.get("objective", ""),
                "verification_procedure": evaluation.get("verification_procedure", ""),
                "acceptance_criteria": evaluation.get("acceptance_criteria", ""),
                "status": status,
                "evidence_snippets": evaluation.get("evidence_snippets", []),
                "checker_notes": evaluation.get("checker_notes", ""),
                "must_include_phrases": evaluation.get("must_include_phrases", []),
                "article_reference": evaluation.get("article_reference", ""),
                "attach_reference": evaluation.get("attach_reference", []),
            })

        total_rules = compliance_result["total_rules"]
        report["compliance_details"].append({
            "disposition_id": compliance_result["disposition_id"],
            "total_rules": total_rules,
            "status_summary": per_disposition,
            "compliance_percentage": (
                round(per_disposition["ok"] / total_rules * 100, 2) if total_rules else 0
            ),
            "rules": rules_details,
        })

    report["overall_statistics"] = overall

    generator = ComplianceReportGenerator(
        output_dir=state["output_dir"],
        dispositions_dir=state["rules_dir"],
    )
    generated = generator.generate_reports(report, state["prospect_name"])

    console.summary_table(
        [
            ("Reglas verificadas", report["summary"]["total_rules_checked"]),
            ("Cumplidas (ok)", overall["ok"]),
            ("No cumplidas (missing)", overall["missing"]),
            ("No aplicables", overall["not_applicable"]),
            ("No evaluables", overall["not_evaluable"]),
            ("Errores", overall["error"]),
        ],
        title="Resultado del análisis",
    )
    for kind, path in generated.items():
        console.ok(f"{kind.upper():<9}{console.path_link(path)}")

    return {
        **state,
        "report": report,
        "report_files": {kind: str(path) for kind, path in generated.items()},
    }


# ============================================================================
# Graph
# ============================================================================

def create_compliance_graph():
    """Compile the graph: load → classify → check → report."""
    workflow = StateGraph(ComplianceState)

    workflow.add_node("load_prospect", load_prospect_node)
    workflow.add_node("classify_dispositions", classify_dispositions_node)
    workflow.add_node("check_compliance", check_compliance_node)
    workflow.add_node("generate_report", generate_report_node)

    workflow.set_entry_point("load_prospect")
    workflow.add_edge("load_prospect", "classify_dispositions")
    workflow.add_edge("classify_dispositions", "check_compliance")
    workflow.add_edge("check_compliance", "generate_report")
    workflow.add_edge("generate_report", END)

    return workflow.compile()


def run_compliance_check(
    prospect_file_path: str | Path,
    rules_dir: str | Path,
    output_dir: str | Path,
) -> Dict[str, Any]:
    """
    Run the complete compliance check.

    Args:
        prospect_file_path: Clean text of the leaflet (step 2's output).
        rules_dir: RULES FOLDER with the disposition JSONs (step 1's output).
        output_dir: Where to write the report (corridas/<timestamp>/resultado).

    Returns:
        The graph's final state, with `report` and `report_files`.

    Raises:
        FileNotFoundError: If the leaflet or the rules folder do not exist.
        ValueError: If the rules folder holds no JSON at all.
    """
    prospect_path = Path(prospect_file_path)
    rules_path = Path(rules_dir)
    output_path = Path(output_dir)

    if not prospect_path.exists():
        raise FileNotFoundError(f"Prospecto no encontrado: {prospect_path}")
    if not rules_path.is_dir():
        raise FileNotFoundError(f"Carpeta de reglas no encontrada: {rules_path}")
    if not any(rules_path.glob("*.json")):
        raise ValueError(f"La carpeta de reglas no tiene archivos JSON: {rules_path}")

    output_path.mkdir(parents=True, exist_ok=True)

    console.kv("Prospecto", prospect_path)
    console.kv("Carpeta de reglas", rules_path)
    console.kv("Salida del informe", output_path)

    initial_state: ComplianceState = {
        "prospect_file_path": str(prospect_path),
        "rules_dir": str(rules_path),
        "output_dir": str(output_path),
        "prospect_text": "",
        "prospect_name": "",
        "classifications": [],
        "applicable_dispositions": [],
        "compliance_results": [],
        "report": {},
        "report_files": {},
        "timestamp": "",
        "errors": [],
    }

    final_state = create_compliance_graph().invoke(initial_state)

    if final_state.get("errors"):
        console.warn(f"Se registraron {len(final_state['errors'])} errores durante la ejecución")
        for error in final_state["errors"]:
            console.detail(error)

    return final_state


def main() -> None:
    """Direct execution of step 3, handy for reprocessing without the pipeline."""
    import argparse

    from core.console import setup_logging

    parser = argparse.ArgumentParser(description="Verificación de cumplimiento normativo (paso 3)")
    parser.add_argument("--prospecto", required=True, help="Ruta al .md/.txt del prospecto")
    parser.add_argument("--reglas", required=True, help="CARPETA-REGLAS con los JSON")
    parser.add_argument("--salida", required=True, help="Directorio de salida del informe")
    args = parser.parse_args()

    setup_logging()
    console.banner("Verificación de cumplimiento normativo", "paso 3 en modo directo")
    run_compliance_check(args.prospecto, args.reglas, args.salida)


if __name__ == "__main__":
    main()
