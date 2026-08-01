"""
Application-layer retries, complementary to the SDK's own.

There are two retry layers and they do not overlap:

    OpenAI SDK      transport errors: timeouts, dropped connections, 429s and
                    5xx. It handles them on its own, with backoff, according to
                    MAX_RETRIES (see `agents.llm_client`). By the time the SDK
                    raises, it has either exhausted its retries or hit a
                    deterministic error (400/401/403/404): in neither case is
                    insisting worth it.

    this layer      the response arrived but is unusable: it does not parse as
                    JSON, it is truncated, it is missing fields. The SDK cannot
                    detect this because as far as it is concerned the call
                    succeeded.

The pipeline's policy is to never accept partial analyses: if a call does not
recover after every attempt, the error propagates and the run fails.
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def is_retryable(error: BaseException) -> bool:
    """
    True if the call that produced `error` is worth repeating.

    OpenAI SDK errors are excluded: either they were already retried up to
    MAX_RETRIES, or they are deterministic and will fail again. Retrying them
    here would only multiply the number of calls.
    """
    try:
        from openai import OpenAIError
    except ImportError:
        return True
    return not isinstance(error, OpenAIError)


def with_retries(
    operation: Callable[[], T],
    *,
    description: str,
    attempts: int,
    base_delay: float = 1.0,
    on_retry: Optional[Callable[[int, float, BaseException], None]] = None,
) -> T:
    """
    Run `operation`, retrying with exponential backoff.

    Args:
        operation: Zero-argument callable to execute.
        description: What was being attempted, for the error messages.
        attempts: Maximum number of attempts (1 = no retries).
        base_delay: First retry's wait, in seconds; doubles on every retry.
        on_retry: Callback (attempt, delay, error) invoked before each retry.

    Returns:
        Whatever `operation` returns.

    Raises:
        RuntimeError: If the attempts ran out or the error is not retryable.
    """
    attempts = max(1, attempts)
    last_error: Optional[BaseException] = None
    used = 0

    for attempt in range(1, attempts + 1):
        used = attempt
        try:
            return operation()
        except Exception as error:
            last_error = error
            if not is_retryable(error) or attempt == attempts:
                break

            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                f"{description}: intento {attempt}/{attempts} falló ({error}). "
                f"Reintentando en {delay:.0f}s"
            )
            if on_retry is not None:
                on_retry(attempt, delay, error)
            time.sleep(delay)

    detail = (
        "el SDK ya agotó sus reintentos o el error no es recuperable"
        if last_error is not None and not is_retryable(last_error)
        else f"se agotaron los {used} intento(s)"
    )
    raise RuntimeError(f"{description}: {detail}. Último error: {last_error}") from last_error
