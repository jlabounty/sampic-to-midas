#!/bin/bash
# Run the SAMPIC MIDAS -> Gaudi reconstruction inside the pioneer-midas
# container. Requires main/ to have been built with the testbeam libraries
# (cd /workdir/main && ./setup.sh -b -t -e) in this container first.
#
# Environment overrides:
#   SAMPIC_MID     input .mid file   (default: sampic-to-midas/output/run914.mid)
#   SAMPIC_REC     output .root file (default: sampic-to-midas/output/run914_rec.root)
#   SAMPIC_EVTMAX  event limit       (default: -1 = all)
# no -u: the container's setup_container_env.sh references unset variables
set -eo pipefail

source /software/setup_container_env.sh

# setup.sh -e writes the env file into a container-specific subfolder
for env_file in /workdir/main/docker/setenv.sh /workdir/main/setenv.sh; do
    if [ -f "$env_file" ]; then
        source "$env_file"
        break
    fi
done

mkdir -p /workdir/sampic-to-midas/output
cd /workdir/sampic-to-midas/output
exec gaudirun.py /workdir/sampic-to-midas/gaudi/sampic-to-midas.py
