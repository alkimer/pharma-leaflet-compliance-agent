#!/usr/bin/env python
"""
Pruebas de integración por paso, contra las APIs reales.

A diferencia de `smoke_pipeline.py` —que simula las respuestas del LLM y no
gasta un token—, acá se llama de verdad a OpenAI y a Claude. Por eso todo es
mínimo: UNA disposición con UNA regla, un prospecto de 20 líneas y un adecuado
igual de corto. Una corrida completa de las cinco cuesta centavos.

La gracia es poder probar un paso sin ejecutar los anteriores: cada uno arranca
de fixtures fijos (`tests/integracion/fixtures/`), derivados de corridas reales
pero recortados. Así, si se toca el prompt del checker, se prueba el paso 3 solo.

Uso:
    python tests/integracion.py                 # todos los pasos
    python tests/integracion.py --paso 3        # solo el paso 3
    python tests/integracion.py --paso 3 4 5    # varios
    python tests/integracion.py --listar

Cada paso deja sus artefactos en una corrida temporal que se borra al terminar
(`--conservar` para inspeccionarla).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from core import console  # noqa: E402
from core.config import Settings, settings as real_settings  # noqa: E402
from core.console import setup_logging  # noqa: E402
from core.run_context import RunContext  # noqa: E402
from core.usage import tracker  # noqa: E402

FIXTURES = Path(__file__).parent / "integracion" / "fixtures"
DISPOSITION_ID = "ANMAT_TEST_9001_2026"
FRASE_EXIGIDA = "ANTE LA MENOR DUDA CONSULTE A SU MÉDICO"

_fallos: List[str] = []


def _check(condition: bool, message: str) -> bool:
    """Registra un chequeo. No corta: interesa ver todos los fallos del paso."""
    if condition:
        console.ok(message)
    else:
        console.error(message)
        _fallos.append(message)
    return condition


def _leer_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _contexto(workdir: Path, stamp: str) -> RunContext:
    """Corrida aislada en el directorio temporal, con las reglas de los fixtures."""
    ajustes = Settings(
        openai_api_key=real_settings.openai_api_key,
        anthropic_api_key=real_settings.anthropic_api_key,
        exploded_dir=workdir / "disposiciones-explotadas",
        corridas_dir=workdir / "corridas",
        base_rules_dir=FIXTURES / "reglas",
        dispositions_sources_dir=FIXTURES,
        verifier=real_settings.verifier,
        rules_generator=real_settings.rules_generator,
        classifier=real_settings.classifier,
        checker=real_settings.checker,
        adequator=real_settings.adequator,
    )
    return RunContext(stamp=stamp, settings=ajustes).prepare()


# ============================================================================
# Paso 1 — disposición → reglas JSON
# ============================================================================

def paso1(workdir: Path) -> None:
    """Genera reglas de una disposición de cuatro artículos y valida el schema."""
    from agents.rules_generator import RulesGenerator, derive_disposition_id
    from etl.document_text import extract_text

    ctx = _contexto(workdir, "20260101-0001")
    generator = RulesGenerator()
    resultado = generator.generate_batch(
        documents=[FIXTURES / "disposicion_minima.md"],
        output_dir=ctx.rules_dir,
        text_loader=extract_text,
    )

    _check(not resultado["failed"], "no falló ninguna disposición")
    if not _check(len(resultado["generated"]) == 1, "generó un archivo de reglas"):
        return

    generado = _leer_json(Path(resultado["generated"][0]["rules_file"]))
    _check("rules" in generado and len(generado["rules"]) >= 1, "el JSON trae al menos una regla")
    _check(
        all(
            {"objective", "verification_procedure", "acceptance_criteria"} <= set(r)
            for r in generado["rules"]
        ),
        "las reglas traen el schema que consume el checker",
    )
    _check(
        derive_disposition_id(FIXTURES / "disposicion_minima.md") == generado["disposition_id"],
        "el disposition_id sale del nombre del archivo",
    )
    texto = json.dumps(generado, ensure_ascii=False).upper()
    _check(
        "CONSULTE A SU MÉDICO" in texto,
        "la regla extraída menciona la leyenda que exige el artículo 1º",
    )


# ============================================================================
# Paso 2 — prospecto → texto limpio  (sin API)
# ============================================================================

def paso2(workdir: Path) -> None:
    """Extrae el texto del prospecto. Es el único paso que no llama a ningún modelo."""
    from pipeline import step2_prospect

    ctx = _contexto(workdir, "20260101-0002")
    limpio = step2_prospect.run(
        ctx, prospect_path=FIXTURES / "prospecto_minimo.md", interactive=False
    )

    _check(limpio.exists(), "escribió el texto limpio")
    contenido = limpio.read_text(encoding="utf-8")
    _check("TESTAMOL" in contenido, "el texto limpio conserva el contenido del prospecto")
    _check(
        FRASE_EXIGIDA not in contenido,
        "el prospecto de prueba NO trae la frase exigida (es lo que el paso 4 debe agregar)",
    )
    _check(
        ctx.get(step2_prospect.STEP, "extraction_method") == "passthrough",
        "un .md se toma tal cual, sin OCR",
    )


# ============================================================================
# Paso 3 — clasificación + verificación de una regla
# ============================================================================

def paso3(workdir: Path) -> None:
    """Clasifica la disposición y verifica sus reglas contra el prospecto."""
    from agents.compliance_checker import ComplianceChecker
    from agents.disposition_classifier import DispositionClassifier

    prospecto = (FIXTURES / "prospecto_minimo.md").read_text(encoding="utf-8")
    disposicion = _leer_json(FIXTURES / "reglas" / "disposicion_minima_rules.json")

    clasificador = DispositionClassifier(rules_dir=FIXTURES / "reglas")
    clasificacion = clasificador.classify(prospecto)
    aplicables = [c for c in clasificacion["classifications"] if c.get("applies")]

    _check(clasificacion["total_dispositions_evaluated"] == 1, "evaluó la única disposición")
    _check(
        len(aplicables) == 1 and aplicables[0]["disposition_id"] == DISPOSITION_ID,
        "la clasificó como aplicable (prospecto de venta bajo receta)",
    )

    checker = ComplianceChecker()
    verificacion = checker.check(prospect_text=prospecto, disposition=disposicion)
    evaluacion = next(e for e in verificacion["evaluations"] if e["rule_id"] == 1)

    _check(verificacion["total_rules"] == 2, "verificó las dos reglas de la disposición")
    _check(
        evaluacion["status"] == "missing",
        f"detectó la regla como incumplida (devolvió: {evaluacion['status']})",
    )
    _check(
        bool(evaluacion.get("checker_notes")),
        "el checker explica por qué la regla no se cumple",
    )


# ============================================================================
# Paso 4 — adecuación de una regla incumplida
# ============================================================================

def paso4(workdir: Path) -> None:
    """Adecua el prospecto para resolver las reglas incumplidas del informe."""
    from agents.prospect_adequator import ProspectAdequator

    ctx = _contexto(workdir, "20260101-0004")
    adecuador = ProspectAdequator()
    resultado = adecuador.run(
        compliance_report_path=FIXTURES / "compliance_report_minimo.json",
        prospect_path=FIXTURES / "prospecto_minimo.md",
        output_dir=ctx.adequated_dir,
    )

    resumen = resultado["summary"]
    _check(resumen["total_missing_rules"] == 2, "tomó las dos reglas incumplidas del informe")
    _check(resumen["adequation_performed"], "aplicó cambios")

    texto = Path(resultado["output_files"]["txt"]).read_text(encoding="utf-8")
    _check(FRASE_EXIGIDA in texto.upper(), "el prospecto adecuado incorpora la frase exigida")
    _check("╬" in texto, "delimita lo agregado con las marcas de adecuación")
    _check("TESTAMOL" in texto, "conserva el prospecto original")
    for kind in ("json", "txt", "docx"):
        _check(
            kind in resultado["output_files"]
            and Path(resultado["output_files"][kind]).stat().st_size > 0,
            f"escribió la salida en {kind.upper()}",
        )


# ============================================================================
# Paso 5 — verificación final con Claude
# ============================================================================

def paso5(workdir: Path) -> None:
    """
    Revisa la adecuación con Claude.

    El prospecto adecuado del fixture trae a propósito un `[COMPLETAR ACÁ!]`:
    el verificador NO debe tratarlo como incumplimiento, sino como un hueco
    deliberado para quien tenga el dato.
    """
    from agents.final_verifier import FinalVerifier, render_text

    ctx = _contexto(workdir, "20260101-0005")
    verificador = FinalVerifier()
    resultado = verificador.verify(
        rules=[_leer_json(FIXTURES / "reglas" / "disposicion_minima_rules.json")],
        compliance_report=_leer_json(FIXTURES / "compliance_report_minimo.json"),
        original_prospect=(FIXTURES / "prospecto_minimo.md").read_text(encoding="utf-8"),
        adequated_prospect=(FIXTURES / "prospecto_adecuado_minimo.txt").read_text(encoding="utf-8"),
    )

    _check(resultado.get("veredicto") in ("correcta", "correcta_con_observaciones", "incorrecta"),
           "devolvió un veredicto del enum")
    _check(isinstance(resultado.get("adecuaciones"), list) and resultado["adecuaciones"],
           "revisó al menos una adecuación")

    por_regla = {str(a.get("rule_id")): a for a in resultado["adecuaciones"]}
    if _check("1" in por_regla, "evaluó la regla 1 (frase exigida)"):
        _check(
            por_regla["1"]["evaluacion"] == "resuelta",
            f"reconoce que la frase exigida quedó agregada "
            f"(devolvió: {por_regla['1']['evaluacion']})",
        )
    # La regla 2 pide un dato que el pipeline no puede conocer: el paso 4 dejó el
    # hueco, y eso es lo correcto — no un incumplimiento.
    if _check("2" in por_regla, "evaluó la regla 2 (certificado ANMAT)"):
        _check(
            por_regla["2"]["evaluacion"] == "pendiente_de_dato",
            f"trata el hueco como dato pendiente, no como falla "
            f"(devolvió: {por_regla['2']['evaluacion']})",
        )

    # El punto del test: el placeholder no puede bajar el veredicto a incorrecta.
    _check(
        resultado["veredicto"] != "incorrecta",
        f"el [COMPLETAR ACÁ!] no se cuenta como incumplimiento "
        f"(veredicto: {resultado['veredicto']})",
    )
    _check(
        not any(a["evaluacion"] in ("no_resuelta", "introduce_error")
                for a in resultado["adecuaciones"]),
        "ninguna adecuación quedó como no resuelta o errónea",
    )

    texto = render_text(resultado)
    _check("VEREDICTO" in texto and len(texto) > 200, "el informe se renderiza a texto legible")

    console.section("Informe del verificador")
    console.detail(resultado.get("resumen", ""))
    for item in resultado.get("requiere_revision_humana") or []:
        console.detail(
            f"⚑ regla {item.get('rule_id')} ({item.get('motivo')}): "
            f"{item.get('que_debe_decidir_la_persona', '')}"
        )


# ============================================================================
# Runner
# ============================================================================

PASOS: Dict[int, tuple] = {
    1: ("Disposición → reglas JSON", paso1),
    2: ("Prospecto → texto limpio (sin API)", paso2),
    3: ("Clasificación + verificación de reglas", paso3),
    4: ("Adecuación de las reglas incumplidas", paso4),
    5: ("Verificación final con Claude", paso5),
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pruebas de integración por paso, contra las APIs reales",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Cada paso arranca de fixtures fijos: se puede probar el 4 sin correr el 3.",
    )
    parser.add_argument(
        "--paso", type=int, nargs="+", choices=sorted(PASOS),
        help="Pasos a probar (default: todos)",
    )
    parser.add_argument("--listar", action="store_true", help="Lista los pasos y termina")
    parser.add_argument(
        "--conservar", action="store_true",
        help="No borra los artefactos temporales, para inspeccionarlos",
    )
    args = parser.parse_args()

    if args.listar:
        console.summary_table(
            [(f"paso {n}", titulo) for n, (titulo, _) in sorted(PASOS.items())],
            title="Pasos disponibles",
        )
        return 0

    setup_logging()
    elegidos = sorted(args.paso or PASOS)
    console.banner(
        "PRUEBAS DE INTEGRACIÓN",
        f"pasos {', '.join(map(str, elegidos))} · una regla, un prospecto corto · APIs reales",
    )

    tracker.reset()
    workdir = Path(tempfile.mkdtemp(prefix="integracion_prospectos_"))
    fallados: List[int] = []

    try:
        for numero in elegidos:
            titulo, funcion = PASOS[numero]
            console.step(str(numero), titulo)
            antes = len(_fallos)
            try:
                funcion(workdir)
            except Exception as e:  # noqa: BLE001 — un paso roto no frena a los demás
                console.error(f"El paso {numero} lanzó una excepción: {e}")
                _fallos.append(f"paso {numero}: {e}")
            if len(_fallos) > antes:
                fallados.append(numero)
    finally:
        if args.conservar:
            console.info(f"Artefactos en {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)

    totales = tracker.totals()
    if totales["calls"]:
        console.summary_table(
            [
                ("Llamadas al modelo", totales["calls"]),
                ("Tokens de entrada", f"{totales['input_tokens']:,}".replace(",", ".")),
                ("Tokens de salida", f"{totales['output_tokens']:,}".replace(",", ".")),
                ("Costo", f"US$ {totales['cost_usd']:.4f}".replace(".", ",")
                 if totales["cost_usd"] is not None else "s/d"),
            ],
            title="Consumo de la prueba",
        )

    if _fallos:
        console.banner(
            "PRUEBAS DE INTEGRACIÓN CON FALLOS",
            f"{len(_fallos)} chequeos fallaron · pasos {', '.join(map(str, fallados))}",
            color=console.BRIGHT_RED,
        )
        for fallo in _fallos:
            console.detail(fallo)
        return 1

    console.banner(
        "PRUEBAS DE INTEGRACIÓN OK",
        f"pasos {', '.join(map(str, elegidos))} sin fallos",
        color=console.BRIGHT_GREEN,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
