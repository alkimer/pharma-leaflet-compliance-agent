"""
Context of one pipeline run.

This is the piece that makes each step feed on the previous one's output: the
`RunContext` pins the `<timestamp>`, creates the folder structure and keeps a
`manifest.json` with the artifacts every step produced. Steps 2, 3 and 4 read
from the manifest instead of receiving hardcoded paths.

On-disk layout:

    disposiciones/disposiciones-explotadas/<timestamp>/   only if step 1 generated
        reglas-extraidas/     rules as JSON — the pipeline's RULES FOLDER
        fuentes/              source regulations they were extracted from

    corridas/<timestamp>/
        documento-subido/     original leaflet + clean text (step 2)
        resultado/            compliance report json/md/pdf/html (step 3)
        documento-adecuado/   adequated leaflet json/txt/docx (step 4)
        verificacion-final/   review with Claude (step 5, only if enabled)
        logs/                 full log of the run
        manifest.json         index of every step's artifacts
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import Settings, settings as default_settings

STAMP_FORMAT = "%Y%m%d-%H%M"

# Subfolder of each run holding the rule JSONs (the RULES FOLDER).
RULES_DIR_NAME = "reglas-extraidas"


def make_stamp(moment: Optional[datetime] = None) -> str:
    """
    Compute the run's `<timestamp>` (step 0).

    Format YYYYmmDD-HHMM (e.g. 20260728-1745). No `:` is used, so the value is a
    valid folder name on any filesystem.
    """
    return (moment or datetime.now()).strftime(STAMP_FORMAT)


@dataclass
class RunContext:
    """Paths and artifacts of a run identified by its `<timestamp>`."""

    stamp: str
    settings: Settings = field(default_factory=lambda: default_settings)
    artifacts: Dict[str, Any] = field(default_factory=dict)

    # ---- Directories --------------------------------------------------------

    @property
    def exploded_root(self) -> Path:
        """disposiciones/disposiciones-explotadas/<timestamp>/"""
        return self.settings.exploded_dir / self.stamp

    @property
    def source_docs_dir(self) -> Path:
        """
        <run>/fuentes — source regulations of the run.

        Only exists when step 1 generated the rules: when they are reused there is
        nothing to preserve. `step1_rules` creates it while copying.
        """
        return self.exploded_root / "fuentes"

    @property
    def rules_dir(self) -> Path:
        """
        <run>/reglas-extraidas — where the rules step 1 GENERATES land.

        It only exists if that run generated them. When already-extracted rules
        are reused nothing is created: the manifest points at the original folder
        and step 3 reads from there. To know which RULES FOLDER a run used, look
        at the manifest, not at this path.
        """
        return self.exploded_root / RULES_DIR_NAME

    @property
    def run_root(self) -> Path:
        """corridas/<timestamp>/"""
        return self.settings.corridas_dir / self.stamp

    @property
    def uploaded_dir(self) -> Path:
        """corridas/<timestamp>/documento-subido — original leaflet + clean text."""
        return self.run_root / "documento-subido"

    @property
    def result_dir(self) -> Path:
        """corridas/<timestamp>/resultado — compliance report."""
        return self.run_root / "resultado"

    @property
    def adequated_dir(self) -> Path:
        """corridas/<timestamp>/documento-adecuado — adequated leaflet."""
        return self.run_root / "documento-adecuado"

    @property
    def verification_dir(self) -> Path:
        """corridas/<timestamp>/verificacion-final — step 5's report (optional)."""
        return self.run_root / "verificacion-final"

    @property
    def logs_dir(self) -> Path:
        """corridas/<timestamp>/logs"""
        return self.run_root / "logs"

    @property
    def log_file(self) -> Path:
        return self.logs_dir / f"pipeline_{self.stamp}.log"

    @property
    def manifest_path(self) -> Path:
        return self.run_root / "manifest.json"

    def all_dirs(self) -> List[Path]:
        # `rules_dir` and `source_docs_dir` are left out on purpose: they only make
        # sense if step 1 generates rules, and in that case step 1 creates them.
        # `verification_dir` is left out too: step 5 is optional and creates it.
        return [
            self.uploaded_dir,
            self.result_dir,
            self.adequated_dir,
            self.logs_dir,
        ]

    def prepare(self) -> "RunContext":
        """Create every folder of the run (idempotent)."""
        for directory in self.all_dirs():
            directory.mkdir(parents=True, exist_ok=True)
        return self

    # ---- Artifacts / manifest ----------------------------------------------

    def record(self, step: str, **values: Any) -> None:
        """
        Record the artifacts produced by a step and persist the manifest.

        Paths are serialised as absolute strings so the manifest stays readable
        and reusable from another tool.
        """
        entry = self.artifacts.setdefault(step, {})
        for key, value in values.items():
            entry[key] = _serialize(value)
        self.save_manifest()

    def get(self, step: str, key: str, default: Any = None) -> Any:
        """Read an artifact recorded by an earlier step."""
        return self.artifacts.get(step, {}).get(key, default)

    def get_path(self, step: str, key: str) -> Optional[Path]:
        """Read an artifact that happens to be a path."""
        raw = self.get(step, key)
        return Path(raw) if raw else None

    def save_manifest(self) -> Path:
        """Write corridas/<timestamp>/manifest.json."""
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "stamp": self.stamp,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            # `rules_dir` does not belong here: the RULES FOLDER a run used may be
            # another run's or the base one, and step 1 records that in
            # `steps.paso1_reglas.rules_dir`, which is the source of truth.
            "paths": {
                "run_root": str(self.run_root),
                "uploaded_dir": str(self.uploaded_dir),
                "result_dir": str(self.result_dir),
                "adequated_dir": str(self.adequated_dir),
            },
            "steps": self.artifacts,
        }
        self.manifest_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return self.manifest_path

    @classmethod
    def load(cls, stamp: str, settings: Optional[Settings] = None) -> "RunContext":
        """
        Recover an existing run from its `<timestamp>`.

        This is what allows an interrupted pipeline to be resumed: the steps
        already done remain recorded in the manifest.
        """
        ctx = cls(stamp=stamp, settings=settings or default_settings)
        if ctx.manifest_path.exists():
            data = json.loads(ctx.manifest_path.read_text(encoding="utf-8"))
            ctx.artifacts = data.get("steps", {})
        return ctx

    @classmethod
    def list_existing(cls, settings: Optional[Settings] = None) -> List[str]:
        """Return the `<timestamp>` of every existing run, newest first."""
        cfg = settings or default_settings
        if not cfg.corridas_dir.exists():
            return []
        return sorted((d.name for d in cfg.corridas_dir.iterdir() if d.is_dir()), reverse=True)


def list_existing_rules_dirs(settings: Optional[Settings] = None) -> List[Path]:
    """
    Return the available RULES FOLDERs, newest first.

    The repo's base rules come last, as a fallback.
    """
    cfg = settings or default_settings
    found: List[Path] = []
    if cfg.exploded_dir.exists():
        for run_dir in sorted(cfg.exploded_dir.iterdir(), reverse=True):
            candidate = run_dir / RULES_DIR_NAME
            if candidate.is_dir() and any(candidate.glob("*.json")):
                found.append(candidate)
    if cfg.base_rules_dir.is_dir() and any(cfg.base_rules_dir.glob("*.json")):
        found.append(cfg.base_rules_dir)
    return found


def _serialize(value: Any) -> Any:
    """Turn Paths (and collections of Paths) into strings for the manifest."""
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_serialize(v) for v in value]
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    return value
