#!/usr/bin/env bash
#
# Recreates selected base Ubersmith containers.

docker-compose -p ubersmith up -d cron db mail php solr web 
docker-compose -p ubersmith up --scale redis=1 -d redis
