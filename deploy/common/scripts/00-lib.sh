cat > deploy/common/scripts/00-lib.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

log()  { printf "[%s] %s\n" "$(date +'%F %T')" "$*"; }
die()  { printf "[%s] ERROR: %s\n" "$(date +'%F %T')" "$*" >&2; exit 1; }

require_root() { [[ "$(id -u)" -eq 0 ]] || die "Must run as root."; }
require_user() { [[ "$(id -un)" == "$1" ]] || die "Must run as user: $1"; }

is_pi() { [[ -f /proc/device-tree/model ]] && grep -qi "Raspberry Pi" /proc/device-tree/model; }

ensure_line_in_file() {
  local line="$1" file="$2"
  mkdir -p "$(dirname "$file")"
  touch "$file"
  grep -qxF "$line" "$file" || echo "$line" >> "$file"
}

ensure_group() { getent group "$1" >/dev/null 2>&1 || groupadd "$1"; }

ensure_user() {
  local user="$1"
  id "$user" >/dev/null 2>&1 || useradd -m -s /bin/bash "$user"
}

ensure_dir() {
  local dir="$1" owner="$2" group="$3" mode="$4"
  mkdir -p "$dir"
  chown "$owner:$group" "$dir"
  chmod "$mode" "$dir"
}

apt_install() {
  # shellcheck disable=SC2068
  DEBIAN_FRONTEND=noninteractive apt-get update -y
  DEBIAN_FRONTEND=noninteractive apt-get install -y $@
}
EOF
chmod +x deploy/common/scripts/00-lib.sh
