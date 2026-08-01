#!/usr/bin/env python
"""
Smoke test of the pipeline without calling the OpenAI API.

Replaces `LLMClient.run` with canned responses and runs steps 1 to 4 in full. It
validates, without spending tokens or depending on the network, that:

- every step leaves its artifacts in the manifest and the next one finds them,
- the classifier's, the checker's and the adequator's responses parse correctly,
- the report is generated in JSON, Markdown, HTML and PDF,
- the adequated leaflet comes out as TXT and DOCX with the formatting markers.

Usage:
    python tests/smoke_pipeline.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agents import llm_client  # noqa: E402
from core import console  # noqa: E402
from core.config import Settings, settings as real_settings  # noqa: E402
from core.console import setup_logging  # noqa: E402
from core.run_context import RunContext  # noqa: E402

PROSPECT_TEXT = """# PROSPECTO DE PRUEBA

TADALAFILO 20 mg comprimidos recubiertos.
Venta bajo receta archivada.

## Advertencias
Mantener fuera del alcance de los niños.
"""

# Classifier response: the disposition applies.
CLASSIFIER_RESPONSE = {
    "disposition_id": "ANMAT_TEST_2026",
    "applies": True,
    "match_score": 0.95,
    "reason": "El prospecto declara tadalafilo como principio activo.",
}

# Checker responses, one per rule (the second one comes out unmet).
CHECKER_RESPONSES = [
    {
        "disposition_id": "ANMAT_TEST_2026",
        "rule_id": 1,
        "status": "ok",
        "evidence_snippets": ["Mantener fuera del alcance de los niños."],
        "checker_notes": "La advertencia está presente.",
    },
    {
        "disposition_id": "ANMAT_TEST_2026",
        "rule_id": 2,
        "status": "missing",
        "evidence_snippets": [],
        "checker_notes": "Falta la advertencia del ANEXO I.",
    },
]

# Adequator response, with the formatting markers the DOCX interprets.
ADEQUATOR_RESPONSE = {
    "updated_prospect_text": (
        "# PROSPECTO DE PRUEBA\n\n"
        "**TADALAFILO 20 mg** comprimidos recubiertos.\n\n"
        "## Advertencias\n"
        "Mantener fuera del alcance de los niños.\n"
        "╬\n"
        "*Este medicamento no debe utilizarse junto con nitratos.*\n"
        '*{ref. { "disposition_id": "ANMAT_TEST_2026", "rule_id": 2 }}*\n'
        "╬\n"
        "[COMPLETAR ACÁ!]\n"
    ),
    "adequation_notes": "Se agregó la advertencia faltante del ANEXO I.",
}

RULES_JSON = {
    "disposition_id": "ANMAT_TEST_2026",
    "title": "Disposición de prueba",
    "source_type": "DISPOSICIÓN",
    "sale_condition": "VENTA BAJO RECETA",
    "objective": "Validar el pipeline de punta a punta.",
    "rules": [
        {
            "rule_id": 1,
            "objective": "Incluir la advertencia de conservación fuera del alcance de los niños.",
            "verification_procedure": "Buscar la frase en la sección de advertencias.",
            "acceptance_criteria": "La frase está presente.",
            "must_include_phrases": ["Mantener fuera del alcance de los niños"],
            "article_reference": "Art. 1",
            "attach_reference": [],
        },
        {
            "rule_id": 2,
            "objective": "Incluir la advertencia del ANEXO I sobre nitratos.",
            "verification_procedure": "Verificar la presencia del texto del ANEXO I.",
            "acceptance_criteria": "El texto del ANEXO I está incluido.",
            "must_include_phrases": ["no debe utilizarse junto con nitratos"],
            "article_reference": "Art. 2",
            "attach_reference": ["ANEXO I"],
        },
    ],
}


class _StubLLMClient:
    """Stand-in for `LLMClient` that answers based on the prompt it receives."""

    checker_calls = 0

    def __init__(self, model, instructions=None, **kwargs):
        self.model = model
        self.instructions = instructions or ""

    @classmethod
    def from_config(cls, config, instructions=None, **kwargs):
        # **kwargs so it does not break when the real client grows parameters
        # (max_retries, etc.): the stub does not care.
        return cls(config.model, instructions)

    def run(self, input, instructions=None, json_mode=False, timeout=300.0, cache_key=None):
        payload = json.loads(input) if input.strip().startswith("{") else {}

        if "current_json" in payload:
            # Rules generator, pass 2: audits and normalises the draft.
            body = dict(RULES_JSON, disposition_id=payload["disposition_id"])
        elif "raw_text" in payload:
            # Rules generator, pass 1: the draft.
            body = {"disposition_id": payload["disposition_id"], "rules": RULES_JSON["rules"][:1]}
        elif "rule" in payload:
            index = min(_StubLLMClient.checker_calls, len(CHECKER_RESPONSES) - 1)
            _StubLLMClient.checker_calls += 1
            body = CHECKER_RESPONSES[index]
        elif "disposition" in payload:
            body = CLASSIFIER_RESPONSE
        elif "compliance_report" in payload:
            body = ADEQUATOR_RESPONSE
        else:
            raise AssertionError(f"Prompt inesperado: {input[:200]}")

        return SimpleNamespace(
            id=f"resp_stub_{id(body)}",
            output_text=json.dumps(body, ensure_ascii=False),
        )

    def run_text(self, input, **kwargs):
        return self.run(input, **kwargs).output_text


def _check(condition: bool, message: str) -> None:
    if condition:
        console.ok(message)
    else:
        console.error(message)
        raise AssertionError(message)


def main() -> int:
    setup_logging()
    console.banner("PRUEBA DE HUMO DEL PIPELINE", "sin llamadas reales a la API")

    workdir = Path(tempfile.mkdtemp(prefix="smoke_prospectos_"))
    stamp = "20260101-0000"

    # Fully isolate the run inside a temporary directory.
    test_settings = Settings(
        openai_api_key="stub",
        exploded_dir=workdir / "disposiciones-explotadas",
        corridas_dir=workdir / "corridas",
        base_rules_dir=workdir / "reglas-base",
        dispositions_sources_dir=real_settings.dispositions_sources_dir,
    )
    test_settings.base_rules_dir.mkdir(parents=True, exist_ok=True)
    (test_settings.base_rules_dir / "disposicion_test_rules.json").write_text(
        json.dumps(RULES_JSON, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    prospect_file = workdir / "prospecto_prueba.md"
    prospect_file.write_text(PROSPECT_TEXT, encoding="utf-8")

    # Swap the LLM client in every module that already imported it.
    from agents import compliance_checker, disposition_classifier, prospect_adequator, rules_generator

    llm_client.LLMClient = _StubLLMClient
    for module in (disposition_classifier, compliance_checker, prospect_adequator, rules_generator):
        module.LLMClient = _StubLLMClient

    from pipeline import step1_rules, step2_prospect, step3_compliance, step4_adequation

    ctx = RunContext(stamp=stamp, settings=test_settings).prepare()

    console.step("1", "Reglas (reutilización)")
    rules_dir = step1_rules.run(ctx, interactive=False, reuse_rules=True)
    _check(len(list(rules_dir.glob("*.json"))) == 1, "el paso 1 resolvió la CARPETA-REGLAS")
    _check(
        rules_dir == test_settings.base_rules_dir,
        "reutilizar apunta a las reglas existentes en vez de copiarlas",
    )
    _check(
        not ctx.rules_dir.exists(),
        "reutilizar no crea carpeta bajo disposiciones-explotadas/",
    )

    console.step("2", "Prospecto → texto limpio")
    clean_text = step2_prospect.run(ctx, prospect_path=prospect_file, interactive=False)
    _check(clean_text.exists(), "el paso 2 generó el texto limpio")
    _check(
        ctx.get_path(step2_prospect.STEP, "clean_text_file") == clean_text,
        "el paso 2 registró el texto limpio en el manifest",
    )

    console.step("3", "Verificación de cumplimiento")
    state = step3_compliance.run(ctx)
    report = state["report"]
    _check(report["summary"]["total_rules_checked"] == 2, "el paso 3 verificó las 2 reglas")
    _check(report["overall_statistics"]["ok"] == 1, "el paso 3 contó 1 regla cumplida")
    _check(report["overall_statistics"]["missing"] == 1, "el paso 3 contó 1 regla incumplida")
    for kind in ("json", "markdown", "html", "pdf"):
        path = ctx.get(step3_compliance.STEP, f"report_{'markdown' if kind == 'markdown' else kind}")
        _check(bool(path) and Path(path).stat().st_size > 0, f"el informe {kind.upper()} se generó")

    console.step("4", "Adecuación del prospecto")
    result = step4_adequation.run(ctx)
    _check(result["summary"]["total_missing_rules"] == 1, "el paso 4 adecuó la regla incumplida")

    output_files = result["output_files"]
    for kind in ("json", "txt", "docx"):
        _check(
            kind in output_files and Path(output_files[kind]).stat().st_size > 0,
            f"el prospecto adecuado se generó en {kind.upper()}",
        )

    adequated_text = Path(output_files["txt"]).read_text(encoding="utf-8")
    _check("nitratos" in adequated_text, "el texto adecuado incorpora la advertencia faltante")
    _check("╬" in adequated_text, "el TXT conserva los delimitadores de adecuación")

    from docx import Document

    docx_paragraphs = Document(output_files["docx"]).paragraphs
    docx_text = "\n".join(p.text for p in docx_paragraphs)
    _check("╬" not in docx_text, "el DOCX no imprime los delimitadores ╬")

    docx_runs = [run for paragraph in docx_paragraphs for run in paragraph.runs]
    _check(any(r.bold for r in docx_runs), "el DOCX tiene texto en negrita")
    _check(
        any(r.italic and r.font.color.rgb is not None for r in docx_runs),
        "el DOCX tiene las adecuaciones resaltadas en color",
    )

    manifest = json.loads(ctx.manifest_path.read_text(encoding="utf-8"))
    _check(len(manifest["steps"]) == 4, "el manifest registró los 4 pasos")

    _smoke_rules_generation(workdir, test_settings)
    _smoke_retry_policy(workdir, test_settings, prospect_file)

    console.banner("PRUEBA DE HUMO OK", f"artefactos en {workdir}", color=console.BRIGHT_GREEN)
    shutil.rmtree(workdir, ignore_errors=True)
    return 0


def _smoke_rules_generation(workdir: Path, test_settings: Settings) -> None:
    """Covers step 1's branch that generates new rules with the LLM (two passes)."""
    from agents.rules_generator import RulesGenerator, derive_disposition_id
    from etl.document_text import extract_text
    from pipeline import step1_rules

    console.step("1b", "Reglas (generación con LLM)")

    _check(
        derive_disposition_id(Path("Disposicion_4525-2006 TADALAFILO.pdf")) == "ANMAT_4525_2006",
        "derive_disposition_id resuelve el formato Disposicion_NNNN-AAAA",
    )
    _check(
        derive_disposition_id(Path("anmar_circular_5_2012.pdf")) == "ANMAT_CIRCULAR_5_2012",
        "derive_disposition_id reconoce las circulares",
    )

    sources_dir = workdir / "disposiciones_fuente"
    sources_dir.mkdir(parents=True, exist_ok=True)
    (sources_dir / "Disposicion_9999-2026 PRUEBA.md").write_text(
        "# Disposición de prueba\n\nArt. 1 — El prospecto debe incluir advertencias.\n",
        encoding="utf-8",
    )

    ctx = RunContext(stamp="20260101-0001", settings=test_settings).prepare()
    generator = RulesGenerator(model="gpt-4.1")
    result = generator.generate_batch(
        documents=sorted(sources_dir.glob("*.md")),
        output_dir=ctx.rules_dir,
        text_loader=extract_text,
    )

    _check(not result["failed"], "la generación de reglas no tuvo fallos")
    _check(len(result["generated"]) == 1, "se generó un archivo de reglas por disposición")

    generated = json.loads(Path(result["generated"][0]["rules_file"]).read_text(encoding="utf-8"))
    _check(
        generated["disposition_id"] == "ANMAT_9999_2026",
        "el JSON generado usa el disposition_id derivado del nombre del archivo",
    )
    _check(
        len(generated["rules"]) == len(RULES_JSON["rules"]),
        "la pasada de auditoría amplía el borrador (1 → 2 reglas)",
    )
    _check(
        all("verification_procedure" in r for r in generated["rules"]),
        "las reglas generadas traen el schema que consume el checker",
    )

    # Step 1 in generation mode: copies the sources and records everything in the manifest.
    import dataclasses

    gen_settings = dataclasses.replace(test_settings, dispositions_sources_dir=sources_dir)
    ctx_step1 = RunContext(stamp="20260101-0002", settings=gen_settings).prepare()
    rules_dir = step1_rules.run(ctx_step1, interactive=False, reuse_rules=False)

    _check(
        len(list(rules_dir.glob("*.json"))) == 1,
        "el paso 1 en modo generación dejó las reglas en la carpeta de la corrida",
    )
    _check(
        len(list(ctx_step1.source_docs_dir.glob("*"))) == 1,
        "el paso 1 preservó los documentos fuente en <corrida>/fuentes",
    )
    _check(
        ctx_step1.get(step1_rules.STEP, "mode") == "generate",
        "el paso 1 registró el modo 'generate' en el manifest",
    )


