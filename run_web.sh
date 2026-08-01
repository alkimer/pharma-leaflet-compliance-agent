#!/usr/bin/env bash
#
# Levanta la interfaz web. Pensado para el servidor: escucha en todas las
# interfaces y no intenta abrir ningún navegador.
#
#   ./run_web.sh              # 0.0.0.0:8000
#   ./run_web.sh 9000         # otro puerto
#   ./run_web.sh 8000 127.0.0.1
#
# Usa el intérprete del entorno virtual del proyecto si existe (.venv/bin/python)
# y, si no, el python3 del sistema.

set -euo pipefail

cd "$(dirname "$0")"

PUERTO="${1:-${WEB_PORT:-8000}}"
HOST="${2:-${WEB_HOST:-0.0.0.0}}"

PYTHON=".venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3 || true)"
fi
if [[ -z "$PYTHON" ]]; then
  echo "No se encontró python3 ni .venv/bin/python" >&2
  exit 1
fi

# exec: el proceso conserva el PID de este script, así quien lo lanzó (el deploy,
# systemd, lo que sea) puede detenerlo con ese mismo PID.
exec "$PYTHON" run_web.py --host "$HOST" --puerto "$PUERTO" --no-abrir
