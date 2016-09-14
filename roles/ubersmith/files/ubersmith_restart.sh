#!/usr/bin/env bash
#
# Issues a restart to all Ubersmith containers.

cd /usr/local/ubersmith
docker-compose -p ubersmith restart
