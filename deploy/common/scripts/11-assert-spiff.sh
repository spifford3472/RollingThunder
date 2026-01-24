cat > deploy/common/scripts/11-assert-spiff.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=deploy/common/scripts/00-lib.sh
source "$(cd "$(dirname "$0")" && pwd)/00-lib.sh"
require_user "spiff"
log "OK: running as spiff."
EOF
chmod +x deploy/common/scripts/11-assert-spiff.sh
