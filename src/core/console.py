"""
Console: coloured logging, step banners and interactive input helpers.

All of the pipeline's "human" output goes through here so the 4 steps look
consistent. Colours switch themselves off when the output is not a TTY or when
the NO_COLOR environment variable is set.

Besides printing, every function emits a structured event to the subscribers
registered with `subscribe()`. The web interface hooks into that to show the run
live without having to parse coloured stdout.
"""
from __future__ import annotations

import logging
import os
import re
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

_WIDTH = 88

# ============================================================================
# Events (for non-terminal consumers, such as the web UI)
# ============================================================================

Event = Dict[str, Any]
Subscriber = Callable[[Event], None]

_subscribers: List[Subscriber] = []
_subscribers_lock = threading.Lock()

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _plain(value: Any) -> Any:
    """Strip the colour codes: events travel as plain text."""
    if isinstance(value, str):
        return _ANSI_RE.sub("", value)
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def subscribe(callback: Subscriber) -> Callable[[], None]:
    """
    Register a consumer of console events.

    Returns:
        A function that cancels the subscription.
    """
    with _subscribers_lock:
        _subscribers.append(callback)

    def unsubscribe() -> None:
        with _subscribers_lock:
            if callback in _subscribers:
                _subscribers.remove(callback)

    return unsubscribe


def _emit(kind: str, **payload: Any) -> None:
    """Notify subscribers of an event, never letting a failure break the pipeline."""
    with _subscribers_lock:
        targets = list(_subscribers)
    if not targets:
        return
    event: Event = {"kind": kind, **{k: _plain(v) for k, v in payload.items()}}
    for callback in targets:
        try:
            callback(event)
        except Exception:  # noqa: BLE001 — a broken consumer cannot take the run down
            pass


def _colors_enabled() -> bool:
    if os.getenv("NO_COLOR"):
        return False
    if os.getenv("FORCE_COLOR"):
        return True
    return sys.stdout.isatty()


COLOR = _colors_enabled()


def _c(code: str) -> str:
    """Return the ANSI sequence, or an empty string when colours are off."""
    return code if COLOR else ""


RESET = _c("\033[0m")
BOLD = _c("\033[1m")
DIM = _c("\033[2m")
ITALIC = _c("\033[3m")

RED = _c("\033[31m")
GREEN = _c("\033[32m")
YELLOW = _c("\033[33m")
BLUE = _c("\033[34m")
MAGENTA = _c("\033[35m")
CYAN = _c("\033[36m")
GREY = _c("\033[90m")
BRIGHT_RED = _c("\033[91m")
BRIGHT_GREEN = _c("\033[92m")
BRIGHT_CYAN = _c("\033[96m")
BRIGHT_WHITE = _c("\033[97m")


# ============================================================================
# High-level output
# ============================================================================

def banner(title: str, subtitle: str = "", color: str = BRIGHT_CYAN) -> None:
    """Large header, for the start of the pipeline."""
    line = "═" * _WIDTH
    print(f"\n{color}{BOLD}{line}{RESET}")
    print(f"{color}{BOLD}  {title}{RESET}")
    if subtitle:
        print(f"{GREY}  {subtitle}{RESET}")
    print(f"{color}{BOLD}{line}{RESET}")
    _emit("banner", title=title, subtitle=subtitle)


def step(number: str, title: str, detail: str = "") -> None:
    """Header of a pipeline step (PASO 0..4)."""
    line = "─" * _WIDTH
    print(f"\n{MAGENTA}{line}{RESET}")
    print(f"{MAGENTA}{BOLD} ▶ PASO {number}{RESET}  {BRIGHT_WHITE}{BOLD}{title}{RESET}")
    if detail:
        print(f"{GREY}   {detail}{RESET}")
    print(f"{MAGENTA}{line}{RESET}")
    _emit("step", number=str(number), title=title, detail=detail)


def section(title: str) -> None:
    """Subtitle inside a step."""
    print(f"\n{CYAN}{BOLD}·· {title}{RESET}")
    _emit("section", text=title)


def kv(label: str, value: object, color: str = BRIGHT_WHITE) -> None:
    """Print an aligned label/value pair."""
    print(f"   {GREY}{label:<28}{RESET}{color}{value}{RESET}")
    _emit("kv", label=label, value=str(value))


def info(msg: str) -> None:
    print(f"   {BLUE}ℹ{RESET}  {msg}")
    _emit("info", text=msg)


def ok(msg: str) -> None:
    print(f"   {BRIGHT_GREEN}✔{RESET}  {msg}")
    _emit("ok", text=msg)


