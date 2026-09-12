#!/bin/bash
# Register the fake-SAMPIC custom pages with mhttpd.
#
#     scripts/register-custom-pages.sh [--remove] [--replace] [--prefix X]
#
# Thin wrapper; all the logic is in install/register_pages.py.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=fake-sampic-env.sh
source "$SCRIPT_DIR/fake-sampic-env.sh"
fs_require_midas
fs_require_python
cd "$FS_REPO"
exec "$FS_PYTHON" -m install.register_pages "$@"
