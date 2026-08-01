#!/usr/bin/env python
"""
Web interface for the pipeline: launches runs and streams them live.

Usage:

    python run_web.py                 # http://127.0.0.1:8000
    python run_web.py --puerto 9000
    python run_web.py --host 0.0.0.0  # reachable from the local network

It is the same pipeline as `run_pipeline.py`, without the interactive questions:
whatever the terminal asks for is picked in the form instead.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from core import console  # noqa: E402
from core.console import setup_logging  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Interfaz web del analizador de prospectos")
    parser.add_argument("--host", default="127.0.0.1", help="Interfaz donde escuchar")
    parser.add_argument("--puerto", type=int, default=8000, help="Puerto (default: 8000)")
    parser.add_argument("--no-abrir", action="store_true", help="No abrir el navegador")
    args = parser.parse_args()

    import uvicorn

    from web.app import ABRIR_NAVEGADOR_ENV

    setup_logging()
    url = f"http://{'127.0.0.1' if args.host == '0.0.0.0' else args.host}:{args.puerto}"
    console.banner("ANALIZADOR DE PROSPECTOS · WEB", url)

    _avisar_si_el_puerto_esta_ocupado(url, args.puerto)
    console.info("Ctrl+C para detener el servidor")

    # El navegador lo abre la app cuando ya está escuchando (ver `web.app`):
    # abrirlo antes puede mandar el pedido a otro server que tenga el puerto.
    if not args.no_abrir:
        os.environ[ABRIR_NAVEGADOR_ENV] = url

    uvicorn.run("web.app:app", host=args.host, port=args.puerto, log_level="warning")
    return 0


def _avisar_si_el_puerto_esta_ocupado(url: str, puerto: int) -> None:
    """
    Avisa si ya hay algo respondiendo en el puerto.

    En macOS un server atado a 0.0.0.0 no impide que nosotros tomemos
    127.0.0.1: el nuestro gana los pedidos a 127.0.0.1 mientras esté vivo, pero
    apenas se cae, los pedidos vuelven a caer en el otro y su 404 se confunde
    con un problema nuestro. Vale la pena saberlo de entrada.
    """
    import urllib.error
    import urllib.request

    try:
        urllib.request.urlopen(url, timeout=0.7)
    except urllib.error.HTTPError:
        pass  # respondió con un error HTTP: hay alguien escuchando igual
    except OSError:
        return  # nadie escuchando, que es lo esperado

    console.warn(f"Ya hay otro proceso respondiendo en el puerto {puerto}")
    console.detail(
        'si en algún momento ves {"detail":"Not Found"}, es ese otro server; '
        f"probá con --puerto {puerto + 1}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
