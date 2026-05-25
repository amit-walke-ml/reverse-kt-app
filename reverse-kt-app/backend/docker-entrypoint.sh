#!/bin/sh
set -e

# Docker embedded DNS can lag behind db healthcheck (common on WSL / Docker Desktop).
_wait_for_compose_db() {
  case "${DATABASE_URL_OVERRIDE:-}" in
    *@db:*|*@db/*) ;;
    *) return 0 ;;
  esac

  echo "Waiting for PostgreSQL host 'db' (DNS + port 5432)..."
  i=0
  while [ "$i" -lt 60 ]; do
    if python -c "
import socket
import sys
try:
    socket.gethostbyname('db')
    s = socket.create_connection(('db', 5432), 2)
    s.close()
except OSError:
    sys.exit(1)
"; then
      echo "Database host 'db' is reachable."
      return 0
    fi
    i=$((i + 1))
    sleep 1
  done
  echo "Timed out waiting for host 'db'. Ensure kt-api and db share the same compose network." >&2
  exit 1
}

_wait_for_compose_db
exec "$@"
