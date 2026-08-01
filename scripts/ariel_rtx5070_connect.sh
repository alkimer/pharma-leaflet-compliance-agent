#!/usr/bin/env bash
#
# Abre una sesión SSH en el VPS.
#
#   scripts/ariel_rtx5070_connect.sh              # sesión interactiva
#   scripts/ariel_rtx5070_connect.sh 'uptime'     # corre un comando y sale
#
# Host, usuario y puerto NO están en este archivo: salen de `scripts/.env`
# (está en .gitignore). Plantilla: `scripts/.env.example`.

set -euo pipefail

# shellcheck source=scripts/vps_env.sh
source "$(cd "$(dirname "$0")" && pwd)/vps_env.sh"

vps_requerir VPS_HOST VPS_USER

trap vps_limpiar EXIT
vps_auth_preparar

# Sin `exec` a propósito: el trap tiene que correr al terminar la sesión para
# borrar el auxiliar temporal de la contraseña.
"${VPS_SSH[@]}" "$VPS_USER@$VPS_HOST" "$@"
