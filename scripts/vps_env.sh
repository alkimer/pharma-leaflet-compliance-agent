#!/usr/bin/env bash
#
# Configuración del VPS, compartida por los scripts de scripts/.
# No se ejecuta: se hace `source` desde deploy_vps.sh y ariel_rtx5070_connect.sh.
#
# Este repositorio es público, así que acá NO hay ni un dato del servidor: todo
# sale del entorno o de `scripts/.env`, que está en .gitignore. La plantilla con
# los nombres de las variables es `scripts/.env.example`.

_vps_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_vps_project_root="$(cd "$_vps_script_dir/.." && pwd)"

# Lee las VPS_* de un archivo .env sin ejecutarlo.
#
# `source` sobre el .env del proyecto era una bomba de tiempo: ese archivo lo
# escribe la app, no el shell, y basta un valor sin comillas —un SMTP_FROM con
# «Nombre <mail@dominio>», por ejemplo— para que bash lo lea como una
# redirección y el script muera con «syntax error near unexpected token». Acá se
# parsean SÓLO las claves VPS_*, como texto, sin evaluar nada.
#
# Una variable que ya venga del entorno gana: `VPS_HOST=otro scripts/deploy_vps.sh`
# hace lo que uno espera.
_vps_leer() {
  local archivo="$1" linea clave valor
  [[ -f "$archivo" ]] || return 0
  while IFS= read -r linea || [[ -n "$linea" ]]; do
    [[ "$linea" =~ ^[[:space:]]*(VPS_[A-Za-z0-9_]+)[[:space:]]*=(.*)$ ]] || continue
    clave="${BASH_REMATCH[1]}"
    valor="${BASH_REMATCH[2]}"
    # Espacios de los bordes y comillas envolventes, si las hay.
    valor="${valor#"${valor%%[![:space:]]*}"}"
    valor="${valor%"${valor##*[![:space:]]}"}"
    if [[ ${#valor} -ge 2 && ( ( "$valor" == \"*\" ) || ( "$valor" == \'*\' ) ) ]]; then
      valor="${valor:1:${#valor}-2}"
    fi
    # Lo que ya está definido no se pisa.
    [[ -n "${!clave:-}" ]] || printf -v "$clave" '%s' "$valor"
    export "${clave?}"
  done < "$archivo"
}

# scripts/.env primero: es el que tiene los datos del servidor y por eso manda.
_vps_leer "$_vps_script_dir/.env"
_vps_leer "$_vps_project_root/.env"

# Sin valor por defecto lo que identifica al servidor: si falta, el script avisa
# en vez de intentar conectarse a cualquier lado.
VPS_HOST="${VPS_HOST:-}"
VPS_USER="${VPS_USER:-}"
VPS_PASSWORD="${VPS_PASSWORD:-}"
# Con valor por defecto lo que no es un dato del servidor sino una convención.
VPS_PORT="${VPS_PORT:-22}"
VPS_DIR="${VPS_DIR:-pharma-leaflet-compliance-agent}"
VPS_WEB_PORT="${VPS_WEB_PORT:-8000}"

# ---------------------------------------------------------------- autenticación
# Deja listo el comando ssh en `VPS_SSH` (array) y en `VPS_RSH` (string, para el
# -e de rsync), resolviendo cómo se entrega la contraseña:
#
#   sshpass   si está instalado (la contraseña viaja por el entorno, no por argv)
#   askpass   si no: OpenSSH >= 8.4 acepta SSH_ASKPASS_REQUIRE=force, que hace
#             que ssh pida la contraseña a un programa auxiliar en vez de a la
#             terminal. El auxiliar NO la guarda en disco: la lee del entorno que
#             hereda, así que el archivo temporal es un script de dos líneas sin
#             ningún dato adentro.
#   plain     sin contraseña configurada: clave pública, o ssh la pide como
#             siempre. Con "control" se multiplexa la conexión para que la pida
#             UNA sola vez y rsync y los comandos siguientes la reusen.
#
#   vps_auth_preparar [control]
VPS_SSH=()
VPS_RSH=""
_vps_askpass_dir=""
_vps_control_path=""

vps_auth_preparar() {
  local multiplexar="${1:-}"
  local opciones=(-p "$VPS_PORT" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15)

  if [[ -n "$VPS_PASSWORD" ]] && command -v sshpass >/dev/null 2>&1; then
    export SSHPASS="$VPS_PASSWORD"
    VPS_SSH=(sshpass -e ssh "${opciones[@]}")
    VPS_RSH="sshpass -e ssh ${opciones[*]}"
    return 0
  fi

  if [[ -n "$VPS_PASSWORD" ]]; then
    _vps_askpass_dir="$(mktemp -d)"
    local auxiliar="$_vps_askpass_dir/askpass"
    printf '#!/bin/sh\nprintf "%%s\\n" "$VPS_PASSWORD"\n' > "$auxiliar"
    chmod 700 "$auxiliar"
    export VPS_PASSWORD
    export SSH_ASKPASS="$auxiliar"
    export SSH_ASKPASS_REQUIRE=force
    VPS_SSH=(ssh "${opciones[@]}")
    VPS_RSH="ssh ${opciones[*]}"
    return 0
  fi

  if [[ "$multiplexar" == "control" ]]; then
    _vps_control_path="${TMPDIR:-/tmp}/vps-$$.sock"
    opciones+=(-o ControlMaster=auto -o "ControlPath=$_vps_control_path" -o ControlPersist=300)
  fi
  VPS_SSH=(ssh "${opciones[@]}")
  VPS_RSH="ssh ${opciones[*]}"
}

# Borra lo temporal. Cada script la engancha con `trap vps_limpiar EXIT`.
vps_limpiar() {
  [[ -n "$_vps_askpass_dir" && -d "$_vps_askpass_dir" ]] && rm -rf "$_vps_askpass_dir"
  if [[ -n "$_vps_control_path" && -S "$_vps_control_path" ]]; then
    ssh -O exit -o "ControlPath=$_vps_control_path" "$VPS_USER@$VPS_HOST" >/dev/null 2>&1 || true
  fi
  return 0
}

# Corta con un mensaje accionable si falta alguna de las variables pedidas.
#   vps_requerir VPS_HOST VPS_USER
vps_requerir() {
  local faltan=() var
  for var in "$@"; do
    [[ -n "${!var:-}" ]] || faltan+=("$var")
  done
  [[ ${#faltan[@]} -gt 0 ]] || return 0

  echo "❌ Falta configurar: ${faltan[*]}" >&2
  echo "   Estos scripts no traen los datos del servidor adentro (el repo es público)." >&2
  echo "   Completalos en $_vps_script_dir/.env, que está en .gitignore:" >&2
  echo "     cp $_vps_script_dir/.env.example $_vps_script_dir/.env" >&2
  echo "     \$EDITOR $_vps_script_dir/.env" >&2
  exit 1
}