def _smoke_retry_policy(workdir: Path, test_settings: Settings, prospect_file: Path) -> None:
    """
    Covers the retry policy: it recovers from a transient failure and, when it
    cannot, aborts the run instead of leaving a partial analysis behind.
    """
    import dataclasses

    from openai import OpenAIError

    from agents import compliance_checker
    from core.retry import with_retries
    from pipeline import step2_prospect, step3_compliance, step4_adequation

    console.step("5", "Reintentos y corte ante análisis parcial")

    # (a) An unusable response is retried until it comes back right.
    attempts = {"n": 0}

    def flaky() -> str:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ValueError("la respuesta no parsea como JSON")
        return "listo"

    _check(
        with_retries(flaky, description="prueba", attempts=3, base_delay=0.0) == "listo",
        "with_retries se recupera de un fallo transitorio",
    )

    # (b) SDK errors are not retried again here: the SDK already did that.
    sdk_attempts = {"n": 0}

    def sdk_error() -> None:
        sdk_attempts["n"] += 1
        raise OpenAIError("el SDK ya agotó MAX_RETRIES")

    try:
        with_retries(sdk_error, description="prueba", attempts=3, base_delay=0.0)
        raise AssertionError("with_retries debía propagar el error del SDK")
    except RuntimeError:
        pass
    _check(sdk_attempts["n"] == 1, "los errores del SDK no se reintentan por segunda vez")

    # (c) If a rule cannot be evaluated, step 3 aborts the run.
    class _BrokenCheckerLLMClient(_StubLLMClient):
        """Like the stub, but the checker returns something useless."""

        def run(self, input, instructions=None, json_mode=False, timeout=300.0, cache_key=None):
            if '"rule"' in input:
                return SimpleNamespace(id="resp_stub_roto", output_text="lo siento, no puedo")
            return super().run(input, instructions, json_mode, timeout)

    original_client = compliance_checker.LLMClient
    original_settings = compliance_checker.settings
    compliance_checker.LLMClient = _BrokenCheckerLLMClient
    compliance_checker.settings = dataclasses.replace(
        original_settings, llm_attempts=2, llm_retry_backoff=0.0
    )
    try:
        ctx = RunContext(stamp="20260101-0003", settings=test_settings).prepare()
        # Rules of this run's own, so it does not depend on the previous sub-tests.
        # The folder has to be created here: only step 1 creates it, when generating.
        ctx.rules_dir.mkdir(parents=True, exist_ok=True)
        (ctx.rules_dir / "disposicion_test_rules.json").write_text(
            json.dumps(RULES_JSON, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        step2_prospect.run(ctx, prospect_path=prospect_file, interactive=False)

        try:
            step3_compliance.run(ctx)
            raise AssertionError("el paso 3 debía cortar cuando una regla no se puede evaluar")
        except RuntimeError:
            console.ok("el paso 3 corta cuando una regla no se puede evaluar")

        _check(
            ctx.get(step3_compliance.STEP, "status") != "ok",
            "el paso 3 fallido no queda marcado como 'ok' en el manifest",
        )

        # (d) And step 4 refuses to adequate over that report, even when invoked
        #     directly while resuming the run.
        ctx.record(
            step3_compliance.STEP,
            report_json=ctx.result_dir / "informe_inexistente.json",
            status="incompleto",
        )
        try:
            step4_adequation.run(ctx)
            raise AssertionError("el paso 4 debía negarse a correr tras un paso 3 incompleto")
        except ValueError:
            console.ok("el paso 4 no corre si el paso 3 quedó incompleto")
    finally:
        compliance_checker.LLMClient = original_client
        compliance_checker.settings = original_settings


if __name__ == "__main__":
    raise SystemExit(main())
