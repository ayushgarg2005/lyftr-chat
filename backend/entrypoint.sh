#!/bin/sh
# Mounted disks on some hosts are root-owned. Fix /data, then drop to the app user.
set -e
if [ "$(id -u)" = "0" ]; then
    mkdir -p /data
    chown -R app:app /data 2>/dev/null || true
    exec setpriv --reuid=1000 --regid=1000 --init-groups "$@"
fi
exec "$@"
