#!/usr/bin/env bash
set -euo pipefail

# ---- ssh/scp options (safe array parsing) ----
SSH_OPTS=()
SCP_OPTS=()

# Allow override via env var, but parse into an array safely.
# Example:
#   export RT_SSH_OPTS="-i ~/.ssh/id_ed25519_rt_deploy -o IdentitiesOnly=yes"
if [[ -n "${RT_SSH_OPTS:-}" ]]; then
  # shellcheck disable=SC2206
  SSH_OPTS=( ${RT_SSH_OPTS} )
  # scp takes similar options; reuse
  SCP_OPTS=( ${RT_SSH_OPTS} )
fi

# Optional: make host key prompts non-interactive/stable
SSH_OPTS+=( -o BatchMode=yes )
SCP_OPTS+=( -o BatchMode=yes )

fail_missing() {
  local p="$1"
  [[ -f "$p" ]] || { echo "[error] missing: $p"; exit 1; }
}

remote_mkdirs() {
  local host="$1" user="$2"
  shift 2
  # pass dirs as args to avoid quoting edge-cases
  ssh "${SSH_OPTS[@]}" "${user}@${host}" bash -lc '
    set -e
    for d in "$@"; do
      mkdir -p "$d"
    done
  ' -- "$@"
}

fail_missing_dir() {
  local p="$1"
  [[ -d "${p}" ]] || die "[error] missing dir: ${p}"
}

push_root_file() {
  local host="$1" user="$2" src="$3" dst="$4" mode="$5"
  local tmp="/tmp/rt.$(basename "$dst").$(date +%s).$$"

  echo "[push] $(basename "$dst") (root-owned) -> ${dst}"
  scp "${SCP_OPTS[@]}" "$src" "${user}@${host}:${tmp}"
  ssh "${SSH_OPTS[@]}" "${user}@${host}" "set -e; sudo mv '$tmp' '$dst' && sudo chown root:root '$dst' && sudo chmod '$mode' '$dst'"
}

push_root_file_if_missing() {
  local host="$1" user="$2" src="$3" dst="$4" mode="$5"
  local tmp="/tmp/rt.$(basename "$dst").$(date +%s).$$"

  echo "[push] $(basename "$dst") (root-owned, install-if-missing) -> ${dst}"
  scp "${SCP_OPTS[@]}" "$src" "${user}@${host}:${tmp}"
  ssh "${SSH_OPTS[@]}" "${user}@${host}" "set -e;
    if [ ! -f '$dst' ]; then
      sudo mv '$tmp' '$dst' &&
      sudo chown root:root '$dst' &&
      sudo chmod '$mode' '$dst' &&
      echo '[push] installed'
    else
      rm -f '$tmp' &&
      echo '[push] exists; leaving as-is'
    fi
  "
}

require_remote_cmd_or_warn() {
  local host="${1:-}" user="${2:-}" cmd="${3:-}" hint="${4:-}"

  if [[ -z "$host" || -z "$user" || -z "$cmd" ]]; then
    echo "[smoke] WARN: require_remote_cmd_or_warn missing args: host='${host}' user='${user}' cmd='${cmd}'"
    return 0
  fi

  ssh "${user}@${host}" "
    if command -v '${cmd}' >/dev/null 2>&1; then
      echo '${cmd}=ok'
    else
      echo '${cmd}=missing (${hint})'
    fi
    exit 0
  "
}

curl_smoke_retry() {
  local host="$1" user="$2" url="$3"
  local tries="${4:-5}"
  local max_time="${5:-1.5}"

  # local validation (prevents empty args causing remote bash -c errors)
  [[ -n "${host}" && -n "${user}" && -n "${url}" ]] || {
    echo "[smoke] curl_smoke_retry missing args host/user/url"
    return 0
  }
  [[ "${tries}" =~ ^[0-9]+$ ]] || tries=5

  ssh "${user}@${host}" "set +e
    if ! command -v curl >/dev/null 2>&1; then
      echo '[smoke] curl missing; skipping'
      exit 0
    fi

    i=1
    while [ \"\$i\" -le \"${tries}\" ]; do
      code=\$(curl --max-time ${max_time} -s -o /dev/null -w '%{http_code}' '${url}' 2>/dev/null)
      code=\${code:-000}
      echo \"try=\${i} http=\${code}\"
      [ \"\${code}\" = \"200\" ] && exit 0
      i=\$((i+1))
      sleep 0.4
    done
    exit 0
  "
}

push_node_json() {
  local host="$1"
  local user="$2"
  local src="$3"
  local dst="/etc/rollingthunder/node.json"
  local mode="${4:-644}"

  fail_missing "${src}"

  # Default: install-if-missing. Explicit override allowed.
  local force="${FORCE_NODE_JSON:-0}"

  if [[ "${force}" == "1" ]]; then
    echo "[push] FORCE_NODE_JSON=1 -> overwriting ${dst}"
    push_root_file "${host}" "${user}" "${src}" "${dst}" "${mode}"
  else
    echo "[push] ensure ${dst} exists (install-if-missing)"
    push_root_file_if_missing "${host}" "${user}" "${src}" "${dst}" "${mode}"
  fi
}

# ---- end lib.sh ----