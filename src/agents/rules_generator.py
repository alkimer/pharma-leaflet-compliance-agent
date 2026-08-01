"""
Rules generator: disposition → rules JSON (step 1).

Automates what used to be done by hand in ChatGPT. It uses TWO passes, which is
what produced the rules now sitting in
`disposiciones/disposiciones-originales/reglas-base`:

  1. Draft (`rules_generator_draft.txt`): extracts the rules from the
     disposition's raw text.
  2. Audit (`rules_generator_audit.txt`): receives the text + the draft, checks
     coverage, drops anything unsupported and normalises to the final schema
     consumed by the classifier and the checker (objective /
     verification_procedure / acceptance_criteria / article_reference /
     attach_reference).

A single pass tends to miss requirements and drift from the schema, which is why
both are kept.
"""
from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional

from agents.llm_client import LONG_CALL_RETRIES, MAX_TIMEOUT, LLMClient
from agents.prompts import (
    RULES_GENERATOR_AUDIT_INSTRUCTIONS,
    RULES_GENERATOR_DRAFT_INSTRUCTIONS,
)
from core import cancellation, console
from core.config import ModelConfig, settings

logger = logging.getLogger(__name__)

# Keys the final schema requires at the root level.
REQUIRED_TOP_LEVEL_KEYS = ("disposition_id", "rules")


