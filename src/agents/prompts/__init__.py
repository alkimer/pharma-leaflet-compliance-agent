"""
The agents' prompts (system instructions), versioned in the repo.

Since the migration to the Responses API they no longer live in the OpenAI
dashboard tied to an `assistant_id`: they are now passed as `instructions=` on
every call and edited as .txt files of this package.

The prompt text itself is in Spanish: the agents reason over Argentine regulation
and produce Spanish output.
"""
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent


def _load(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


# Step 1 — disposition → rules JSON (two passes: draft and audit/normalisation)
RULES_GENERATOR_DRAFT_INSTRUCTIONS = _load("rules_generator_draft.txt")
RULES_GENERATOR_AUDIT_INSTRUCTIONS = _load("rules_generator_audit.txt")

# Step 3 — classification of applicable dispositions and rule checking
CLASSIFIER_INSTRUCTIONS = _load("classifier.txt")
CHECKER_INSTRUCTIONS = _load("checker.txt")

# Step 4 — leaflet adequation
ADEQUATOR_INSTRUCTIONS = _load("adequator.txt")

# Paso 5 — verificación final (Claude), opcional
FINAL_VERIFIER_INSTRUCTIONS = _load("final_verifier.txt")

__all__ = [
    "RULES_GENERATOR_DRAFT_INSTRUCTIONS",
    "RULES_GENERATOR_AUDIT_INSTRUCTIONS",
    "CLASSIFIER_INSTRUCTIONS",
    "CHECKER_INSTRUCTIONS",
    "ADEQUATOR_INSTRUCTIONS",
    "FINAL_VERIFIER_INSTRUCTIONS",
]
