"""
Step 1 — Dispositions and regulations → rules JSON.

Asks whether generated rules already exist:

  YES → an existing RULES FOLDER is pointed at (a previous run's, or the repo's
        base rules) and used as is. It is not copied: that would be a new folder
        with exactly the same content.

  NO  → the disposition documents are requested (PDF / MD / TXT / DOCX), copied
        into the run's folder, a model is chosen and the rules are generated with
        the `RulesGenerator` (two passes per regulation), leaving the result in
        `disposiciones-explotadas/<timestamp>/reglas-extraidas`.

Only the GENERATING branch creates a folder under `disposiciones-explotadas/`: it
is the only one that produces new content.

Either way the step leaves a `rules_dir` key in the manifest, which is what step 3
consumes.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import List, Optional

from agents.rules_generator import RulesGenerator
from core import console
from core.config import AVAILABLE_MODELS, settings
from core.run_context import RunContext, list_existing_rules_dirs
from etl.document_text import extract_text, list_documents

logger = logging.getLogger(__name__)

STEP = "paso1_reglas"


def run(
    ctx: RunContext,
    interactive: bool = True,
    reuse_rules: Optional[bool] = None,
    rules_source: Optional[Path] = None,
) -> Path:
    """
    Run step 1 and return the run's RULES FOLDER.

    Args:
        ctx: The run's context.
        interactive: When False, nothing is asked and the most recent rules are reused.
        reuse_rules: Force the branch (True = reuse, False = generate).
        rules_source: Specific RULES FOLDER to reuse, with no asking and no
            guessing. Used by the web interface, where the user picks it.

    Returns:
        The RULES FOLDER step 3 will consume: the freshly generated one, or the
        existing one being reused.
    """
    if reuse_rules is None:
        reuse_rules = (
            console.ask_yes_no("¿Ya tenés las reglas generadas a partir de las disposiciones?", default=True)
            if interactive
            else True
        )

    if reuse_rules:
        return _reuse_existing_rules(ctx, interactive=interactive, rules_source=rules_source)
    return _generate_rules(ctx, interactive=interactive)


# ============================================================================
# YES branch — reuse existing rules
# ============================================================================

def _reuse_existing_rules(
    ctx: RunContext,
    interactive: bool,
    rules_source: Optional[Path] = None,
) -> Path:
    """Point the run at an existing RULES FOLDER, without duplicating it."""
    if rules_source is not None:
        source = Path(rules_source)
        if not source.is_dir() or not any(source.glob("*.json")):
            raise FileNotFoundError(f"La carpeta de reglas indicada no tiene JSON: {source}")
        return _record_reuse(ctx, source)

    candidates = [d for d in list_existing_rules_dirs(ctx.settings) if d != ctx.rules_dir]
    if not candidates:
        base = ctx.settings.base_rules_dir
        missing_base = "" if base.is_dir() else " (esa carpeta no existe)"
        raise FileNotFoundError(
            "No hay reglas generadas todavía: hay que extraerlas de las disposiciones "
            "antes de analizar un prospecto. Corré el paso 1 en modo «generar» "
            f"(en la web, la opción «generar reglas nuevas»), o dejá los JSON en {base}{missing_base}. "
            f"Se buscó en {ctx.settings.exploded_dir} y en {base}."
        )

    if interactive and len(candidates) > 1:
        labels = [f"{d}  ({len(list(d.glob('*.json')))} disposiciones)" for d in candidates]
        source = candidates[console.ask_choice("¿Qué carpeta de reglas querés usar?", labels)]
    else:
        source = candidates[0]

    return _record_reuse(ctx, source)


def _record_reuse(ctx: RunContext, source: Path) -> Path:
    """
    Leave the reused RULES FOLDER in the manifest.

    Nothing is copied: duplicating the JSONs would create one folder per run with
    exactly the same content. The manifest stores the path, so the run stays
    traceable — you know which rules it was analysed with — without filling
    `disposiciones-explotadas/` with identical copies.
    """
    files = sorted(p.name for p in source.glob("*.json"))
    console.info(f"Reutilizando reglas de {console.path_link(source)}")
    console.ok(f"{len(files)} disposiciones disponibles para el paso 3")

    ctx.record(
        STEP,
        mode="reuse",
        rules_dir=source,
        source_rules_dir=source,
        rules_files=files,
    )
    return source


# ============================================================================
# NO branch — generate rules with the LLM
# ============================================================================

def _generate_rules(ctx: RunContext, interactive: bool) -> Path:
    """Ask for the disposition documents and generate the rules with the LLM."""
    documents = _collect_documents(ctx, interactive=interactive)
    if not documents:
        raise ValueError("No se seleccionó ningún documento de disposición")

    model = settings.rules_generator.model
    if interactive:
        options = AVAILABLE_MODELS + ["otro (escribir el nombre)"]
        default_index = options.index(model) if model in options else 0
        choice = console.ask_choice(
            "¿Con qué modelo querés generar las reglas?", options, default=default_index
        )
        model = (
            console.ask_text("Nombre del modelo", default=model)
            if choice == len(options) - 1
            else options[choice]
        )

    console.section(f"Generando reglas de {len(documents)} disposiciones con {model}")
    console.detail("dos pasadas por norma: borrador + auditoría/normalización de schema")

    generator = RulesGenerator(model=model)
    result = generator.generate_batch(
        documents=documents,
        output_dir=ctx.rules_dir,
        text_loader=extract_text,
    )

    console.summary_table(
        [
            ("Disposiciones procesadas", len(documents)),
            ("Reglas generadas", sum(g["rules_count"] for g in result["generated"])),
            ("Archivos JSON", len(result["generated"])),
            ("Fallidas", len(result["failed"])),
            ("Carpeta de reglas", ctx.rules_dir),
        ],
        title="Resumen del paso 1",
    )

    if not result["generated"]:
        raise RuntimeError("No se pudo generar ninguna regla; revisá los errores anteriores")
    if result["failed"]:
        console.warn(f"{len(result['failed'])} disposiciones fallaron y quedan fuera de esta corrida")

    ctx.record(
        STEP,
        mode="generate",
        model=model,
        source_documents=[str(d) for d in documents],
        rules_dir=ctx.rules_dir,
        generated=result["generated"],
        failed=result["failed"],
    )
    return ctx.rules_dir


def _collect_documents(ctx: RunContext, interactive: bool) -> List[Path]:
    """
    Ask for the disposition documents and copy them into the run's folder.

    Accepts a single file or a whole folder. The original files are preserved in
    `<run>/fuentes`, which only exists in this branch.
    """
    sources: List[Path] = []

    if interactive:
        console.info("Podés indicar un archivo suelto o una carpeta con varias disposiciones")
        selection = console.ask_existing_path(
            "Documentos de las disposiciones (PDF, MD, TXT o DOCX)",
            default=ctx.settings.dispositions_sources_dir,
        )
    else:
        selection = ctx.settings.dispositions_sources_dir

    if selection.is_dir():
        sources = list_documents(selection)
        console.info(f"{len(sources)} documentos encontrados en {console.path_link(selection)}")
    else:
        sources = [selection]

    if interactive and len(sources) > 1:
        for document in sources:
            console.detail(document.name)
        if not console.ask_yes_no(f"¿Procesar estos {len(sources)} documentos?", default=True):
            raise ValueError("Selección de documentos cancelada por el usuario")

    ctx.source_docs_dir.mkdir(parents=True, exist_ok=True)
    copied: List[Path] = []
    for document in sources:
        target = ctx.source_docs_dir / document.name
        shutil.copy2(document, target)
        copied.append(target)
    console.ok(f"{len(copied)} documentos copiados a {console.path_link(ctx.source_docs_dir)}")

    return copied
