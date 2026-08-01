"""
API of the web interface.

Endpoints:

    GET  /                     the page (terminal view)
    GET  /minimal              the same run, in the light, minimalist view
    GET  /laboratorio          try one isolated step with a custom prompt and params
    GET  /static/*             static assets (the shared i18n dictionary)
    GET  /api/contexto         example leaflets, rules folders, past runs
    GET  /api/laboratorio      the steps the laboratory exposes, with their fields
    POST /api/laboratorio/ejecutar   run one isolated step and return its output
    POST /api/corridas         launch a run (uploaded file or existing path)
    GET  /api/corridas/{id}    status and artifacts of a run
    GET  /api/corridas/{id}/eventos   SSE stream of the live run
    GET  /api/archivo?path=    download an artifact of the run

The download endpoint validates that the requested path lives inside the app's
folders: this is a local server, but `?path=/etc/passwd` has no business working.

The endpoint names and JSON keys are in Spanish because they are the contract with
the front-end. The front-end itself is bilingual (English by default, see
`static/i18n.js`); only the pipeline's streamed log lines stay Spanish.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import threading
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from core import language, mailer
from core.config import settings
from core.run_context import RULES_DIR_NAME, RunContext, list_existing_rules_dirs
from etl.document_text import SUPPORTED_SUFFIXES, list_documents
from pipeline.orchestrator import LAST_STEP
from web import lab
from web.runner import manager

STATIC_DIR = Path(__file__).parent / "static"
UPLOADS_DIR = Path(tempfile.gettempdir()) / "prospectos_web_uploads"

# Variable through which `run_web.py` asks for the browser to be opened. It goes
# through the environment because uvicorn imports this module by name and passes
# it no arguments.
ABRIR_NAVEGADOR_ENV = "PROSPECTOS_ABRIR_NAVEGADOR"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Open the browser only once the server is already listening.

    It used to open before uvicorn started, and there the browser could get in
    first: if another process holds the port (another project listening on
    0.0.0.0, for instance), the request lands on THAT server and returns its own
    404 instead of our page. Reloading "fixed" the problem because by then we
    were already listening.
    """
    url = os.environ.pop(ABRIR_NAVEGADOR_ENV, "")
    if url:
        threading.Timer(0.3, webbrowser.open, args=[url]).start()
    yield


app = FastAPI(
    title="Analizador y adecuador de prospectos",
    docs_url="/api/docs",
    lifespan=lifespan,
)

# Both views load `/static/i18n.js`, the shared translation dictionary.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ============================================================================
# Page
# ============================================================================
# Two views over the same API: the terminal one (default) and a light,
# minimalist one that shows only the process and the two results that matter.

@app.get("/", response_class=HTMLResponse)
@app.get("/clasico", response_class=HTMLResponse)
def index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/minimal", response_class=HTMLResponse)
def minimal() -> str:
    return (STATIC_DIR / "minimal.html").read_text(encoding="utf-8")


@app.get("/laboratorio", response_class=HTMLResponse)
def laboratorio() -> str:
    return (STATIC_DIR / "laboratorio.html").read_text(encoding="utf-8")


# ============================================================================
# Laboratory: one isolated step, with its inputs, params and prompt
# ============================================================================

@app.get("/api/laboratorio")
def lab_catalogo() -> Dict[str, Any]:
    """The available steps with their fields and default values."""
    return {"pasos": lab.catalogo(), "claude_disponible": bool(settings.anthropic_api_key)}


