#!/usr/bin/env bash
#
# Issues a restart to all Ubersmith containers.

docker compose -p ubersmith restart app_cron app_db app_web
