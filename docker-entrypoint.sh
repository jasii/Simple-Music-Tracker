#!/bin/sh
# Run the app as PUID:PGID (default 1000:1000) instead of root, so what it
# writes under /data belongs to you on the host. GUID is accepted as another
# name for PGID. PUID=0 keeps the old behaviour and runs as root.
set -e

PUID="${PUID:-1000}"
PGID="${PGID:-${GUID:-1000}}"

# Started with --user (or user: in compose): nothing to switch, run as that.
if [ "$(id -u)" != "0" ] || [ "$PUID" = "0" ]; then
    exec "$@"
fi

case "$PUID$PGID" in
    *[!0-9]*)
        echo "PUID and PGID must be numbers (got PUID=$PUID PGID=$PGID)" >&2
        exit 1
        ;;
esac

# The built-in account takes the requested ids, so the process has a name,
# a home and a group to go with them.
[ "$(id -g app)" = "$PGID" ] || groupmod -o -g "$PGID" app
[ "$(id -u app)" = "$PUID" ] || usermod -o -u "$PUID" app

# Hand over what earlier (root) runs left behind. Only files owned by someone
# else are touched, so a big artwork cache isn't rewritten on every start.
mkdir -p /data
find /data \( ! -user "$PUID" -o ! -group "$PGID" \) \
    -exec chown -h "$PUID:$PGID" {} + 2>/dev/null || \
    echo "Warning: could not take ownership of everything under /data" >&2

chown -R "$PUID:$PGID" /home/app

echo "Running as uid=$PUID gid=$PGID"
export HOME=/home/app
exec setpriv --reuid="$PUID" --regid="$PGID" --init-groups "$@"