@app.post("/api/laboratorio/ejecutar")
def lab_ejecutar(pedido: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run one step and return its output.

    Synchronous on purpose: these are minimal cases that take seconds, and the
    laboratory is used one test at a time. FastAPI runs it in its threadpool, so
    it does not block the rest of the app.
    """
    try:
        return lab.ejecutar(
            paso_id=pedido.get("paso", ""),
            entradas=pedido.get("entradas") or {},
            parametros=pedido.get("parametros") or {},
            prompts=pedido.get("prompts") or {},
        )
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001 — the model's error is shown to the user
        raise HTTPException(500, f"{type(e).__name__}: {e}") from e


# ============================================================================
# Context used to build the form
# ============================================================================

@app.get("/api/contexto")
def contexto() -> Dict[str, Any]:
    """Everything the page needs in order to offer its options."""
    examples_dir = settings.project_root / "ejemplos" / "prospectos"
    ejemplos = [
        {"nombre": p.name, "ruta": str(p)}
        for p in list_documents(examples_dir)
    ]

    reglas_dirs = list_existing_rules_dirs(settings)
    reglas = [
        {
            "ruta": str(d),
            "nombre": _rules_label(d),
            "disposiciones": len(list(d.glob("*.json"))),
            "base": d == settings.base_rules_dir,
        }
        for d in reglas_dirs
    ]

    active_run = manager.active()
    return {
        "ejemplos": ejemplos,
        "reglas": reglas,
        # The folder the form preselects: the repo's base rules, which are the ones
        # always there. Any other folder — or generating new ones — stays one
        # click away in the same select.
        "reglas_predeterminadas": _default_rules_dir(reglas_dirs),
        # With no rules no analysis is possible: the page needs to know whether it
        # can generate them, and from how many dispositions, to ask for that first.
        "disposiciones": {
            "carpeta": str(settings.dispositions_sources_dir),
            "cantidad": len(_disposition_documents()),
        },
        "corridas": RunContext.list_existing(settings)[:12],
        "ultimo_paso": LAST_STEP,
        # Step 5 needs its own credential: without it the web offers the option
        # disabled instead of letting the run fail at the very end.
        "verificacion_final_disponible": bool(settings.anthropic_api_key),
        "verificador": settings.verifier.model,
        # Without SMTP configured the first screen does not offer to mail the
        # results: promising an email that will never arrive is worse than not
        # offering it.
        "email_disponible": mailer.is_configured(),
        "corrida_activa": (
            active_run.snapshot() if active_run and active_run.status == "running" else None
        ),
    }


def _disposition_documents() -> List[Path]:
    """Disposition documents available for generating rules."""
    return list_documents(settings.dispositions_sources_dir)


def _default_rules_dir(candidates: List[Path]) -> Optional[str]:
    """
    RULES FOLDER the form starts on: the repo's base rules.

    They are the ones that always exist and the ones the project ships with, so
    they are a better default than whichever run happens to be the newest. If
    they are missing, the newest available folder is used instead.
    """
    if not candidates:
        return None
    base = settings.base_rules_dir
    return str(base if base in candidates else candidates[0])


def _rules_label(directory: Path) -> str:
    """Short label: the run's `<timestamp>`, or the base folder's name."""
    if directory.name == RULES_DIR_NAME and directory.parent.parent == settings.exploded_dir:
        return f"corrida {directory.parent.name}"
    return directory.name


# ============================================================================
# Runs
# ============================================================================

@app.post("/api/corridas")
async def crear_corrida(
    archivo: Optional[UploadFile] = None,
    ruta_prospecto: Optional[str] = Form(default=None),
    ruta_reglas: Optional[str] = Form(default=None),
    generar_reglas: bool = Form(default=False),
    desde: int = Form(default=1),
    hasta: int = Form(default=LAST_STEP),
    forzar_ocr: bool = Form(default=False),
    verificacion_final: bool = Form(default=False),
    idioma: str = Form(default=language.DEFAULT),
    email: Optional[str] = Form(default=None),
) -> Dict[str, Any]:
    """
    Launch a run with the uploaded leaflet or with one of the examples.

    With `generar_reglas`, step 1 extracts the rules from the dispositions instead
    of reusing an existing RULES FOLDER. A run that ends at step 1 needs no
    leaflet: it only generates rules for the runs to come.

    `idioma` is the language the run REPORTS in; the adequated leaflet is always
    in Spanish. `email`, if given, receives the results when the run finishes.
    """
    if not 1 <= desde <= hasta <= LAST_STEP:
        raise HTTPException(400, f"Rango de pasos inválido: {desde} → {hasta}")

    # A typo in the address is worth catching now: the run takes minutes and the
    # mistake would only surface at the very end, with nothing sent.
    email = (email or "").strip() or None
    if email and not mailer.valid_address(email):
        raise HTTPException(400, f"La dirección de correo no parece válida: {email}")

    _validar_reglas(desde, ruta_reglas, generar_reglas)

    prospect_path: Optional[Path] = None
    if hasta >= 2:
        prospect_path = await _resolve_prospect(archivo, ruta_prospecto)

    rules_source: Optional[Path] = None
    if ruta_reglas and not generar_reglas:
        rules_source = Path(ruta_reglas)
        if not rules_source.is_dir():
            raise HTTPException(400, f"La carpeta de reglas no existe: {rules_source}")

    try:
        session = manager.start(
            prospect_path=prospect_path,
            rules_source=rules_source,
            from_step=desde,
            to_step=hasta,
            force_ocr=forzar_ocr,
            reuse_rules=not generar_reglas,
            verify=verificacion_final,
            lang=language.normalize(idioma),
            email=email,
        )
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e

    return session.snapshot()


def _validar_reglas(desde: int, ruta_reglas: Optional[str], generar_reglas: bool) -> None:
    """
    Abort before starting if step 1 will not be able to do its job.

    This is the most expensive failure to discover halfway through: with no rules
    step 3 has nothing to compare against, and the user already waited out step 2
    for nothing.
    """
    if desde > 1:
        return

    if generar_reglas:
        if not _disposition_documents():
            raise HTTPException(
                400,
                "No hay documentos de disposiciones para generar reglas en "
                f"{settings.dispositions_sources_dir}. Dejá ahí los PDF/DOCX/MD de las normas.",
            )
        return

    if not ruta_reglas and not list_existing_rules_dirs(settings):
        raise HTTPException(
            400,
            "Todavía no hay reglas extraídas de las disposiciones: es el primer paso. "
            "Elegí «generar reglas nuevas» para extraerlas (se hace una vez y se "
            "reutilizan en las próximas corridas).",
        )


async def _resolve_prospect(archivo: Optional[UploadFile], ruta: Optional[str]) -> Path:
    """Save the uploaded file, or validate the path chosen from the examples."""
    if archivo is not None and archivo.filename:
        suffix = Path(archivo.filename).suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise HTTPException(
                400, f"Extensión no soportada: {suffix}. Soportadas: {sorted(SUPPORTED_SUFFIXES)}"
            )
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        destination = UPLOADS_DIR / Path(archivo.filename).name
        with destination.open("wb") as f:
            shutil.copyfileobj(archivo.file, f)
        return destination

    if ruta:
        candidate = Path(ruta)
        if not candidate.is_file():
            raise HTTPException(400, f"El prospecto no existe: {candidate}")
        return candidate

    raise HTTPException(400, "Hace falta subir un archivo o elegir un prospecto de ejemplo")


@app.post("/api/corridas/{run_id}/cancelar")
def cancelar_corrida(run_id: str) -> Dict[str, Any]:
    """Request cancellation; the pipeline stops at the next checkpoint."""
    if manager.get(run_id) is None:
        raise HTTPException(404, "Corrida desconocida")
    return {"cancelacion_pedida": manager.cancel(run_id)}


@app.get("/api/corridas/{run_id}")
def estado_corrida(run_id: str) -> Dict[str, Any]:
    session = manager.get(run_id)
    if session is None:
        raise HTTPException(404, "Corrida desconocida")
    return session.snapshot()


@app.get("/api/corridas/{run_id}/eventos")
async def eventos(run_id: str, cursor: int = 0) -> StreamingResponse:
    """
    SSE stream of the run's events.

    `cursor` is the index of the next expected event, so reconnecting or reloading
    the page neither loses nor duplicates anything.
    """
    session = manager.get(run_id)
    if session is None:
        raise HTTPException(404, "Corrida desconocida")

    async def generator():
        position = cursor
        while True:
            pending = session.since(position)
            for event in pending:
                position += 1
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
            if session.status != "running" and not session.since(position):
                break
            await asyncio.sleep(0.15)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ============================================================================
# Artifacts
# ============================================================================

@app.get("/api/archivo")
def archivo(path: str, descargar: bool = False) -> FileResponse:
    """Serve an artifact of the run, as long as it lives inside the project."""
    target = Path(path).resolve()
    allowed_roots: List[Path] = [
        settings.corridas_dir.resolve(),
        settings.exploded_dir.resolve(),
        UPLOADS_DIR.resolve(),
    ]
    if not any(target.is_relative_to(base) for base in allowed_roots):
        raise HTTPException(403, "Ruta fuera de las carpetas de la aplicación")
    if not target.is_file():
        raise HTTPException(404, f"No existe: {target}")

    return FileResponse(
        target,
        filename=target.name if descargar else None,
        media_type="application/pdf" if target.suffix.lower() == ".pdf" else None,
    )
