#!/usr/bin/env bash
#
# Recreates selected base Ubersmith containers.

REDIS_SCALE_SIZE=1

docker-compose -p ubersmith up --scale redis=$REDIS_SCALE_SIZE -d redis
docker-compose -p ubersmith up -d cron db mail php solr web 
