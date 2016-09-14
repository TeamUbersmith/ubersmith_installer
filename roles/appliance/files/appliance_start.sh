#!/usr/bin/env bash
#
# Stops, removes, and recreates selected base Ubersmith containers.

cd /usr/local/ubersmith
docker-compose -p ubersmith stop
docker-compose -p ubersmith rm -f
docker-compose -p ubersmith up -d app_cron app_db app_web
