"""
Checker for ANMAT rule compliance over a leaflet (step 3b).

For every rule of a disposition it makes ONE stateless call with the full leaflet
+ the disposition header + the rule, and returns the compliance status
(ok / missing / not_applicable / not_evaluable).

Because each call is self-contained and bounded in size (leaflet + 1 rule), there
is no accumulated context degradation.

Every rule is retried on unusable responses. If a rule cannot be evaluated, the
run stops: a report with unverified rules cannot support the claim that the
leaflet complies.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

from agents.llm_client import LLMClient
from agents.prompts import CHECKER_INSTRUCTIONS
from core import cache_key, cancellation, console
from core.config import ModelConfig, settings
from core.retry import with_retries

logger = logging.getLogger(__name__)

VALID_STATUSES = ("ok", "missing", "not_applicable", "not_evaluable")

# Presentation order of the evaluations in the report.
STATUS_ORDER = {"ok": 1, "not_applicable": 2, "not_evaluable": 3, "missing": 4, "error": 5}

# Rule fields copied into the result so the report has them at hand.
_RULE_PASSTHROUGH = {
    "objective": "",
    "verification_procedure": "",
    "acceptance_criteria": "",
    "must_include_phrases": [],
    "article_reference": "",
    "attach_reference": [],
}


class ComplianceChecker:
    """
    Checks compliance with every rule of a disposition.

    Possible statuses:
    - "ok": the rule applies and is met.
    - "missing": the rule applies but is NOT met.
    - "not_applicable": the rule does NOT apply (out of scope).
    - "not_evaluable": the rule applies but cannot be verified with the available info.
    """

    def __init__(self, config: Optional[ModelConfig] = None):
        """
        Args:
            config: Model parameters; defaults to `settings.checker`.
        """
        self.config = config or settings.checker
        self.delay_between_calls = self.config.delay_between_calls
        self.client = LLMClient.from_config(self.config, CHECKER_INSTRUCTIONS)
        logger.info(f"ComplianceChecker listo (model={self.config.model})")

    def _evaluate_rule(
        self,
        prospect_text: str,
        disposition_header: Dict[str, Any],
        disposition_id: str,
        rule: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Evaluate ONE rule: send {prospect_text, disposition, rule} and parse the JSON."""
        rule_id = rule.get("rule_id", "N/A")

        # Order matters: the leaflet is the prefix shared by every rule and is
        # what the cache covers; the rule, which changes on each call, goes last.
        message = {
            "prospect_text": prospect_text,
            "disposition": disposition_header,
            "rule": rule,
        }
        response = self.client.run(
            input=json.dumps(message, ensure_ascii=False, indent=2),
            json_mode=True,
            cache_key=cache_key.for_prospect(prospect_text, "checker"),
        )
        logger.debug(f"Respuesta del checker (regla {rule_id}): {response.output_text}")

        result = json.loads(response.output_text)
        result["response_id"] = response.id

        # Attach the rule's original fields (the report needs them).
        for field, fallback in _RULE_PASSTHROUGH.items():
            result[field] = rule.get(field, fallback)

        # An incomplete response, or one with an invalid status, cannot be dumped
        # into the report: treat it as an error so the retry layer repeats it.
        required = ["disposition_id", "rule_id", "status", "evidence_snippets", "checker_notes"]
        missing_fields = [f for f in required if f not in result]
        if missing_fields:
            raise ValueError(f"respuesta incompleta, faltan los campos {missing_fields}")
        if result.get("status") not in VALID_STATUSES:
            raise ValueError(f"estado inválido: {result.get('status')!r}")

        return result

    def check(self, prospect_text: str, disposition: Dict[str, Any]) -> Dict[str, Any]:
        """
        Check compliance with every rule of a disposition.

        Args:
            prospect_text: Full text of the leaflet.
            disposition: The whole disposition, including the `rules` array.

        Returns:
            {"disposition_id": str, "total_rules": int, "evaluations": [...]}

        Raises:
            RuntimeError: If any rule could not be evaluated even after the
                retries.
        """
        disposition_id = disposition.get("disposition_id", "UNKNOWN")
        rules: List[Dict[str, Any]] = disposition.get("rules", [])
        disposition_header = {k: v for k, v in disposition.items() if k != "rules"}

        console.info(f"{disposition_id}: verificando {len(rules)} reglas")

        evaluations: List[Dict[str, Any]] = []

        for idx, rule in enumerate(rules, start=1):
            cancellation.check()
            rule_id = rule.get("rule_id", idx)
            evaluation = with_retries(
                lambda: self._evaluate_rule(
                    prospect_text=prospect_text,
                    disposition_header=disposition_header,
                    disposition_id=disposition_id,
                    rule=rule,
                ),
                description=f"Evaluación de la regla {rule_id} de {disposition_id}",
                attempts=settings.llm_attempts,
                base_delay=settings.llm_retry_backoff,
                on_retry=lambda attempt, delay, error: console.warn(
                    f"regla {rule_id}: intento {attempt} falló ({error}); "
                    f"reintentando en {delay:.0f}s"
                ),
            )
            evaluations.append(evaluation)
            console.status_line(
                evaluation.get("status", "unknown"),
                f"[{idx}/{len(rules)}] regla {rule_id}: {_truncate(rule.get('objective', ''), 70)}",
            )

            if self.delay_between_calls > 0:
                time.sleep(self.delay_between_calls)

        evaluations.sort(
            key=lambda x: (STATUS_ORDER.get(x.get("status", "error"), 999), x.get("rule_id") or 0)
        )

        return {
            "disposition_id": disposition_id,
            "total_rules": len(rules),
            "evaluations": evaluations,
        }


def _truncate(text: str, limit: int) -> str:
    """Collapse whitespace and clip to `limit` characters for one-line logs."""
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
