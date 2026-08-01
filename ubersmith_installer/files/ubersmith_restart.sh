#!/usr/bin/env bash
#
# Issues a restart to all Ubersmith containers.
export MAINTENANCE=0

docker compose -p ubersmith restart
