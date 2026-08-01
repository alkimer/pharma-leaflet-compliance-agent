#!/usr/bin/env bash
#
# deploy_vps.sh — copia el proyecto al VPS y levanta la web.
#
# Qué hace, en orden:
#   1. rsync incremental del proyecto al VPS (sólo lo que cambió)
#   2. crea/actualiza el entorno virtual remoto e instala las dependencias
#   3. detiene la instancia anterior y levanta `run_web.sh` en el puerto 8000
#   4. verifica que la web responda y, si no, muestra el final de web.log
#
# Uso:
#   scripts/deploy_vps.sh                 # deploy completo
#   scripts/deploy_vps.sh --dry-run       # muestra qué copiaría, sin tocar nada
#   scripts/deploy_vps.sh --solo-copiar   # copia y no reinicia la web
#   scripts/deploy_vps.sh --solo-levantar # no copia; sólo reinicia la web
#   scripts/deploy_vps.sh --sin-deps      # no corre pip (más rápido)
#   scripts/deploy_vps.sh --con-corridas  # también copia corridas/ (pesado)
#
# Este repositorio es público: acá NO hay ningún dato del servidor. Todo sale del
# entorno o de `scripts/.env` (que está en .gitignore y que el deploy tampoco
# sube al servidor). Plantilla: `scripts/.env.example`.
#   VPS_HOST      (obligatorio)  host o IP del servidor
#   VPS_USER      (obligatorio)  usuario SSH
#   VPS_PORT      (default 22)
#   VPS_DIR       (default pharma-leaflet-compliance-agent, relativo al home remoto)
#   VPS_WEB_PORT  (default 8000)
#   VPS_PASSWORD  (opcional; con clave pública no hace falta. Si está, el deploy
#                  es desatendido: usa sshpass si existe y, si no, el mecanismo
#                  askpass de OpenSSH)

set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
project_root="$(cd "$script_dir/.." && pwd)"

# ---------------------------------------------------------------- configuración
# shellcheck source=scripts/vps_env.sh
source "$script_dir/vps_env.sh"

DRY_RUN=0
SKIP_DEPS=0
CON_CORRIDAS=0
COPIAR=1
LEVANTAR=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)        DRY_RUN=1 ;;
    --sin-deps)       SKIP_DEPS=1 ;;
    --con-corridas)   CON_CORRIDAS=1 ;;
    --solo-copiar)    LEVANTAR=0 ;;
    --solo-levantar)  COPIAR=0 ;;
    --puerto)         VPS_WEB_PORT="${2:?falta el número de puerto}"; shift ;;
    # La ayuda es el encabezado del propio script, sin las almohadillas.
    -h|--help)        awk 'NR>1 && /^#/ { sub(/^# ?/, ""); print; next } NR>1 { exit }' "$0"; exit 0 ;;
    *) echo "Opción desconocida: $1 (probá --help)" >&2; exit 2 ;;
  esac
  shift
done

for binario in rsync ssh; do
  command -v "$binario" >/dev/null 2>&1 || { echo "❌ Falta $binario" >&2; exit 1; }
done

# Sin servidor no hay deploy: se avisa acá y no a mitad de camino.
vps_requerir VPS_HOST VPS_USER

# ------------------------------------------------------------------ autenticación
# Cómo se entrega la contraseña lo resuelve vps_env.sh: sshpass si está, y si no
# el mecanismo askpass de OpenSSH, que también hace el deploy desatendido. Sin
# contraseña configurada se multiplexa la conexión para que, si ssh la pide, la
# pida una sola vez y rsync la reuse.
trap vps_limpiar EXIT
vps_auth_preparar control

remoto() { "${VPS_SSH[@]}" "$VPS_USER@$VPS_HOST" "$@"; }

# ------------------------------------------------------------------------ exclusiones
# Las tres primeras son las pedidas explícitamente; el resto es ruido local que no
# tiene sentido en el servidor (control de versiones, cachés, entornos, salidas).
EXCLUDES=(
  ".claude/"
  ".github/"
  ".venv/"
  ".git/"
  ".gitignore"
  ".idea/"
  "__pycache__/"
  "*.pyc"
  ".DS_Store"
  ".pytest_cache/"
  ".mypy_cache/"
  "web.log"
  "web.pid"
  # Las credenciales del propio VPS no tienen por qué viajar al VPS.
  "scripts/.env"
)
# corridas/ son las salidas de cada análisis: pesan mucho y el servidor genera
# las suyas. Se copian sólo si se pide.
[[ "$CON_CORRIDAS" == "1" ]] || EXCLUDES+=("corridas/")

echo "🚀 Deploy a $VPS_USER@$VPS_HOST:$VPS_PORT"
echo "   destino:  ~/$VPS_DIR"
echo "   web:      http://$VPS_HOST:$VPS_WEB_PORT"
[[ "$DRY_RUN" == "1" ]] && echo "   ⚗️  DRY-RUN: no se modifica nada en el servidor"

