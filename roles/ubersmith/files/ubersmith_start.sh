#!/usr/bin/env bash
#
# Stops, removes, and recreates selected base Ubersmith containers.

cd /usr/local/ubersmith
docker-compose -p ubersmith rm -f -s
docker-compose -p ubersmith up -d cron db mail php solr web
