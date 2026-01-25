#!/usr/bin/env bash
set -euo pipefail

fail_missing() {
  local p="$1"
  [[ -f "$p" ]] || { echo "[error] missing: $p"; exit 1; }
}

remote_mkdirs() {
  local host="$1" user="$2"
  shift 2
  local dirs=("$@")
  local joined=""
  for d in "${dirs[@]}"; do joined+=" '$d'"; done
  ssh "${user}@${host}" "set -e; mkdir -p ${joined}"
}

push_root_file() {
  local host="$1" user="$2" src="$3" dst="$4" mode="$5"
  local tmp="/tmp/rt.$(basename "$dst").$(date +%s).$$"

  echo "[push] $(basename "$dst") (root-owned) -> ${dst}"
  scp "$src" "${user}@${host}:${tmp}"
  ssh "${user}@${host}" "set -e; sudo mv '${tmp}' '${dst}' && sudo chown root:root '${dst}' && sudo chmod ${mode} '${dst}'"
}

push_root_file_if_missing() {
  local host="$1" user="$2" src="$3" dst="$4" mode="$5"
  local tmp="/tmp/rt.$(basename "$dst").$(date +%s).$$"

  echo "[push] $(basename "$dst") (root-owned, install-if-missing) -> ${dst}"
  scp "$src" "${user}@${host}:${tmp}"
  ssh "${user}@${host}" "set -e;
    if [ ! -f '${dst}' ]; then
      sudo mv '${tmp}' '${dst}' &&
      sudo chown root:root '${dst}' &&
      sudo chmod ${mode} '${dst}' &&
      echo '[push] installed'
    else
      rm -f '${tmp}' &&
      echo '[push] exists; leaving as-is'
    fi
  "
}

require_remote_cmd_or_warn() {
  local host="$1" user="$2" cmd="$3" hint="$4"
  ssh "${user}@${host}" "
    if command -v ${cmd} >/dev/null 2>&1; then
      echo '${cmd}=ok'
      exit 0
    else
      echo '${cmd}=missing (${hint})'
      exit 0
    fi
  "
}

curl_smoke_retry() {
  local host="$1" user="$2" url="$3" tries="${4:-5}" max_time="${5:-1.5}"

  ssh "${user}@${host}" "
    set +e
    if ! command -v curl >/dev/null 2>&1; then
      echo '[smoke] curl missing; skipping'
      exit 0
    fi
    for i in \$(seq 1 ${tries}); do
      code=\$(curl --max-time ${max_time} -s -o /dev/null -w '%{http_code}' '${url}' 2>/dev/null)
      code=\${code:-000}
      echo \"try=\${i} http=\${code}\"
      [ \"\${code}\" = \"200\" ] && exit 0
      sleep 0.4
    done
    exit 0
  "
}
