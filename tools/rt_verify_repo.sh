#!/usr/bin/env bash
set -euo pipefail

# Repo-side invariants for RollingThunder deployment hygiene.
# This script is intentionally strict: violations are deploy blockers.

die() { echo "[verify-repo][error] $*"; exit 2; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo "[verify-repo] repo_root=${REPO_ROOT}"

# ----------------------------
# Helpers (define BEFORE use)
# ----------------------------

verify_unit_name_uniqueness() {
  echo "[verify-repo] systemd unit name uniqueness invariants..."

  # Normalize:
  #  - *.service stays *.service
  #  - *.timer stays *.timer
  #  - *.service.template becomes *.service (because it installs to /etc/systemd/system/foo.service)
  local dupes
  dupes="$(
    find "${REPO_ROOT}/nodes" -type f \( \
        -path "*/systemd/*.service" -o \
        -path "*/systemd/*.timer" -o \
        -path "*/ops/*.service.template" \
      \) -print0 \
    | while IFS= read -r -d '' p; do
        local base
        base="$(basename "$p")"
        if [[ "$base" == *.service.template ]]; then
          base="${base%.template}"  # -> foo.service
        fi
        printf "%s\t%s\n" "$base" "$p"
      done \
    | sort -k1,1 -k2,2 \
    | awk -F'\t' '
        { count[$1]++; paths[$1]=paths[$1] "\n  - " $2 }
        END {
          for (u in count) {
            if (count[u] > 1) {
              print "DUPLICATE: " u paths[u] "\n"
            }
          }
        }'
  )"

  if [[ -n "$dupes" ]]; then
    echo "[verify-repo][error] duplicate unit name(s) detected:"
    echo "$dupes"
    exit 2
  fi
}

# Extract the first /opt/rollingthunder/... path mentioned anywhere in ExecStart.
# Robust against wrappers like:
#   ExecStart=/bin/bash -lc "/opt/rollingthunder/.../thing.sh ..."
#   ExecStart=/opt/rollingthunder/.venv/bin/python /opt/rollingthunder/services/foo.py
#   ExecStart=/usr/bin/env bash /opt/rollingthunder/nodes/rt-display/ops/kiosk.sh
extract_execstart_rt_path() {
  local unit_file="$1"
  local line
  line="$(grep -E '^[[:space:]]*ExecStart=' "$unit_file" | head -n1 || true)"
  [[ -n "$line" ]] || return 0

  # strip leading whitespace + key
  line="$(sed -E 's/^[[:space:]]*ExecStart=//' <<<"$line")"

  # grab first occurrence of /opt/rollingthunder/... up to whitespace or quote
  # (works even if inside quotes)
  sed -nE 's#.*(/opt/rollingthunder/[^ "'"'"'\t]+).*#\1#p' <<<"$line" | head -n1
}


# If ExecStart uses python, extract the python script path token ending in .py
extract_execstart_py_script() {
  local unit_file="$1"

  local line
  line="$(grep -E '^[[:space:]]*ExecStart=' "$unit_file" | head -n1 || true)"
  [[ -n "$line" ]] || return 0

  line="$(sed -E 's/^[[:space:]]*ExecStart=//' <<<"$line")"

  awk '
    {
      for (i=1; i<=NF; i++) {
        if ($i ~ /\.py$/) { print $i; exit 0 }
      }
    }
  ' <<<"$line"
}

