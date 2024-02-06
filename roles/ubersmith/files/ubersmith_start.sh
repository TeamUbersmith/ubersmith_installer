#!/usr/bin/env bash
#
# Recreates selected base Ubersmith containers.

docker compose -p ubersmith up -d cron db mail php solr web rsyslog
docker compose -p ubersmith up --scale redis=3 -d redis
