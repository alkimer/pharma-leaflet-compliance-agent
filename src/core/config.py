"""
Central project configuration, read once from `.env`.

Replaces the scattered `os.getenv` calls and hardcoded paths that used to live in
every script. Any module just does `from core.config import settings`.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# src/core/config.py → src/core → src → repo root
PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(dotenv_path=PROJECT_ROOT / ".env")


def _env_str(name: str, default: str) -> str:
    raw = os.getenv(name)
    return raw.strip() if raw and raw.strip() else default


def _env_float(name: str, default: Optional[float], lo: float = None, hi: float = None) -> Optional[float]:
    """Read a float from the environment, validating its range; falls back to the default on error."""
    raw = os.getenv(name)
    if not raw or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except ValueError:
        logger.warning(f"{name} inválido en .env: {raw!r}. Usando {default}")
        return default
    if lo is not None and hi is not None and not (lo <= value <= hi):
        logger.warning(f"{name} fuera de rango ({lo}-{hi}): {value}. Usando {default}")
        return default
    return value


def _env_bool(name: str, default: bool) -> bool:
    """Read a boolean from the environment (`1/true/yes/si/on` are True)."""
    raw = os.getenv(name)
    if not raw or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "si", "sí", "on")


def _env_int(name: str, default: Optional[int]) -> Optional[int]:
    """Read a positive int from the environment; falls back to the default on error."""
    raw = os.getenv(name)
    if not raw or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        logger.warning(f"{name} inválido en .env: {raw!r}. Usando {default}")
        return default
    if value <= 0:
        logger.warning(f"{name} debe ser positivo: {value}. Usando {default}")
        return default
    return value


def _env_path(name: str, default: str) -> Path:
    """Read a path from the environment; relative values resolve against the repo root."""
    raw = _env_str(name, default)
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (PROJECT_ROOT / path)


def _env_input_dir(name: str, default: str) -> Path:
    """
    Same as `_env_path`, but for folders we only READ from.

    A stale override here is silent and expensive: pointing `BASE_RULES_DIR` at a
    folder that no longer exists makes the app believe there are no rules and ask
    to generate them from scratch. So when the override does not exist and the
    default does, the default wins and the mismatch is logged.
    """
    configured = _env_path(name, default)
    if configured.is_dir():
        return configured

    fallback = PROJECT_ROOT / default
    if fallback.is_dir():
        logger.warning(
            f"{name} apunta a {configured}, que no existe. Usando {fallback}. "
            f"Corregí o borrá {name} en el .env (o en el entorno, que tiene prioridad "
            f"sobre el .env)."
        )
        return fallback
    return configured


@dataclass(frozen=True)
class ModelConfig:
    """Parameters of one LLM agent."""
    model: str
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_output_tokens: Optional[int] = None
    delay_between_calls: float = 0.0


@dataclass(frozen=True)
class VerifierConfig:
    """Parameters of the final verifier (step 5, Claude)."""
    model: str
    effort: str = "high"
    max_output_tokens: int = 64000


@dataclass(frozen=True)
class SMTPConfig:
    """
    Outgoing mail server, used to send a run's results (see `core.mailer`).

    Optional: with no `host`/`sender` the pipeline simply does not mail anything.
    For Gmail the password is an *app password*, not the account's own: Google
    rejects plain passwords over SMTP.
    """

    host: str = ""
    port: int = 587
    user: str = ""
    password: str = ""
    # "Name <address>" or a bare address.
    sender: str = ""
    use_starttls: bool = True
    timeout: float = 30.0

    @property
    def use_ssl(self) -> bool:
        """Port 465 speaks TLS from the first byte; the rest negotiate STARTTLS."""
        return self.port == 465


@dataclass(frozen=True)
class Settings:
    """Effective pipeline configuration."""

    project_root: Path = PROJECT_ROOT
    openai_api_key: Optional[str] = None
    # Only needed if step 5 is enabled.
    anthropic_api_key: Optional[str] = None
    max_retries: int = 5

    # Our own retries (per classification / per rule) for responses that arrived
    # but are unusable. Transport errors are covered by `max_retries`.
    llm_attempts: int = 3
    llm_retry_backoff: float = 1.0

    # Directories
    exploded_dir: Path = PROJECT_ROOT / "disposiciones" / "disposiciones-explotadas"
    corridas_dir: Path = PROJECT_ROOT / "corridas"
    base_rules_dir: Path = PROJECT_ROOT / "disposiciones" / "disposiciones-originales" / "reglas-base"
    dispositions_sources_dir: Path = PROJECT_ROOT / "disposiciones" / "disposiciones-originales" / "fuentes"

    # Agents
    rules_generator: ModelConfig = field(default_factory=lambda: ModelConfig("gpt-4.1"))
    classifier: ModelConfig = field(default_factory=lambda: ModelConfig("gpt-4.1-mini"))
    checker: ModelConfig = field(default_factory=lambda: ModelConfig("gpt-4.1-mini"))
    adequator: ModelConfig = field(default_factory=lambda: ModelConfig("gpt-4.1"))
    verifier: VerifierConfig = field(default_factory=lambda: VerifierConfig("claude-opus-5"))

    # Outgoing mail (optional): results sent to whoever asked for them.
    smtp: SMTPConfig = field(default_factory=SMTPConfig)

    # Local OCR (only for PDFs without a text layer)
    ocr_model_dir: Path = Path("~/modelos/DeepSeek-OCR").expanduser()
    ocr_device: str = "mps"
    ocr_dpi: int = 300
    ocr_target_height: int = 2048

    def require_anthropic_key(self) -> str:
        """Return the Anthropic API key, or fail with an actionable message."""
        if not self.anthropic_api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY no está configurada y el paso 5 la necesita. "
                "Agregala al .env, o corré sin la verificación final."
            )
        return self.anthropic_api_key

    def require_api_key(self) -> str:
        """Return the API key, or fail with an actionable message."""
        if not self.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY no está configurada. Copiá .env.example a .env y completala."
            )
        return self.openai_api_key


def load_settings() -> Settings:
    """Build the Settings object from the environment."""
    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
        max_retries=_env_int("MAX_RETRIES", 5) or 5,
        llm_attempts=_env_int("LLM_ATTEMPTS", 3) or 3,
        llm_retry_backoff=_env_float("LLM_RETRY_BACKOFF_SECONDS", 1.0) or 1.0,
        exploded_dir=_env_path("DISPOSICIONES_EXPLOTADAS_DIR", "disposiciones/disposiciones-explotadas"),
        corridas_dir=_env_path("CORRIDAS_DIR", "corridas"),
        base_rules_dir=_env_input_dir("BASE_RULES_DIR", "disposiciones/disposiciones-originales/reglas-base"),
        dispositions_sources_dir=_env_input_dir("DISPOSITIONS_SOURCES_DIR", "disposiciones/disposiciones-originales/fuentes"),
        rules_generator=ModelConfig(
            model=_env_str("RULES_GENERATOR_MODEL", "gpt-4.1"),
            temperature=_env_float("RULES_GENERATOR_TEMPERATURE", 0.0, lo=0.0, hi=2.0),
            top_p=_env_float("RULES_GENERATOR_TOP_P", None, lo=0.0, hi=1.0),
            delay_between_calls=_env_float("RULES_GENERATOR_DELAY_SECONDS", 0.0) or 0.0,
        ),
        classifier=ModelConfig(
            model=_env_str("CLASSIFIER_MODEL", "gpt-4.1-mini"),
            temperature=_env_float("CLASSIFIER_TEMPERATURE", 0.01, lo=0.0, hi=2.0),
            top_p=_env_float("CLASSIFIER_TOP_P", None, lo=0.0, hi=1.0),
            delay_between_calls=_env_float("CLASSIFIER_DELAY_SECONDS", 0.0) or 0.0,
        ),
        checker=ModelConfig(
            model=_env_str("CHECKER_MODEL", "gpt-4.1-mini"),
            temperature=_env_float("CHECKER_TEMPERATURE", 0.01, lo=0.0, hi=2.0),
            top_p=_env_float("CHECKER_TOP_P", None, lo=0.0, hi=1.0),
            max_output_tokens=_env_int("CHECKER_MAX_COMPLETION_TOKENS", None),
            delay_between_calls=_env_float("CHECKER_DELAY_SECONDS", 0.0) or 0.0,
        ),
        adequator=ModelConfig(
            model=_env_str("ADEQUATOR_ONESHOT_MODEL", "gpt-4.1"),
            temperature=_env_float("ADEQUATOR_ONESHOT_TEMPERATURE", 0.2, lo=0.0, hi=2.0),
            top_p=_env_float("ADEQUATOR_ONESHOT_TOP_P", 1.0, lo=0.0, hi=1.0),
        ),
        verifier=VerifierConfig(
            model=_env_str("VERIFIER_MODEL", "claude-opus-5"),
            effort=_env_str("VERIFIER_EFFORT", "high"),
            max_output_tokens=_env_int("VERIFIER_MAX_TOKENS", 64000) or 64000,
        ),
        smtp=SMTPConfig(
            host=_env_str("SMTP_HOST", ""),
            port=_env_int("SMTP_PORT", 587) or 587,
            user=_env_str("SMTP_USER", ""),
            # Not `_env_str`: an app password is copied and pasted, and it is not
            # ours to normalise beyond the surrounding whitespace.
            password=(os.getenv("SMTP_PASSWORD") or "").strip(),
            # With no SMTP_FROM the sending account is the sender, which is what
            # Gmail forces anyway.
            sender=_env_str("SMTP_FROM", "") or _env_str("SMTP_USER", ""),
            use_starttls=_env_bool("SMTP_STARTTLS", True),
            timeout=_env_float("SMTP_TIMEOUT_SECONDS", 30.0) or 30.0,
        ),
        ocr_model_dir=Path(_env_str("OCR_MODEL_DIR", "~/modelos/DeepSeek-OCR")).expanduser(),
        ocr_device=_env_str("OCR_DEVICE", "mps"),
        ocr_dpi=_env_int("OCR_DPI", 300) or 300,
        ocr_target_height=_env_int("OCR_TARGET_HEIGHT", 2048) or 2048,
    )


settings = load_settings()

# Models offered in step 1's menu (the user can type any other one).
AVAILABLE_MODELS = [
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-4o",
    "gpt-5",
]
