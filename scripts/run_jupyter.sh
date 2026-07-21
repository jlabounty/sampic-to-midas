#!/bin/bash
# Launch jupyter lab inside the pioneer-midas container for the analysis
# notebooks. Reachable from the host browser thanks to run_container.sh's
# --net=host (open the 127.0.0.1 URL that jupyter prints).
# no -u: the container's setup_container_env.sh references unset variables
set -eo pipefail

source /software/setup_container_env.sh

# PyROOT (fallback reader in the notebook): thisroot.sh must be sourced from
# the ROOT install directory in this image.
cd /software/root/install && source bin/thisroot.sh && cd - >/dev/null

# main's env (if built): EDM dictionaries for the PyROOT reader
for env_file in /workdir/main/docker/setenv.sh /workdir/main/setenv.sh; do
    [ -f "$env_file" ] && source "$env_file" && break
done || true

cd /workdir/sampic-to-midas/notebooks
exec /software/venv/bin/jupyter lab --allow-root --no-browser --ip=0.0.0.0
