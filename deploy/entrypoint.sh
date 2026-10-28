#!/bin/sh
set -eu
install -d -m 700 -o crm -g crm /data/uploads
exec "$@"
