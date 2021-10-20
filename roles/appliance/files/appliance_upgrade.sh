#!/usr/bin/env bash
#
# Upgrades the Ubersmith Appliance database to the latest release.

docker-compose -p ubersmith exec app_web  php /var/www/appliance_root/www/upgrade.php