def warn(msg: str) -> None:
    print(f"   {YELLOW}⚠{RESET}  {YELLOW}{msg}{RESET}")
    _emit("warn", text=msg)


def error(msg: str) -> None:
    print(f"   {BRIGHT_RED}✖{RESET}  {BRIGHT_RED}{msg}{RESET}")
    _emit("error", text=msg)


def detail(msg: str) -> None:
    """Detail line, dimmed."""
    print(f"      {GREY}{msg}{RESET}")
    _emit("detail", text=msg)


def progress(current: int, total: int, msg: str) -> None:
    """[i/n] counter for long loops (dispositions, rules, pages)."""
    print(f"   {CYAN}[{current}/{total}]{RESET} {msg}")
    _emit("progress", current=current, total=total, text=msg)


def heartbeat(text: str, elapsed: float, limit: Optional[float] = None) -> None:
    """
    Heartbeat of a long wait: says we are still waiting, and for how long.

    Args:
        text: What is being waited on.
        elapsed: Seconds elapsed.
        limit: Per-attempt wait ceiling, if any. Passing it lets us tell
            "still waiting" apart from "the attempt blew past it and retried".
    """
    # Blowing past the ceiling without an exception can only mean one thing: the
    # SDK already dropped that attempt and is retrying on its own. Saying so keeps
    # the wait from looking like a hang. The flag travels in the event so that the
    # web UI and the terminal do not recompute it differently (the event's
    # `elapsed` is rounded).
    retrying = bool(limit and elapsed > limit)
    notice = " · reintentando, el intento anterior se pasó del techo" if retrying else ""
    print(f"      {GREY}⋯ {text} · {_duration(elapsed)}{notice}{RESET}")
    _emit(
        "heartbeat",
        text=text,
        elapsed=round(elapsed, 1),
        limit=limit,
        retrying=retrying,
    )


def _duration(seconds: float) -> str:
    """Human-readable duration: `48s`, `3m 20s`."""
    total = int(seconds)
    return f"{total}s" if total < 60 else f"{total // 60}m {total % 60:02d}s"


@contextmanager
def waiting(text: str, interval: float = 15.0, limit: Optional[float] = None):
    """
    Emit a heartbeat every `interval` seconds for as long as the block runs.

    A model call can take minutes without saying anything, and from the web that
    is indistinguishable from a hung run. The heartbeat runs on its own thread and
    switches itself off when the block exits; short calls emit nothing, because
    the first heartbeat only lands once the first interval elapses.

    Args:
        text: What is being waited on (e.g. "esperando a gpt-4.1").
        interval: Seconds between heartbeats.
        limit: Per-attempt wait ceiling, to warn once it is exceeded.
    """
    started = time.monotonic()
    done = threading.Event()

    def beat() -> None:
        while not done.wait(interval):
            heartbeat(text, time.monotonic() - started, limit=limit)

    thread = threading.Thread(target=beat, name="console-heartbeat", daemon=True)
    thread.start()
    try:
        yield
    finally:
        done.set()


def status_line(status: str, msg: str) -> None:
    """Line coloured according to a rule's compliance status."""
    palette = {
        "ok": (BRIGHT_GREEN, "✔"),
        "missing": (BRIGHT_RED, "✖"),
        "not_applicable": (GREY, "➖"),
        "not_evaluable": (YELLOW, "?"),
        "error": (BRIGHT_RED, "🔥"),
    }
    color, icon = palette.get(status, (GREY, "•"))
    print(f"      {color}{icon} {status:<15}{RESET}{msg}")
    _emit("status", status=status, text=msg)


def summary_table(rows: Sequence[tuple], title: str = "") -> None:
    """Simple two-column table for each step's summary."""
    if title:
        section(title)
    width = max((len(str(r[0])) for r in rows), default=0)
    for label, value in rows:
        print(f"   {GREY}{str(label):<{width + 2}}{RESET}{BRIGHT_WHITE}{value}{RESET}")
    _emit(
        "table",
        title=title,
        rows=[[str(label), str(value)] for label, value in rows],
    )


def path_link(path: Path | str) -> str:
    """Format a path so it stands out from the surrounding text."""
    return f"{ITALIC}{CYAN}{path}{RESET}"


# ============================================================================
# Interactive input
# ============================================================================