# Derive service executable basenames from systemd units that run:
#   /opt/rollingthunder/services/<name>.py
# Then forbid a duplicate copy living at:
#   nodes/rt-controller/<name>.py
derive_controller_service_basenames() {
  find "${REPO_ROOT}/nodes/rt-controller" -type f \( \
      -path "*/systemd/*.service" -o \
      -path "*/ops/*.service.template" \
    \) -print0 \
  | while IFS= read -r -d '' unit; do
      local py
      py="$(extract_execstart_py_script "$unit")"
      [[ -n "$py" ]] || continue

      if [[ "$py" == /opt/rollingthunder/services/*.py ]]; then
        basename "$py"
      fi
    done \
  | sort -u
}

# For every *.timer in nodes/*/{systemd,ops}, ensure:
# - a matching *.service exists in the same folder
# - the timer's Unit= points to that service (or defaults to same base)
verify_timer_pairs_in_dir() {
  local dir="$1"
  [[ -d "$dir" ]] || return 0

  while IFS= read -r timer; do
    local base service expected unit_line unit_target

    base="$(basename "$timer" .timer)"
    service="${dir}/${base}.service"

    [[ -f "$service" ]] || die "timer has no matching service in same dir: ${timer} -> expected ${service}"

    # systemd default: foo.timer triggers foo.service if Unit= not specified.
    expected="${base}.service"

    unit_line="$(grep -E '^[[:space:]]*Unit=' "$timer" | tail -n1 || true)"
    if [[ -z "$unit_line" ]]; then
        die "timer missing explicit Unit=: ${timer} (must declare Unit=${expected})"
    fi
    unit_target="$(sed -E 's/^[[:space:]]*Unit=//' <<<"$unit_line" | tr -d '\r')"
    [[ "$unit_target" == "$expected" ]] || die "timer Unit= mismatch: ${timer} has Unit=${unit_target}, expected ${expected}"

  done < <(find "$dir" -maxdepth 1 -type f -name "*.timer" | sort)
}

# ----------------------------
# Invariant 1: Forbidden duplicates (derived)
# ----------------------------
echo "[verify-repo] forbidden duplicates..."

while IFS= read -r base; do
  # base like "ui_snapshot_api.py"
  [[ -n "$base" ]] || continue

  local_repo_service="${REPO_ROOT}/nodes/rt-controller/services/${base}"
  local_repo_dup="${REPO_ROOT}/nodes/rt-controller/${base}"

  [[ -f "$local_repo_service" ]] || die "service referenced by systemd but missing in nodes/rt-controller/services/: ${base}"
  [[ ! -f "$local_repo_dup" ]] || die "forbidden duplicate exists (must not live at nodes/rt-controller/<name>.py): ${local_repo_dup}"
done < <(derive_controller_service_basenames)

echo "[verify-repo] controller service single-home invariants..."

# Extract .py token from ExecStart= (first ExecStart line only)
extract_execstart_py_script() {
  local unit_file="$1"
  local line
  line="$(grep -E '^[[:space:]]*ExecStart=' "$unit_file" | head -n1 || true)"
  [[ -n "$line" ]] || return 0
  line="$(sed -E 's/^[[:space:]]*ExecStart=//' <<<"$line")"

  awk '
    {
      for (i=1; i<=NF; i++) {
        if ($i ~ /\.py$/) { print $i; exit 0 }
      }
    }
  ' <<<"$line"
}

# Find controller units that ExecStart /opt/rollingthunder/services/<name>.py
derive_controller_service_basenames() {
  find "${REPO_ROOT}/nodes/rt-controller" -type f \( \
      -path "*/systemd/*.service" -o \
      -path "*/ops/*.service.template" \
    \) -print0 \
  | while IFS= read -r -d '' unit; do
      local py
      py="$(extract_execstart_py_script "$unit" || true)"
      [[ -n "$py" ]] || continue
      if [[ "$py" == /opt/rollingthunder/services/*.py ]]; then
        basename "$py"
      fi
    done \
  | sort -u
}

while IFS= read -r base; do
  repo_service="${REPO_ROOT}/nodes/rt-controller/services/${base}"
  repo_dup="${REPO_ROOT}/nodes/rt-controller/${base}"

  [[ -f "$repo_service" ]] || die "service referenced by systemd but missing in nodes/rt-controller/services/: ${base}"
  [[ ! -f "$repo_dup" ]] || die "forbidden duplicate exists (must not live at nodes/rt-controller/<name>.py): ${repo_dup}"
done < <(derive_controller_service_basenames)


# ----------------------------
# Invariant 2: systemd ExecStart invariants
# ----------------------------
echo "[verify-repo] systemd ExecStart invariants..."

while IFS= read -r unit; do
  rel="${unit#${REPO_ROOT}/}"
  node_id="$(awk -F/ '{print $2}' <<<"$rel")"

  rt_path="$(extract_execstart_rt_path "$unit")"
  py_script="$(extract_execstart_py_script "$unit")"

  # If a unit doesn't reference /opt/rollingthunder at all, ignore it (out of scope).
  [[ -n "$rt_path" ]] || continue

  case "$node_id" in
    rt-controller)
      # Controller may run:
      # - python scripts from /opt/rollingthunder/services/*.py  (root-owned execs)
      # - python scripts from /opt/rollingthunder/nodes/rt-controller/*.py (node code)
      #
      # If the unit references /opt/rollingthunder but doesn't run a .py, that's suspicious.
      if [[ -z "$py_script" ]]; then
        die "unit '$rel' references /opt/rollingthunder but no .py found in ExecStart (unexpected for rt-controller): rt_path=${rt_path}"
      fi

      if [[ "$py_script" == /opt/rollingthunder/services/*.py ]]; then
        base="$(basename "$py_script")"
        repo_service="${REPO_ROOT}/nodes/rt-controller/services/${base}"
        repo_dup="${REPO_ROOT}/nodes/rt-controller/${base}"

        [[ -f "$repo_service" ]] || die "unit '$rel' ExecStart runs ${py_script} but repo is missing nodes/rt-controller/services/${base}"
        [[ ! -f "$repo_dup" ]] || die "unit '$rel' runs ${py_script} but forbidden duplicate exists at nodes/rt-controller/${base}"

      elif [[ "$py_script" == /opt/rollingthunder/nodes/rt-controller/*.py ]]; then
        base="$(basename "$py_script")"
        repo_node="${REPO_ROOT}/nodes/rt-controller/${base}"
        [[ -f "$repo_node" ]] || die "unit '$rel' ExecStart runs ${py_script} but repo is missing nodes/rt-controller/${base}"

      else
        die "unit '$rel' has invalid ExecStart python script path for rt-controller: ${py_script}"
      fi
      ;;
    *)
      # Non-controller nodes must not ExecStart from /opt/rollingthunder/services/
      if [[ "$rt_path" == /opt/rollingthunder/services/* ]]; then
        die "unit '$rel' on node '${node_id}' must not ExecStart from /opt/rollingthunder/services/: ${rt_path}"
      fi

      # Non-controller nodes: strict allow-list for any /opt/rollingthunder path referenced by ExecStart.
      #
      # Allowed:
      #   - node-owned code: /opt/rollingthunder/nodes/<node_id>/...
      #   - shared tools:    /opt/rollingthunder/tools/...
      #   - venv binaries:   /opt/rollingthunder/.venv/...   (interpreter path etc.)
      #
      # Everything else under /opt/rollingthunder is forbidden.
      if [[ "$rt_path" == /opt/rollingthunder/nodes/${node_id}/* ]]; then
        : # ok
      elif [[ "$rt_path" == /opt/rollingthunder/tools/* ]]; then
        : # ok
      elif [[ "$rt_path" == /opt/rollingthunder/.venv/* ]]; then
        : # ok
      else
        die "unit '$rel' on node '${node_id}' has forbidden ExecStart /opt/rollingthunder path (allow-list violation): ${rt_path}"
      fi
      ;;
  esac
done < <(
  find "${REPO_ROOT}/nodes" -type f \( \
      -path "*/systemd/*.service" -o \
      -path "*/ops/*.service.template" \
    \) | sort
)

echo "[verify-repo] systemd unit name uniqueness invariants..."

# Any unit name (basename) must appear in only ONE repo location.
# Otherwise you get “which one is authoritative?” drift.
verify_unit_name_uniqueness() {
  local tmp
  tmp="$(mktemp)"
  trap 'rm -f "$tmp"' RETURN

  find "${REPO_ROOT}/nodes" -type f \( \
      -path "*/systemd/*.service" -o \
      -path "*/systemd/*.timer" -o \
      -path "*/ops/*.service.template" \
    \) -print0 \
  | while IFS= read -r -d '' f; do
      local base
      base="$(basename "$f")"
      # normalize templates: foo.service.template -> foo.service
      base="${base%.template}"
      printf "%s\t%s\n" "$base" "$f"
    done \
  | sort > "$tmp"

  # Find duplicate basenames
  local dups
  dups="$(cut -f1 "$tmp" | uniq -d || true)"
  [[ -n "$dups" ]] || return 0

  while IFS= read -r name; do
    [[ -n "$name" ]] || continue
    echo "[verify-repo][error] duplicate unit name '${name}' found in:"
    # Match lines that start with: <name><TAB>
    awk -F'\t' -v n="$name" '$1==n { print "  - " $2 }' "$tmp"
  done <<<"$dups"

  exit 2
}


verify_unit_name_uniqueness


# ----------------------------
# Invariant 3: timer/service pairing
# ----------------------------
echo "[verify-repo] systemd timer/service pairing invariants..."

for node in "${REPO_ROOT}/nodes/"*; do
  [[ -d "$node" ]] || continue
  verify_timer_pairs_in_dir "${node}/systemd"
  verify_timer_pairs_in_dir "${node}/ops"
done

echo "[verify-repo] OK"
