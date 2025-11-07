#!/usr/bin/env bash
set -euo pipefail

APP_USER=${APP_USER:-gazarr}
APP_GROUP=${APP_GROUP:-gazarr}
DATA_DIR=${GAZARR_DATA_DIR:-/app/data}
COVERS_DIR=${GAZARR_COVERS_DIR:-/app/covers}

run_as_root() {
    mkdir -p "$DATA_DIR" "$COVERS_DIR"
    chown -R "${APP_USER}:${APP_GROUP}" "$DATA_DIR" "$COVERS_DIR"
    exec gosu "${APP_USER}:${APP_GROUP}" "$@"
}

run_without_root() {
    echo "WARNING: Gazarr entrypoint running without root privileges; skipping permission fix for ${DATA_DIR}." >&2
    exec "$@"
}

if [[ "$(id -u)" -ne 0 ]]; then
    run_without_root "$@"
else
    run_as_root "$@"
fi
