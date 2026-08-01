"""
Background execution of the pipeline, with the run observable live.

The pipeline is synchronous and writes to the console; here it runs on a thread and
its events are captured by subscribing to `core.console` (structured events) and to
logging (log lines). Each run keeps all of its events in memory, so a client that
connects late — or reloads the page — receives the full history and continues from
there.

One run executes at a time: the pipeline reconfigures the process's logging and
writes to stdout, so two simultaneous runs would trample each other.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from core import cancellation, console, language
from core.run_context import RunContext
from core.usage import tracker
from pipeline.orchestrator import LAST_STEP, run_pipeline

logger = logging.getLogger(__name__)


@dataclass
class RunSession:
    """One run launched from the web, plus its events."""

    run_id: str
    params: Dict[str, Any]
    status: str = "running"  # running | ok | error | cancelado
    stamp: Optional[str] = None
    error: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    events: List[Dict[str, Any]] = field(default_factory=list)
    usage: Dict[str, Any] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def append(self, event: Dict[str, Any]) -> None:
        with self._lock:
            event = {**event, "seq": len(self.events), "t": round(time.time() - self.started_at, 2)}
            self.events.append(event)

    def since(self, cursor: int) -> List[Dict[str, Any]]:
        """Events from `cursor` onwards (index of the next event to send)."""
        with self._lock:
            return self.events[cursor:]

    def snapshot(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "stamp": self.stamp,
            "error": self.error,
            "params": self.params,
            "elapsed": round((self.finished_at or time.time()) - self.started_at, 1),
            "artifacts": self.artifacts(),
            "usage": self.usage,
        }

    def artifacts(self) -> Dict[str, Any]:
        """The run's artifacts, read from the manifest (empty if there are none yet)."""
        if not self.stamp:
            return {}
        try:
            return RunContext.load(self.stamp).artifacts
        except Exception:  # noqa: BLE001 — the manifest may not exist yet
            return {}


class _LogEventHandler(logging.Handler):
    """Handler that forwards log lines to the run as events."""

    def __init__(self, session: RunSession):
        super().__init__()
        self.session = session

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.session.append({
                "kind": "log",
                "level": record.levelname.lower(),
                "name": record.name,
                "text": record.getMessage(),
            })
        except Exception:  # noqa: BLE001
            pass


class RunManager:
    """Launches runs and keeps them reachable by `run_id`."""

    def __init__(self) -> None:
        self._sessions: Dict[str, RunSession] = {}
        self._lock = threading.Lock()
        self._active: Optional[str] = None

    def get(self, run_id: str) -> Optional[RunSession]:
        return self._sessions.get(run_id)

    def active(self) -> Optional[RunSession]:
        with self._lock:
            return self._sessions.get(self._active) if self._active else None

    def cancel(self, run_id: str) -> bool:
        """
        Request cancellation of an in-flight run.

        Cancellation is cooperative: the pipeline stops at the next checkpoint, so
        it takes at most as long as the model call currently in flight.

        Returns:
            True if there was anything to cancel.
        """
        session = self._sessions.get(run_id)
        if session is None or session.status != "running":
            return False
        cancellation.request()
        session.append({"kind": "warn", "text": "Cancelación pedida; cortando al terminar la llamada en curso…"})
        return True

    def start(
        self,
        prospect_path: Optional[Path] = None,
        rules_source: Optional[Path] = None,
        from_step: int = 1,
        to_step: int = LAST_STEP,
        force_ocr: bool = False,
        reuse_rules: bool = True,
        verify: bool = False,
        lang: str = language.DEFAULT,
        email: Optional[str] = None,
    ) -> RunSession:
        """
        Launch a run in the background.

        Args:
            prospect_path: Leaflet to analyse. May be absent if the run does not
                reach step 2 (a rules-only run, for instance).
            rules_source: RULES FOLDER to reuse in step 1.
            from_step: First step to run.
            to_step: Last step to run.
            force_ocr: Force local OCR in step 2.
            reuse_rules: False to generate the rules from the dispositions.
            verify: Enable step 5, the final verification with Claude.
            lang: Language the run reports in ("es" or "en").
            email: Address to mail the results to when it finishes.

        Raises:
            RuntimeError: If a run is already in progress.
        """
        with self._lock:
            current = self._sessions.get(self._active) if self._active else None
            if current is not None and current.status == "running":
                raise RuntimeError("Ya hay una corrida en curso; esperá a que termine.")

            session = RunSession(
                run_id=uuid.uuid4().hex[:12],
                params={
                    "prospecto": prospect_path.name if prospect_path else None,
                    "reglas": (
                        "generar desde las disposiciones" if not reuse_rules
                        else str(rules_source) if rules_source
                        else "más recientes"
                    ),
                    "desde": from_step,
                    "hasta": to_step,
                    "ocr_forzado": force_ocr,
                    "verificacion_final": verify,
                    "idioma": lang,
                    "email": email,
                },
            )
            self._sessions[session.run_id] = session
            self._active = session.run_id

        cancellation.clear()
        thread = threading.Thread(
            target=self._execute,
            args=(
                session, prospect_path, rules_source,
                from_step, to_step, force_ocr, reuse_rules, verify, lang, email,
            ),
            name=f"pipeline-{session.run_id}",
            daemon=True,
        )
        thread.start()
        return session

    @staticmethod
    def _execute(
        session: RunSession,
        prospect_path: Optional[Path],
        rules_source: Optional[Path],
        from_step: int,
        to_step: int,
        force_ocr: bool,
        reuse_rules: bool = True,
        verify: bool = False,
        lang: str = language.DEFAULT,
        email: Optional[str] = None,
    ) -> None:
        """Thread body: hook up the observers, run the pipeline and release them."""
        unsubscribe = console.subscribe(session.append)
        log_handler = _LogEventHandler(session)
        logging.getLogger().addHandler(log_handler)
        try:
            run_pipeline(
                prospect_path=prospect_path,
                interactive=False,
                reuse_rules=reuse_rules,
                rules_source=rules_source,
                force_ocr=force_ocr,
                from_step=from_step,
                to_step=to_step,
                verify=verify,
                lang=lang,
                email=email,
                # The stamp is recorded as soon as it exists, so a run that fails
                # still stays linked to its folder and its log.
                on_context=lambda ctx: setattr(session, "stamp", ctx.stamp),
            )
            session.status = "ok"
        except cancellation.RunCancelled:
            logger.warning("La corrida se canceló a pedido del usuario")
            session.status = "cancelado"
            session.append({"kind": "warn", "text": "Corrida cancelada"})
        except Exception as e:  # noqa: BLE001 — the error is shown to the user
            logger.exception("La corrida lanzada desde la web falló")
            session.status = "error"
            session.error = str(e)
            session.append({"kind": "error", "text": str(e)})
        finally:
            session.finished_at = time.time()
            # Usage is read here rather than at render time: the tracker is global
            # and the next run resets it.
            session.usage = tracker.snapshot()
            session.append({
                "kind": "done",
                "status": session.status,
                "error": session.error,
                "stamp": session.stamp,
                "artifacts": session.artifacts(),
                "usage": session.usage,
            })
            logging.getLogger().removeHandler(log_handler)
            unsubscribe()
            cancellation.clear()
            # The language is global and the process outlives the run: a failed
            # English run must not leave the laboratory answering in English.
            language.reset()


manager = RunManager()
