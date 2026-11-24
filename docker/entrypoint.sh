#!/bin/sh
# Keep themes in /data so the ones you import survive container updates; refresh the built-in ones each start.
set -e
mkdir -p /data/themes
cp -r /app/themes-builtin/. /data/themes/
ln -sfn /data/themes /app/vsdpad/themes
if [ -z "$VSDPAD_PASSWORD" ]; then
  echo "Set VSDPAD_PASSWORD (the password for the settings page and for your PC to connect)." >&2
  exit 2
fi
exec python /app/run.py --server --data /data --port "${VSDPAD_PORT:-8765}" "$@"
