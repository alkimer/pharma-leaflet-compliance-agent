"""
Orchestrator of the whole pipeline.

Step 0  computes the `<timestamp>` and prepares the run's folders
Step 1  dispositions → rules JSON        (disposiciones-explotadas/<timestamp>/reglas-extraidas)
Step 2  leaflet → clean text             (corridas/<timestamp>/documento-subido)
Step 3  compliance check                 (corridas/<timestamp>/resultado)
Step 4  leaflet adequation               (corridas/<timestamp>/documento-adecuado)
Step 5  final verification with Claude   (corridas/<timestamp>/verificacion-final)  · optional

Every step records its artifacts in `corridas/<timestamp>/manifest.json`, and the
next one reads them from there: no hardcoded paths between steps.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from core import cancellation, console, language, mailer
from core.console import setup_logging
from core.usage import tracker
from core.run_context import RunContext, make_stamp
from pipeline import (
    step1_rules,
    step2_prospect,
    step3_compliance,
    step4_adequation,
    step5_verification,
)

logger = logging.getLogger(__name__)

LAST_STEP = 5

# Step 5 is optional: it does not run unless explicitly requested.
OPTIONAL_STEPS = (5,)


def run_pipeline(
    prospect_path: Optional[Path] = None,
    stamp: Optional[str] = None,
    interactive: bool = True,
    reuse_rules: Optional[bool] = None,
    rules_source: Optional[Path] = None,
    force_ocr: bool = False,
    from_step: int = 1,
    to_step: int = LAST_STEP,
    verify: bool = False,
    lang: str = language.DEFAULT,
    email: Optional[str] = None,
    on_context: Optional[Callable[[RunContext], None]] = None,
) -> RunContext:
    """
    Run the pipeline end to end.

    Args:
        prospect_path: Leaflet to analyse (if omitted and interactive, it is asked for).
        stamp: `<timestamp>` to reuse in order to resume an existing run.
        interactive: When False, nothing is asked (defaults and given arguments are used).
        reuse_rules: Force step 1's branch (True = reuse, False = generate).
        rules_source: Specific RULES FOLDER to reuse in step 1.
        force_ocr: Force local OCR in step 2.
        from_step: First step to run (to resume a run).
        to_step: Last step to run.
        verify: Enable step 5, the final verification with Claude. Off by default:
            it is optional and it is the run's most expensive call.
        lang: Language of the email that delivers the results ("es" or "en"). The
            documents themselves are always in Spanish — see `core.language`.
        email: Address to mail the results to when the run finishes. Optional:
            the artifacts are written to disk either way.
        on_context: Called with the `RunContext` as soon as it is built, before any
            step runs. The web UI uses it to follow the run (and its artifacts)
            even if it later fails.

    Returns:
        The run's `RunContext`, with every artifact recorded.
    """
    # ---- Step 0 ----
    is_resume = stamp is not None
    ctx = RunContext.load(stamp) if is_resume else RunContext(stamp=make_stamp())
    ctx.prepare()
    setup_logging(log_file=ctx.log_file)
    tracker.reset()
    # Before step 1: the agents read the language when they are built.
    lang = language.use(lang)
    if on_context is not None:
        on_context(ctx)

    console.banner(
        "ANALIZADOR Y ADECUADOR DE PROSPECTOS",
        f"pipeline de 4 pasos · ANMAT · corrida {ctx.stamp}",
    )
    console.step("0", "Contexto de la corrida", "fecha-hora y estructura de carpetas")
    console.kv("Fecha-hora (<fecha-hora>)", ctx.stamp, console.BRIGHT_CYAN)
    console.kv("Reglas de la corrida", ctx.rules_dir)
    console.kv("Carpeta de la corrida", ctx.run_root)
    console.kv("Log de la corrida", ctx.log_file)
    if lang != language.DEFAULT:
        console.kv("Idioma del correo", lang, console.BRIGHT_CYAN)
        console.detail("los documentos que produce la corrida salen siempre en español")
    if email:
        console.kv("Enviar resultados a", email)
    if is_resume:
        console.info(f"Retomando una corrida existente desde el paso {from_step}")
        for step_name in ctx.artifacts:
            console.detail(f"ya ejecutado: {step_name}")
    console.ok("Paso 0 completado")

    # ---- Step 1 ----
    if from_step <= 1 <= to_step:
        cancellation.check()
        console.step("1", "Disposiciones → reglas JSON", "CARPETA-REGLAS de la corrida")
        rules_dir = step1_rules.run(
            ctx, interactive=interactive, reuse_rules=reuse_rules, rules_source=rules_source
        )
        console.ok(f"Paso 1 completado · {console.path_link(rules_dir)}")

    # ---- Step 2 ----
    if from_step <= 2 <= to_step:
        cancellation.check()
        console.step("2", "Prospecto a analizar → texto limpio", "documento-subido/")
        clean_text = step2_prospect.run(
            ctx, prospect_path=prospect_path, interactive=interactive, force_ocr=force_ocr
        )
        console.ok(f"Paso 2 completado · {console.path_link(clean_text)}")

    # ---- Step 3 ----
    if from_step <= 3 <= to_step:
        cancellation.check()
        console.step("3", "Verificación de cumplimiento normativo", "resultado/")
        step3_compliance.run(ctx)
        console.ok("Paso 3 completado")

    # ---- Step 4 ----
    if from_step <= 4 <= to_step:
        cancellation.check()
        console.step("4", "Adecuación del prospecto", "documento-adecuado/")
        step4_adequation.run(ctx)
        console.ok("Paso 4 completado")

    # ---- Step 5 (optional) ----
    if verify and from_step <= 5 <= to_step:
        cancellation.check()
        console.step("5", "Verificación final con Claude", "verificacion-final/ · opcional")
        step5_verification.run(ctx)
        console.ok("Paso 5 completado")

    if email:
        _send_results(ctx, email, lang)

    _print_usage_report(ctx)
    _print_final_summary(ctx)
    language.reset()
    return ctx


def _send_results(ctx: RunContext, email: str, lang: str) -> None:
    """
    Mail the run's two deliverables: the adequated leaflet and the report.

    Failing to send is reported and nothing more: the analysis is finished and
    saved, and losing it over an SMTP error would be absurd. The outcome is
    recorded in the manifest so the run leaves a trace of what happened.
    """
    console.section("Envío por correo")

    attachments = []
    output_files: Dict[str, Any] = ctx.get(step4_adequation.STEP, "output_files") or {}
    if output_files.get("docx"):
        attachments.append(Path(output_files["docx"]))
    report_pdf = ctx.get(step3_compliance.STEP, "report_pdf")
    if report_pdf:
        attachments.append(Path(report_pdf))
    # Step 5 only ran if it was asked for; when it did, its verdict travels too.
    verification: Dict[str, Any] = ctx.get(step5_verification.STEP, "output_files") or {}
    if verification.get("txt"):
        attachments.append(Path(verification["txt"]))

    result = mailer.send_run_results(
        to=email, attachments=attachments, stamp=ctx.stamp, language=lang
    )
    ctx.record(
        "correo",
        to=email,
        sent=result.sent,
        detail=result.detail,
        attachments=list(result.attachments),
    )

    if result.sent:
        console.ok(f"Correo {result.detail}")
        for name in result.attachments:
            console.detail(f"adjunto: {name}")
    else:
        console.warn(f"No se envió el correo: {result.detail}")
        console.detail("los archivos quedaron igual en la carpeta de la corrida")


def _print_usage_report(ctx: RunContext) -> None:
    """
    Report which models were used, how many tokens they cost and how much was saved.

    The cache saving is not an estimate: every response reports how many input
    tokens came from the cache, and those are billed at a fraction of the price.
    It is compared against what they would have cost at full price.
    """
    report = tracker.snapshot()
    totals = report["totals"]
    if not totals["calls"]:
        return

    ctx.record("consumo", **report)

    console.section("Consumo de modelos")
    for entry in report["by_model"]:
        console.detail(
            f"{entry['model']}  ·  {entry['calls']} "
            f"{'llamada' if entry['calls'] == 1 else 'llamadas'}  ·  "
            f"{_thousands(entry['input_tokens'])} in / {_thousands(entry['output_tokens'])} out  ·  "
            f"{_money(entry['cost_usd'])}"
        )

    rows = [
        ("Llamadas al modelo", totals["calls"]),
        ("Tokens de entrada", f"{_thousands(totals['input_tokens'])}"),
        (
            "  desde la caché",
            f"{_thousands(totals['cached_tokens'])}  "
            f"({_one_decimal(totals['cache_hit_rate'] * 100)}%)",
        ),
        ("  facturados enteros", _thousands(totals["fresh_tokens"])),
        ("Tokens de salida", _thousands(totals["output_tokens"])),
        ("Costo de la corrida", _money(totals["cost_usd"])),
    ]

    savings = totals["cache_savings_usd"]
    without_cache = totals["cost_without_cache_usd"]
    if savings:
        percent = savings / without_cache * 100 if without_cache else 0
        rows.append(("Ahorrado por caché", f"{_money(savings)}  ({percent:.0f}% menos)"))
        rows.append(("  sin caché habría costado", _money(without_cache)))

    console.summary_table(rows)


def _one_decimal(value: float) -> str:
    """One decimal with a comma, the way it is written in Spanish."""
    return f"{value:.1f}".replace(".", ",")


def _thousands(value: int) -> str:
    """Thousands separated by dots, the way it is written in Spanish."""
    return f"{value:,}".replace(",", ".")


def _money(value: Optional[float]) -> str:
    return "s/d" if value is None else f"US$ {value:.4f}".replace(".", ",")


def _print_final_summary(ctx: RunContext) -> None:
    """Closing block listing every output of the run."""
    console.banner(f"CORRIDA {ctx.stamp} FINALIZADA", str(ctx.run_root), color=console.BRIGHT_GREEN)

    rows = [("Reglas usadas", ctx.get(step1_rules.STEP, "rules_dir") or ctx.rules_dir)]

    clean_text = ctx.get(step2_prospect.STEP, "clean_text_file")
    if clean_text:
        rows.append(("Texto limpio", clean_text))

    for label, key in (
        ("Informe JSON", "report_json"),
        ("Informe Markdown", "report_markdown"),
        ("Informe PDF", "report_pdf"),
        ("Informe HTML", "report_html"),
    ):
        value = ctx.get(step3_compliance.STEP, key)
        if value:
            rows.append((label, value))

    output_files: Dict[str, Any] = ctx.get(step4_adequation.STEP, "output_files") or {}
    for kind, path in output_files.items():
        rows.append((f"Prospecto adecuado ({kind})", path))

    rows.append(("Manifest", ctx.manifest_path))
    rows.append(("Log", ctx.log_file))
    console.summary_table(rows, title="Artefactos generados")

    statistics: Dict[str, Any] = ctx.get(step3_compliance.STEP, "overall_statistics") or {}
    if statistics:
        console.summary_table(
            [
                ("Reglas cumplidas", statistics.get("ok", 0)),
                ("Reglas no cumplidas", statistics.get("missing", 0)),
                ("No aplicables", statistics.get("not_applicable", 0)),
                ("No evaluables", statistics.get("not_evaluable", 0)),
                ("Errores", statistics.get("error", 0)),
            ],
            title="Cumplimiento",
        )
