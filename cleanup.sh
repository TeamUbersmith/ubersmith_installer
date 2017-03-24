#!/usr/bin/env bash
set -e

# Clean up dangling Docker images to conserve disk space.

docker rmi -f $(docker images -q -f dangling=true)