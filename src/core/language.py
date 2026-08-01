"""
Language of what the pipeline SAYS to the person who launched it.

The web's first screen asks for a language, and that choice reaches the one thing
the pipeline writes to a human outside the page: the email that delivers the
results. Everything else — the leaflet, the compliance report, the notes, the
final verification — is ALWAYS in Spanish, whatever the interface is set to.

That is deliberate, not an omission. The dispositions demand literal Spanish
wording (a rule can require the exact phrase «ANTE LA MENOR DUDA CONSULTE A SU
MÉDICO»), the report is the evidence backing a filing before an Argentine
regulator, and both are read by the same regulatory-affairs teams. Translating
any of it would produce a document that reads better and is worth less.

It lives here, as module state, like `core.cancellation` and `core.usage`: one run
at a time, set by the orchestrator when the run starts and cleared when it ends.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

DEFAULT = "es"
SUPPORTED = ("es", "en")

_current: str = DEFAULT


def normalize(language: str | None) -> str:
    """Reduce whatever arrived to a supported code ("es-AR" → "es")."""
    code = (language or "").strip().lower().replace("_", "-").split("-")[0]
    return code if code in SUPPORTED else DEFAULT


def use(language: str | None) -> str:
    """Set the run's language. Returns the code that actually took effect."""
    global _current
    _current = normalize(language)
    logger.info(f"Idioma de la corrida: {_current}")
    return _current


def current() -> str:
    """The run's language."""
    return _current


def is_english() -> bool:
    return _current == "en"


def reset() -> None:
    """Back to Spanish. The orchestrator calls it when the run ends."""
    global _current
    _current = DEFAULT