class RulesGenerator:
    """Turns a disposition's text into a JSON of verifiable rules."""

    def __init__(self, config: Optional[ModelConfig] = None, model: Optional[str] = None):
        """
        Args:
            config: Model parameters; defaults to `settings.rules_generator`.
            model: Shortcut to override just the model (the user picks it in step 1).
        """
        base = config or settings.rules_generator
        self.config = ModelConfig(
            model=model or base.model,
            temperature=base.temperature,
            top_p=base.top_p,
            max_output_tokens=base.max_output_tokens,
            delay_between_calls=base.delay_between_calls,
        )
        # Two passes per regulation, each several minutes long: few retries
        # (see LONG_CALL_RETRIES), or a batch of 18 dispositions never finishes.
        self.draft_client = LLMClient.from_config(
            self.config, RULES_GENERATOR_DRAFT_INSTRUCTIONS, max_retries=LONG_CALL_RETRIES
        )
        self.audit_client = LLMClient.from_config(
            self.config, RULES_GENERATOR_AUDIT_INSTRUCTIONS, max_retries=LONG_CALL_RETRIES
        )
        logger.info(f"RulesGenerator listo (model={self.config.model})")

    # ---- Passes -------------------------------------------------------------

    def _draft(self, disposition_id: str, raw_text: str, timeout: float) -> Dict[str, Any]:
        """Pass 1: draft the rules from the disposition's text."""
        payload = json.dumps(
            {"disposition_id": disposition_id, "raw_text": raw_text},
            ensure_ascii=False,
        )
        response = self.draft_client.run_text(payload, json_mode=True, timeout=timeout)
        logger.debug(f"Borrador para {disposition_id}:\n{response}")
        return json.loads(response)

    def _audit(
        self,
        disposition_id: str,
        raw_text: str,
        draft: Dict[str, Any],
        timeout: float,
    ) -> Dict[str, Any]:
        """Pass 2: audit the draft against the text and normalise to the final schema."""
        payload = json.dumps(
            {"disposition_id": disposition_id, "raw_text": raw_text, "current_json": draft},
            ensure_ascii=False,
        )
        response = self.audit_client.run_text(payload, json_mode=True, timeout=timeout)
        logger.debug(f"Auditoría para {disposition_id}:\n{response}")
        return json.loads(response)

    def generate(
        self,
        disposition_id: str,
        raw_text: str,
        timeout: float = MAX_TIMEOUT,
    ) -> Dict[str, Any]:
        """
        Generate a disposition's rules JSON using both passes.

        Args:
            disposition_id: Identifier to use in the JSON (e.g. "ANMAT_753_2012").
            raw_text: Full text of the disposition, annexes included.
            timeout: Maximum time per call, in seconds (ceiling: MAX_TIMEOUT).

        Returns:
            The normalised rules JSON.

        Raises:
            ValueError: If the result is missing the mandatory keys.
        """
        console.detail(f"pasada 1/2 · borrador ({len(raw_text)} caracteres de norma)")
        draft = self._draft(disposition_id, raw_text, timeout)
        console.detail(f"pasada 1/2 · {len(draft.get('rules', []))} reglas en el borrador")

        console.detail("pasada 2/2 · auditoría y normalización de schema")
        final = self._audit(disposition_id, raw_text, draft, timeout)

        missing = [k for k in REQUIRED_TOP_LEVEL_KEYS if k not in final]
        if missing:
            raise ValueError(f"El JSON generado para {disposition_id} no tiene {missing}")

        # The model may return a different id; we force the requested one so the
        # classifier and the checker can cross-reference the files.
        final["disposition_id"] = disposition_id
        console.detail(f"pasada 2/2 · {len(final.get('rules', []))} reglas finales")
        return final

    # ---- Batch --------------------------------------------------------------

    def generate_batch(
        self,
        documents: List[Path],
        output_dir: Path,
        text_loader,
        timeout: float = MAX_TIMEOUT,
    ) -> Dict[str, Any]:
        """
        Process several dispositions and write one rules JSON per document.

        Args:
            documents: Source files of the dispositions (pdf / md / txt / docx).
            output_dir: Output RULES FOLDER.
            text_loader: Callable that takes a Path and returns its clean text
                (`etl.document_text.extract_text` is injected so the agent is not
                coupled to the ETL).
            timeout: Maximum time per model call (ceiling: MAX_TIMEOUT).

        Returns:
            {"generated": [...], "failed": [...]}
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        generated: List[Dict[str, str]] = []
        failed: List[Dict[str, str]] = []
        total = len(documents)

        for idx, document in enumerate(documents, start=1):
            cancellation.check()
            disposition_id = derive_disposition_id(document)
            console.progress(idx, total, f"{document.name}  →  {disposition_id}")
            try:
                raw_text = text_loader(document)
                if not raw_text.strip():
                    raise ValueError("el documento no tiene texto extraíble")

                rules_json = self.generate(disposition_id, raw_text, timeout=timeout)

                out_path = output_dir / f"{_slug(document.stem)}_rules.json"
                out_path.write_text(
                    json.dumps(rules_json, indent=2, ensure_ascii=False), encoding="utf-8"
                )
                generated.append({
                    "source": str(document),
                    "disposition_id": disposition_id,
                    "rules_file": str(out_path),
                    "rules_count": len(rules_json.get("rules", [])),
                })
                console.ok(
                    f"{disposition_id}: {len(rules_json.get('rules', []))} reglas → "
                    f"{console.path_link(out_path.name)}"
                )
            except Exception as e:
                logger.exception(f"Error generando reglas para {document.name}")
                console.error(f"{document.name}: {e}")
                failed.append({"source": str(document), "error": str(e)})

            if self.config.delay_between_calls > 0:
                time.sleep(self.config.delay_between_calls)

        return {"generated": generated, "failed": failed}


def derive_disposition_id(document: Path) -> str:
    """
    Derive a stable `disposition_id` from the filename.

    Examples:
        "Disposicion_4525-2006 TADALAFILO.pdf"  → "ANMAT_4525_2006"
        "anmar_circular_5_2012.pdf"             → "ANMAT_CIRCULAR_5_2012"
        "Dispo-753-12-–-VENTA-LIBRE.pdf"        → "ANMAT_753_12"
    """
    stem = _strip_accents(document.stem)
    is_circular = "circular" in stem.lower()

    # First number-number pattern in the name (5904-96, 753-12, 4525-2006…).
    pair = re.search(r"(\d{3,4})[-_/](\d{2,4})", stem)
    if pair:
        number, year = pair.group(1), pair.group(2)
    else:
        numbers = re.findall(r"\d+", stem)
        if len(numbers) >= 2:
            number, year = numbers[-2], numbers[-1]
        elif numbers:
            number, year = numbers[0], ""
        else:
            return f"ANMAT_{_slug(stem).upper()}"

    parts = ["ANMAT"]
    if is_circular:
        parts.append("CIRCULAR")
    parts.append(number)
    if year:
        parts.append(year)
    return "_".join(parts)


def _strip_accents(text: str) -> str:
    """Strip accents and diacritics, keeping the base letters."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )


def _slug(text: str) -> str:
    """Safe filename: no accents, spaces or odd punctuation."""
    clean = _strip_accents(text)
    clean = re.sub(r"[^\w\-]+", "_", clean)
    return re.sub(r"_{2,}", "_", clean).strip("_")
