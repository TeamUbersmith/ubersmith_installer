#!/usr/bin/env bash
#
# Mount the ubersmith_rrds volume and make a backup.

TIMESTAMP=`date +%Y-%m-%d`

/usr/bin/docker run --rm --volumes-from ubersmith-app_web-1 -v $(pwd):/backup \
  busybox tar cvf /backup/rrd_backup_$TIMESTAMP.tar /var/www/appliance_root/rrds