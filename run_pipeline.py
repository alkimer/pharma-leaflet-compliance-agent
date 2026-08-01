#!/usr/bin/env python
"""
Entry point of the leaflet analysis and adequation pipeline (ANMAT).

Typical use (interactive, asks for whatever it needs):

    python run_pipeline.py

No questions asked, for automation:

    python run_pipeline.py --prospecto ejemplos/prospectos/IBUPROFENO-DEMO.md --no-interactivo

Resume an existing run from a given step:

    python run_pipeline.py --corrida 20260728-1745 --desde 3

The CLI flags and the console output are in Spanish: the tool is used by Argentine
regulatory-affairs teams.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from core import console  # noqa: E402
from core.run_context import RunContext  # noqa: E402
from pipeline.orchestrator import LAST_STEP, run_pipeline  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analiza un prospecto contra las disposiciones ANMAT y genera su versión adecuada",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Pasos:\n"
            "  0  calcula el <fecha-hora> y prepara las carpetas\n"
            "  1  disposiciones -> reglas JSON (disposiciones-explotadas/<fecha-hora>/reglas-extraidas)\n"
            "  2  prospecto -> texto limpio   (corridas/<fecha-hora>/documento-subido)\n"
            "  3  verificación de cumplimiento (corridas/<fecha-hora>/resultado)\n"
            "  4  adecuación del prospecto     (corridas/<fecha-hora>/documento-adecuado)\n"
            "  5  verificación final con Claude (opcional: --verificar)\n"
        ),
    )
    parser.add_argument(
        "--prospecto", type=Path,
        help="Prospecto a analizar (PDF, MD, TXT o DOCX). Si no se indica, se pregunta.",
    )
    parser.add_argument(
        "--corrida", metavar="FECHA_HORA",
        help="Reutiliza el <fecha-hora> de una corrida existente para retomarla",
    )
    parser.add_argument(
        "--desde", type=int, default=1, choices=range(1, LAST_STEP + 1),
        help="Primer paso a ejecutar (default: 1)",
    )
    parser.add_argument(
        "--hasta", type=int, default=LAST_STEP, choices=range(1, LAST_STEP + 1),
        help=f"Último paso a ejecutar (default: {LAST_STEP})",
    )
    parser.add_argument(
        "--no-interactivo", action="store_true",
        help="No hace preguntas: usa los argumentos y los defaults del .env",
    )
    parser.add_argument(
        "--generar-reglas", action="store_true",
        help="Paso 1: genera las reglas con el LLM en lugar de reutilizar las existentes",
    )
    parser.add_argument(
        "--reusar-reglas", action="store_true",
        help="Paso 1: reutiliza las reglas existentes sin preguntar",
    )
    parser.add_argument(
        "--verificar", action="store_true",
        help="Paso 5: verificación final con Claude (opcional, apagado por defecto)",
    )
    parser.add_argument(
        "--forzar-ocr", action="store_true",
        help="Paso 2: fuerza el OCR local aunque el PDF tenga capa de texto",
    )
    parser.add_argument(
        "--idioma", choices=("es", "en"), default="es",
        help="Idioma del INFORME (default: es). El prospecto adecuado sale siempre en español",
    )
    parser.add_argument(
        "--email", metavar="DIRECCION",
        help="Envía el prospecto adecuado y el informe a esa dirección al terminar",
    )
    parser.add_argument(
        "--listar-corridas", action="store_true",
        help="Lista las corridas existentes y termina",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.listar_corridas:
        existing = RunContext.list_existing()
        if not existing:
            console.warn("Todavía no hay corridas")
            return 0
        console.summary_table([(stamp, "") for stamp in existing], title="Corridas existentes")
        return 0

    if args.desde > args.hasta:
        console.error(f"--desde ({args.desde}) no puede ser mayor que --hasta ({args.hasta})")
        return 2

    if args.generar_reglas and args.reusar_reglas:
        console.error("--generar-reglas y --reusar-reglas son mutuamente excluyentes")
        return 2

    reuse_rules = True if args.reusar_reglas else (False if args.generar_reglas else None)

    # El paso 5 es opcional y caro: si no se pidió por flag, se pregunta (y en
    # modo no interactivo simplemente no corre).
    verify = args.verificar
    if not verify and not args.no_interactivo and args.hasta >= LAST_STEP:
        verify = console.ask_yes_no(
            "¿Querés la verificación final con Claude? (revisa la adecuación y marca "
            "qué necesita criterio humano; es la llamada más cara de la corrida)",
            default=False,
        )

    if args.desde > 1 and not args.corrida:
        console.error("Para empezar desde un paso posterior al 1 hace falta --corrida <fecha-hora>")
        return 2

    # Una dirección mal escrita se descubriría recién al final, después de minutos
    # de corrida y sin haber enviado nada.
    if args.email:
        from core import mailer  # noqa: PLC0415 — sólo si se pidió el envío

        if not mailer.valid_address(args.email):
            console.error(f"La dirección de correo no parece válida: {args.email}")
            return 2
        if not mailer.is_configured():
            console.warn(
                "No hay SMTP configurado (SMTP_HOST / SMTP_FROM en el .env): "
                "la corrida va a terminar sin poder enviar el correo"
            )

    try:
        run_pipeline(
            prospect_path=args.prospecto,
            stamp=args.corrida,
            interactive=not args.no_interactivo,
            reuse_rules=reuse_rules,
            force_ocr=args.forzar_ocr,
            from_step=args.desde,
            to_step=args.hasta,
            verify=verify,
            lang=args.idioma,
            email=args.email,
        )
    except KeyboardInterrupt:
        console.warn("Interrumpido por el usuario")
        return 130
    except Exception as e:
        console.error(str(e))
        import logging

        logging.getLogger("pipeline").exception("El pipeline falló")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
