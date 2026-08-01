#!/usr/bin/env zsh

# Deploy the project to your own server over rsync + ssh.
# - Copies changed files only
# - Excludes: .venv, documentacion, etl (local OCR) and each run's outputs
#   (corridas/, documentos/)
# - Requires: rsync, ssh and SSH access to the configured host
#
# The target is NOT hardcoded: configure it through the environment or through the
# project root's `.env` (which is in .gitignore).
#   DEPLOY_HOST        (required)  - Server host or IP
#   DEPLOY_USER        (required)  - SSH user
#   DEPLOY_PORT        (optional)  - SSH port (default: 22)
#   DEPLOY_REMOTE_DIR  (optional)  - Remote directory (default: ~/pharma-leaflet-compliance-agent)
#   DEPLOY_DRY_RUN     (optional)  - 1 = simulate (default), 0 = real deploy

set -euo pipefail

# Work out the script's path and the project root (the script lives in scripts/)
script_dir="$(cd "$(dirname "$0")" && pwd)"
project_root="$(cd "$script_dir/.." && pwd)"

# Load .env if it exists at the project root
if [[ -f "$project_root/.env" ]]; then
  echo "📦 Cargando configuración desde $project_root/.env"
  set -a
  # shellcheck disable=SC1090
  source "$project_root/.env"
  set +a
fi

# Defaults and required variables.
# No host/user defaults: everyone points at their own server.
DEPLOY_HOST=${DEPLOY_HOST:-""}
DEPLOY_USER=${DEPLOY_USER:-""}
DEPLOY_PORT=${DEPLOY_PORT:-"22"}
DEPLOY_REMOTE_DIR=${DEPLOY_REMOTE_DIR:-"~/pharma-leaflet-compliance-agent"}
# Simulates by default: a real deploy has to be asked for explicitly.
DEPLOY_DRY_RUN=${DEPLOY_DRY_RUN:-"1"}

# Validate dependencies
if ! command -v rsync >/dev/null 2>&1; then
  echo "❌ rsync no está instalado. En macOS puedes instalarlo con: brew install rsync" >&2
  exit 1
fi

if ! command -v ssh >/dev/null 2>&1; then
  echo "❌ ssh no está disponible en este sistema." >&2
  exit 1
fi

# Validate the required configuration
if [[ -z "$DEPLOY_HOST" || -z "$DEPLOY_USER" ]]; then
  echo "❌ DEPLOY_HOST y DEPLOY_USER deben estar configurados (en el entorno o en .env)." >&2
  exit 1
fi

# Directories to exclude
EXCLUDES=(
  ".git/"
  ".github/"
  ".venv/"
  ".idea/"
  "__pycache__/"
  "documentacion/"
  "etl/"
  "corridas/"
  "documentos/"
)

echo "🚀 Preparando despliegue"
echo "- Host remoto:      $DEPLOY_USER@$DEPLOY_HOST"
echo "- Puerto SSH:       $DEPLOY_PORT"
echo "- Directorio remoto: $DEPLOY_REMOTE_DIR"
echo "- Directorio local:  $project_root"

# Show the excluded and included folders
echo "- Directorios EXCLUIDOS:"
for d in "${EXCLUDES[@]}"; do
  echo "   • $d"
done

echo "- Directorios INCLUIDOS (resto del contenido de la raíz del proyecto)"

# Build rsync's exclusion parameters
RSYNC_EXCLUDES=()
for d in "${EXCLUDES[@]}"; do
  RSYNC_EXCLUDES+=("--exclude=$d")
done

# Set up dry-run mode
RSYNC_DRY_RUN=()
if [[ "$DEPLOY_DRY_RUN" == "1" ]]; then
  echo "⚗️  MODO DRY-RUN ACTIVADO: no se modificarán archivos remotos."
  RSYNC_DRY_RUN+=("--dry-run")
fi

# Check / create the remote directory
echo "🔎 Verificando directorio remoto..."
if ! ssh -p "$DEPLOY_PORT" "$DEPLOY_USER@$DEPLOY_HOST" "mkdir -p $DEPLOY_REMOTE_DIR"; then
  echo "❌ No se pudo verificar o crear el directorio remoto: $DEPLOY_REMOTE_DIR" >&2
  exit 3
fi

# Build the rsync command
RSYNC_CMD=(
  rsync -avz --progress
  "-e" "ssh -p $DEPLOY_PORT"
  "${RSYNC_EXCLUDES[@]}"
  "${RSYNC_DRY_RUN[@]}"
  "$project_root/"
  "$DEPLOY_USER@$DEPLOY_HOST:$DEPLOY_REMOTE_DIR/"
)

# Show the effective command (with no extra sensitive data)
echo "🔧 Ejecutando rsync con el siguiente comando:"
printf '  %q' "${RSYNC_CMD[@]}"; echo

# When it is not a dry-run, ask for interactive confirmation
if [[ "$DEPLOY_DRY_RUN" != "1" ]]; then
  echo "⚠️  Estás a punto de ejecutar un despliegue REAL (sin --dry-run)."
  read -r "?¿Confirmas que deseas continuar? Escribe 'yes' para seguir: " respuesta
  if [[ "$respuesta" != "yes" ]]; then
    echo "⏹  Despliegue cancelado por el usuario."
    exit 0
  fi
fi

# Run rsync
set +e
"${RSYNC_CMD[@]}"
rsync_exit_code=$?
set -e

if [[ $rsync_exit_code -ne 0 ]]; then
  echo "❌ Error durante rsync. Código de salida: $rsync_exit_code" >&2
  case $rsync_exit_code in
    255)
      echo "   Posible error de conexión SSH o autenticación. Verifica host, puerto, usuario y claves." >&2
      ;;
    *)
      echo "   Revisa la salida anterior para más detalles." >&2
      ;;
  esac
  exit 2
fi

if [[ "$DEPLOY_DRY_RUN" == "1" ]]; then
  echo "✅ Dry-run completado. Si todo se ve bien, exporta DEPLOY_DRY_RUN=0 para ejecutar el despliegue real."
else
  echo "✅ Despliegue completado correctamente."
fi