# ----------------------------------------------------------------------- copia
if [[ "$COPIAR" == "1" ]]; then
  echo "📦 Copiando archivos (sólo los que cambiaron)…"

  # El rsync que trae macOS es el 2.6.9 de Apple y no conoce --info ni -h; el 3.x
  # (Linux, o `brew install rsync`) da un resumen mucho mejor. Se usa lo que haya.
  RSYNC_ARGS=(-az)
  if rsync --version 2>/dev/null | head -1 | grep -q "version 3"; then
    RSYNC_ARGS+=(--human-readable --info=stats1,progress2)
  else
    RSYNC_ARGS+=(--stats)
  fi

  for patron in "${EXCLUDES[@]}"; do
    RSYNC_ARGS+=("--exclude=$patron")
  done
  [[ "$DRY_RUN" == "1" ]] && RSYNC_ARGS+=(--dry-run --itemize-changes)

  if [[ "$DRY_RUN" != "1" ]]; then
    remoto "mkdir -p '$VPS_DIR'"
  fi

  rsync "${RSYNC_ARGS[@]}" -e "$VPS_RSH" \
    "$project_root/" "$VPS_USER@$VPS_HOST:$VPS_DIR/"

  # El .env viaja con el resto: sin él, el servidor no tiene las credenciales de
  # OpenAI ni de Anthropic y la web arranca pero ninguna corrida funciona.
  echo "   (incluye el .env con las credenciales)"
fi

if [[ "$DRY_RUN" == "1" ]]; then
  echo "✅ Dry-run terminado. Sin --dry-run se copia de verdad y se levanta la web."
  exit 0
fi

# -------------------------------------------------------------------- levantar
if [[ "$LEVANTAR" != "1" ]]; then
  echo "✅ Copia terminada (no se tocó la web: --solo-copiar)."
  exit 0
fi

# Llaves obligatorias: sin ellas, el «…» pegado al nombre se come la variable
# (bash lo toma como parte del identificador y con `set -u` aborta).
echo "🌐 Levantando la web en el puerto ${VPS_WEB_PORT}…"

# El script remoto se manda por stdin y recibe sus datos como argumentos: así no
# hay que escapar nada acá. Comillas simples = no se expande nada localmente.
REMOTE_SCRIPT='
set -eu
DIR="$1"; PUERTO="$2"; SIN_DEPS="$3"

cd "$DIR"
chmod +x run_web.sh 2>/dev/null || true

if [ ! -x .venv/bin/python ]; then
  echo "· creando el entorno virtual"
  python3 -m venv .venv || {
    echo "no se pudo crear .venv — probá: apt-get install -y python3-venv" >&2
    exit 1
  }
fi

if [ "$SIN_DEPS" != "1" ]; then
  echo "· instalando dependencias (pipeline + web)"
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt -r requirements-web.txt
fi

if [ -f web.pid ] && kill -0 "$(cat web.pid)" 2>/dev/null; then
  echo "· deteniendo la instancia anterior (pid $(cat web.pid))"
  kill "$(cat web.pid)" 2>/dev/null || true
  sleep 2
  kill -9 "$(cat web.pid)" 2>/dev/null || true
fi
pkill -f "run_web.py --host" 2>/dev/null || true
sleep 1

# Ya paramos lo nuestro: si algo sigue escuchando ahí, es de otro. Sin este
# chequeo, uvicorn falla al bindear y el error queda enterrado en web.log, o peor,
# el que contesta es el otro servidor y su 404 parece un problema nuestro.
if command -v ss >/dev/null 2>&1 && [ -n "$(ss -lntH "sport = :$PUERTO" 2>/dev/null)" ]; then
  echo "✖ el puerto $PUERTO ya está ocupado en el servidor por otro proceso:" >&2
  ss -lptnH "sport = :$PUERTO" 2>/dev/null >&2
  echo "  usá otro puerto: scripts/deploy_vps.sh --puerto 8090" >&2
  exit 1
fi

echo "· arrancando run_web.sh en el puerto $PUERTO"
# nohup + stdin cerrado: el proceso sobrevive al cierre de la sesión SSH. Sin
# setsid a propósito, porque setsid puede forkear y entonces el PID que
# guardaríamos no sería el del servidor. run_web.sh hace exec, así que este PID
# es el del uvicorn real y sirve para pararlo.
nohup ./run_web.sh "$PUERTO" 0.0.0.0 > web.log 2>&1 < /dev/null &
echo $! > web.pid
sleep 4

if ! kill -0 "$(cat web.pid)" 2>/dev/null; then
  echo "✖ la web se cayó al arrancar. Últimas líneas de web.log:" >&2
  tail -n 40 web.log >&2
  exit 1
fi

vivo=0
i=1
while [ "$i" -le 6 ]; do
  if command -v curl >/dev/null 2>&1; then
    curl -fsS -m 5 "http://127.0.0.1:$PUERTO/api/contexto" >/dev/null 2>&1 && { vivo=1; break; }
  else
    python3 -c "import sys,urllib.request; urllib.request.urlopen(\"http://127.0.0.1:$PUERTO/api/contexto\", timeout=5)" >/dev/null 2>&1 && { vivo=1; break; }
  fi
  sleep 3
  i=$((i + 1))
done

if [ "$vivo" != "1" ]; then
  echo "✖ el proceso vive (pid $(cat web.pid)) pero la web no responde. web.log:" >&2
  tail -n 40 web.log >&2
  exit 1
fi

echo "· la web responde en el puerto $PUERTO (pid $(cat web.pid), log en $DIR/web.log)"
'

remoto "bash -s -- '$VPS_DIR' '$VPS_WEB_PORT' '$SKIP_DEPS'" <<< "$REMOTE_SCRIPT"

echo "✅ Deploy completo: http://$VPS_HOST:$VPS_WEB_PORT"
echo "   log remoto:  ssh -p $VPS_PORT $VPS_USER@$VPS_HOST 'tail -f $VPS_DIR/web.log'"
echo "   detenerla:   ssh -p $VPS_PORT $VPS_USER@$VPS_HOST 'kill \$(cat $VPS_DIR/web.pid)'"
echo "   ⚠️  si no abre desde afuera, hay que habilitar el puerto $VPS_WEB_PORT en el firewall del VPS"