def ask_yes_no(question: str, default: bool = True) -> bool:
    """Yes/no question. Enter accepts the default."""
    hint = "S/n" if default else "s/N"
    while True:
        raw = input(f"\n{BOLD}{question}{RESET} {GREY}[{hint}]{RESET} ").strip().lower()
        if not raw:
            return default
        if raw in ("s", "si", "sí", "y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        warn("Respondé 's' o 'n'.")


def ask_choice(question: str, options: Sequence[str], default: int = 0) -> int:
    """Numbered menu. Returns the chosen index."""
    print(f"\n{BOLD}{question}{RESET}")
    for i, opt in enumerate(options, start=1):
        marker = f"{BRIGHT_GREEN}◉{RESET}" if i - 1 == default else f"{GREY}○{RESET}"
        print(f"   {marker} {CYAN}{i}{RESET}) {opt}")
    while True:
        raw = input(f"{GREY}Opción [{default + 1}]:{RESET} ").strip()
        if not raw:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1
        warn(f"Elegí un número entre 1 y {len(options)}.")


def ask_text(question: str, default: str = "") -> str:
    """Ask for free text. Enter accepts the default."""
    suffix = f" {GREY}[{default}]{RESET}" if default else ""
    raw = input(f"\n{BOLD}{question}{RESET}{suffix} ").strip()
    return raw or default


def _clean_path_input(raw: str) -> str:
    """
    Normalise a path pasted into the terminal.

    Dragging a file into the terminal makes zsh/bash escape the spaces, and the
    quotes end up glued to the path; this undoes both.
    """
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        raw = raw[1:-1]
    raw = raw.replace("\\ ", " ").replace("\\~", "~")
    return raw.strip()


def ask_existing_path(
    question: str,
    default: Optional[Path] = None,
    must_be: str = "any",
    suggestions: Optional[Sequence[Path]] = None,
) -> Path:
    """
    Ask for an existing path (you can drag the file into the terminal).

    Args:
        question: Text of the question.
        default: Proposed path; Enter accepts it.
        must_be: "file", "dir" or "any".
        suggestions: Paths to list as numbered shortcuts.

    Returns:
        An existing Path of the requested kind.
    """
    numbered: List[Path] = list(suggestions or [])
    if numbered:
        print(f"\n{BOLD}{question}{RESET}")
        for i, cand in enumerate(numbered, start=1):
            print(f"   {CYAN}{i}{RESET}) {cand}")
        prompt = f"{GREY}Número o ruta"
    else:
        prompt = f"\n{BOLD}{question}{RESET}\n{GREY}Ruta"
    if default is not None:
        prompt += f" [{default}]"
    prompt += f":{RESET} "

    while True:
        raw = _clean_path_input(input(prompt))
        if not raw and default is not None:
            candidate = Path(default)
        elif raw.isdigit() and numbered and 1 <= int(raw) <= len(numbered):
            candidate = numbered[int(raw) - 1]
        elif raw:
            candidate = Path(raw).expanduser()
        else:
            warn("Hace falta una ruta.")
            continue

        if not candidate.exists():
            error(f"No existe: {candidate}")
            continue
        if must_be == "file" and not candidate.is_file():
            error(f"No es un archivo: {candidate}")
            continue
        if must_be == "dir" and not candidate.is_dir():
            error(f"No es un directorio: {candidate}")
            continue
        return candidate.resolve()


# ============================================================================
# Logging
# ============================================================================

class _ColorFormatter(logging.Formatter):
    """Formatter that colours the level and dims the logger name."""

    LEVEL_COLORS = {
        logging.DEBUG: GREY,
        logging.INFO: BLUE,
        logging.WARNING: YELLOW,
        logging.ERROR: BRIGHT_RED,
        logging.CRITICAL: BRIGHT_RED + BOLD,
    }

    def format(self, record: logging.LogRecord) -> str:
        color = self.LEVEL_COLORS.get(record.levelno, "")
        level = f"{color}{record.levelname:<8}{RESET}"
        name = f"{GREY}{record.name}{RESET}"
        msg = record.getMessage()
        if record.exc_info:
            msg = f"{msg}\n{self.formatException(record.exc_info)}"
        return f"{DIM}{self.formatTime(record, '%H:%M:%S')}{RESET} {level} {name}  {msg}"


def setup_logging(log_file: Optional[Path] = None, level: int = logging.INFO) -> None:
    """
    Configure the process's logging: coloured console + optional file.

    Called once, when the pipeline starts. Replaces any previous handler (the
    modules that call logging.basicConfig on import).

    Args:
        log_file: If given, a plain log (no ANSI) is also written there.
        level: Minimum level to emit.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.setLevel(level)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(_ColorFormatter())
    console.setLevel(level)
    root.addHandler(console)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-8s %(name)s  %(message)s")
        )
        file_handler.setLevel(logging.DEBUG)
        root.addHandler(file_handler)

    # OpenAI's HTTP clients are very chatty at INFO.
    for noisy in ("httpx", "httpcore", "openai", "urllib3", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
