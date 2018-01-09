#!/usr/bin/env bash
#
# Stops, removes, and recreates selected base Ubersmith containers.

docker-compose -p ubersmith rm -f -s
docker-compose -p ubersmith up --scale redis=3 -d redis
docker-compose -p ubersmith up -d cron db mail php solr web
