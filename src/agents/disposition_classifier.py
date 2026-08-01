"""
Classifier of the dispositions applicable to a leaflet (step 3a).

For every disposition in the RULES FOLDER it makes ONE stateless call sending the
leaflet + the disposition header (everything except the `rules` array), and
decides whether it applies.

Every call is retried on transient errors. If a disposition cannot be classified,
the run stops: an analysis with unexamined dispositions is useless for deciding
whether the leaflet complies.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from agents.llm_client import LLMClient
from agents.prompts import CLASSIFIER_INSTRUCTIONS
from core import cache_key, cancellation, console
from core.config import ModelConfig, settings
from core.retry import with_retries

logger = logging.getLogger(__name__)


class DispositionClassifier:
    """
    Works out which dispositions apply to a leaflet.

    Flow:
    1. Load the disposition headers from `rules_dir`.
    2. One call per header, with {prospect_text, disposition}.
    3. Return a consolidated JSON with the classifications.
    """

    def __init__(self, rules_dir: Path, config: Optional[ModelConfig] = None):
        """
        Args:
            rules_dir: RULES FOLDER holding the disposition JSONs.
            config: Model parameters; defaults to `settings.classifier`.
        """
        self.rules_dir = Path(rules_dir)
        if not self.rules_dir.is_dir():
            raise FileNotFoundError(f"La carpeta de reglas no existe: {self.rules_dir}")

        self.config = config or settings.classifier
        self.delay_between_calls = self.config.delay_between_calls
        self.client = LLMClient.from_config(self.config, CLASSIFIER_INSTRUCTIONS)

        logger.info(f"DispositionClassifier listo (model={self.config.model})")
        logger.info(f"Carpeta de reglas: {self.rules_dir}")

    def _rules_files(self) -> List[Path]:
        """Paths of every disposition JSON in the rules folder."""
        files = sorted(self.rules_dir.glob("*.json"))
        if not files:
            logger.warning(f"No se encontraron archivos JSON en {self.rules_dir}")
        return files

    @staticmethod
    def _extract_header(rule_file: Path) -> Dict[str, Any]:
        """Extract a disposition's header: everything except the `rules` array."""
        data = json.loads(rule_file.read_text(encoding="utf-8-sig"))
        return {k: v for k, v in data.items() if k != "rules"}

    def _classify_one(self, prospect_text: str, header: Dict[str, Any]) -> Dict[str, Any]:
        """Classify ONE disposition: send {prospect_text, disposition} and parse the JSON."""
        # The leaflet goes first: it is the prefix the 15 calls share and the one
        # the cache covers. The disposition, which changes, goes last.
        message = {"prospect_text": prospect_text, "disposition": header}
        response = self.client.run(
            input=json.dumps(message, ensure_ascii=False, indent=2),
            json_mode=True,
            cache_key=cache_key.for_prospect(prospect_text, "classifier"),
        )
        logger.debug(f"Respuesta del clasificador: {response.output_text}")

        result = json.loads(response.output_text)
        result["response_id"] = response.id
        return result

    def classify(self, prospect_text: str) -> Dict[str, Any]:
        """
        Classify which dispositions are applicable to the leaflet.

        Returns:
            {
                "total_dispositions_evaluated": int,
                "classifications": [{disposition_id, applies, match_score, reason,
                                     rule_file, response_id}, ...]
            }

        Raises:
            RuntimeError: If any disposition could not be classified even after
                the retries.
        """
        rules_files = self._rules_files()
        if not rules_files:
            return {"total_dispositions_evaluated": 0, "classifications": []}

        console.info(f"Evaluando {len(rules_files)} disposiciones contra el prospecto")
        console.detail(
            f"prospecto: {len(prospect_text)} caracteres · modelo: {self.config.model} · "
            f"hasta {settings.llm_attempts} intento(s) por disposición"
        )

        classifications: List[Dict[str, Any]] = []
        total = len(rules_files)

        for idx, rule_file in enumerate(rules_files, start=1):
            cancellation.check()
            try:
                header = self._extract_header(rule_file)
            except Exception as e:
                raise RuntimeError(f"No se pudo leer la disposición {rule_file.name}: {e}") from e

            disposition_id = header.get("disposition_id", "N/A")
            console.progress(idx, total, f"{disposition_id}")

            result = with_retries(
                lambda: self._classify_one(prospect_text, header),
                description=f"Clasificación de {disposition_id} ({rule_file.name})",
                attempts=settings.llm_attempts,
                base_delay=settings.llm_retry_backoff,
                on_retry=lambda attempt, delay, error: console.warn(
                    f"{disposition_id}: intento {attempt} falló ({error}); "
                    f"reintentando en {delay:.0f}s"
                ),
            )
            result["rule_file"] = rule_file.name
            classifications.append(result)

            applies = result.get("applies")
            mark = "APLICA" if applies else "no aplica"
            color = console.BRIGHT_GREEN if applies else console.GREY
            console.detail(
                f"{color}{mark}{console.RESET} · score={result.get('match_score')} · "
                f"{_truncate(result.get('reason', ''), 90)}"
            )

            if self.delay_between_calls > 0:
                time.sleep(self.delay_between_calls)

        return {
            "total_dispositions_evaluated": total,
            "classifications": classifications,
        }

    def load_disposition(self, disposition_id: str) -> Optional[Dict[str, Any]]:
        """
        Load the full disposition (rules included), looking it up by `disposition_id`.

        Returns:
            The disposition dict, or None if it was not found.
        """
        return load_disposition(self.rules_dir, disposition_id)


def load_disposition(rules_dir: Path, disposition_id: str) -> Optional[Dict[str, Any]]:
    """
    Find the JSON in `rules_dir` whose `disposition_id` matches and return it whole.

    The lookup is by content rather than by filename because the names come from
    the original PDFs and follow no convention.

    Returns:
        The disposition dict (including `rules`), or None if it was not found.
    """
    for json_file in sorted(Path(rules_dir).glob("*.json")):
        try:
            data = json.loads(json_file.read_text(encoding="utf-8-sig"))
        except Exception as e:
            logger.warning(f"Error leyendo {json_file.name}: {e}")
            continue
        if data.get("disposition_id") == disposition_id:
            logger.info(f"Disposición {disposition_id} cargada desde {json_file.name}")
            return data
    logger.warning(f"No se encontró archivo para la disposición {disposition_id}")
    return None


def _truncate(text: str, limit: int) -> str:
    """Collapse whitespace and clip to `limit` characters for one-line logs."""
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
