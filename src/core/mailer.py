"""
Delivery of a run's results by email.

A full run takes minutes: whoever launched it should not have to sit in front of
the page waiting. If an address was given in the web's first screen, the run ends
by mailing the two artifacts that matter — the adequated leaflet (DOCX) and the
compliance report (PDF).

The SMTP credentials come from the `.env` (see `SMTP_*` in `.env.example`). With
no credentials configured, sending is skipped with a warning: an unsendable email
must never fail a run whose real work — the analysis — is already done and saved
to disk.
"""
from __future__ import annotations

import logging
import mimetypes
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from core.config import SMTPConfig, settings

logger = logging.getLogger(__name__)

# Nothing gets read into memory beyond this per attachment. The DOCX and the PDF
# of a leaflet weigh a few hundred KB; anything near this ceiling means something
# went wrong upstream, and most inboxes reject over ~25 MB anyway.
MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024


@dataclass(frozen=True)
class MailResult:
    """Outcome of the attempt, so the caller can report it without re-deriving it."""

    sent: bool
    detail: str
    attachments: Sequence[str] = ()


# The body is short on purpose: the reader wants the two files, not a letter.
BODIES: Dict[str, Dict[str, str]] = {
    "es": {
        "subject": "Prospecto adecuado y análisis de cumplimiento · {stamp}",
        "greeting": "Tu análisis terminó.",
        "body": (
            "Adjuntamos el prospecto adecuado según las disposiciones ANMAT "
            "(Argentina) y el informe de cumplimiento con el detalle regla por "
            "regla y la evidencia de cada veredicto. Si pediste la verificación "
            "final, va también su revisión."
        ),
        "files": "Archivos adjuntos:",
        "footer": (
            "Las partes agregadas o modificadas están marcadas dentro del "
            "documento. Los tramos con [COMPLETAR ACÁ!] necesitan datos que el "
            "prospecto original no traía: requieren criterio humano."
        ),
    },
    "en": {
        "subject": "Adequated leaflet and compliance analysis · {stamp}",
        "greeting": "Your analysis is ready.",
        "body": (
            "Attached are the leaflet adequated to the ANMAT (Argentina) "
            "dispositions and the compliance report, rule by rule, with the "
            "evidence behind every verdict. If you asked for the final "
            "verification, its review is attached too."
        ),
        "files": "Attached files:",
        "footer": (
            "Added or modified passages are marked inside the document. Passages "
            "reading [COMPLETAR ACÁ!] need data the original leaflet did not "
            "carry: they require human judgement. The leaflet itself stays in "
            "Spanish — the dispositions demand specific Spanish wording."
        ),
    },
}


def is_configured(config: Optional[SMTPConfig] = None) -> bool:
    """True when there is enough configuration to even try to send."""
    config = config or settings.smtp
    return bool(config.host and config.sender)


def valid_address(address: str) -> bool:
    """
    Minimal sanity check: `parseaddr` gives us something with a name and a domain.

    Deliberately not a full RFC 5322 validation — that rejects valid addresses far
    more often than it catches typos. Whether the mailbox exists is something only
    the delivery attempt can tell.
    """
    _, email = parseaddr(address or "")
    if "@" not in email:
        return False
    local, _, domain = email.rpartition("@")
    return bool(local) and "." in domain and " " not in email


def send_run_results(
    to: str,
    attachments: Sequence[Path],
    stamp: str,
    language: str = "es",
    config: Optional[SMTPConfig] = None,
) -> MailResult:
    """
    Send the run's results to `to`.

    Args:
        to: Recipient address.
        attachments: Files to attach; the missing ones are skipped.
        stamp: The run's `<timestamp>`, used in the subject.
        language: "es" or "en" — which body to use.
        config: SMTP configuration; defaults to the one in `settings`.

    Returns:
        A `MailResult`. It never raises: a failed send is reported, not thrown,
        because the run's real output is already on disk.
    """
    config = config or settings.smtp

    if not valid_address(to):
        return MailResult(False, f"dirección de correo inválida: {to!r}")
    if not is_configured(config):
        return MailResult(
            False,
            "no hay SMTP configurado (SMTP_HOST / SMTP_FROM en el .env): "
            "no se envió el correo",
        )

    usable, skipped = _usable_attachments(attachments)
    if not usable:
        return MailResult(False, "no hay artefactos para adjuntar")

    strings = BODIES.get(language, BODIES["es"])
    message = _build_message(to, usable, stamp, strings, config)

    try:
        _deliver(message, config)
    except Exception as e:  # noqa: BLE001 — reported, never fatal for the run
        logger.exception("No se pudo enviar el correo con los resultados")
        return MailResult(False, f"{type(e).__name__}: {e}")

    detail = f"enviado a {to}"
    if skipped:
        detail += f" (sin adjuntar: {', '.join(skipped)})"
    return MailResult(True, detail, [p.name for p in usable])


def _usable_attachments(paths: Sequence[Path]) -> tuple[List[Path], List[str]]:
    """Split the requested attachments into the ones we can send and the rest."""
    usable: List[Path] = []
    skipped: List[str] = []
    for path in paths:
        if path is None:
            continue
        path = Path(path)
        if not path.is_file():
            skipped.append(f"{path.name} (no existe)")
        elif path.stat().st_size > MAX_ATTACHMENT_BYTES:
            skipped.append(f"{path.name} (demasiado grande)")
        else:
            usable.append(path)
    return usable, skipped


def _build_message(
    to: str,
    attachments: Sequence[Path],
    stamp: str,
    strings: Dict[str, str],
    config: SMTPConfig,
) -> EmailMessage:
    """Assemble the message with its plain-text body and the attachments."""
    message = EmailMessage()
    message["Subject"] = strings["subject"].format(stamp=stamp)
    message["From"] = _format_sender(config)
    message["To"] = to

    listed = "\n".join(f"  · {path.name}" for path in attachments)
    message.set_content(
        f"{strings['greeting']}\n\n"
        f"{strings['body']}\n\n"
        f"{strings['files']}\n{listed}\n\n"
        f"{strings['footer']}\n"
    )

    for path in attachments:
        guessed, _ = mimetypes.guess_type(path.name)
        maintype, _, subtype = (guessed or "application/octet-stream").partition("/")
        message.add_attachment(
            path.read_bytes(),
            maintype=maintype,
            subtype=subtype or "octet-stream",
            filename=path.name,
        )
    return message


def _format_sender(config: SMTPConfig) -> str:
    """`SMTP_FROM` may be a bare address or a "Name <address>" pair; both work."""
    name, email = parseaddr(config.sender)
    return formataddr((name, email)) if name else email


def _deliver(message: EmailMessage, config: SMTPConfig) -> None:
    """
    Open the connection, authenticate if there are credentials, and send.

    Port 465 is implicit TLS (SMTP_SSL); 587 and the rest negotiate STARTTLS,
    which is what Gmail expects.
    """
    if config.use_ssl:
        with smtplib.SMTP_SSL(config.host, config.port, timeout=config.timeout) as smtp:
            _authenticate(smtp, config)
            smtp.send_message(message)
        return

    with smtplib.SMTP(config.host, config.port, timeout=config.timeout) as smtp:
        smtp.ehlo()
        if config.use_starttls:
            smtp.starttls()
            smtp.ehlo()
        _authenticate(smtp, config)
        smtp.send_message(message)


def _authenticate(smtp: smtplib.SMTP, config: SMTPConfig) -> None:
    """Log in only when there are credentials: local relays often need none."""
    if config.user and config.password:
        smtp.login(config.user, config.password)
