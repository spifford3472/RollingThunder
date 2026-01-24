cat > deploy/common/scripts/10-assert-root.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=deploy/common/scripts/00-lib.sh
source "$(cd "$(dirname "$0")" && pwd)/00-lib.sh"
require_root
log "OK: running as root."
EOF
chmod +x deploy/common/scripts/10-assert-root.sh
