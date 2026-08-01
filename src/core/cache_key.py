"""
Grouping key for OpenAI's prompt cache.

Every call in a run shares the same prefix — the agent instructions plus the
leaflet text — so they should land in the same server-side cache.
`prompt_cache_key` pins that routing; without it a call may end up on another
machine and pay the whole prefix at full price.

The key is derived from the leaflet: same run (and same re-run over the same
leaflet) ⇒ same key.
"""
from __future__ import annotations

import hashlib


def for_prospect(prospect_text: str, agent: str) -> str:
    """
    Stable key for one agent's calls over one leaflet.

    Args:
        prospect_text: Leaflet text, which is the bulk of the shared prefix.
        agent: Agent name; instructions differ per agent, so each one gets its
            own prefix and its own key.
    """
    digest = hashlib.sha256(prospect_text.encode("utf-8")).hexdigest()[:16]
    return f"{agent}-{digest}"
