"""
Cooperative cancellation of the current run.

You cannot kill a thread in Python, so cancellation is cooperative: the web app
records the request with `request()` and the pipeline's long loops call `check()`
between one model call and the next. When a request is pending, `check()` raises
`RunCancelled` and the exception propagates like any other error: the run stops
and step 4 never executes.

The checkpoints sit where the pipeline spends most of its time — one model call
per disposition and one per rule — so cancelling takes at most as long as the
in-flight call.

There is a single global token because the app runs one pipeline at a time.
"""
from __future__ import annotations

import threading


class RunCancelled(RuntimeError):
    """The run was cancelled at the user's request."""


_requested = threading.Event()


def request() -> None:
    """Ask the current run to stop."""
    _requested.set()


def clear() -> None:
    """Clear the pending request. Called when each run starts."""
    _requested.clear()


def is_requested() -> bool:
    return _requested.is_set()


def check() -> None:
    """
    Checkpoint: abort the run if a cancellation is pending.

    Raises:
        RunCancelled: If cancellation was requested.
    """
    if _requested.is_set():
        raise RunCancelled("La corrida se canceló a pedido del usuario")
