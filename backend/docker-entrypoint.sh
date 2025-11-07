#!/usr/bin/env bash
set -euo pipefail

APP_USER=${APP_USER:-gazarr}
APP_GROUP=${APP_GROUP:-gazarr}

DATA_DIR=${GAZARR_DATA_DIR:-/app/data}
COVERS_DIR=${GAZARR_COVERS_DIR:-/app/covers}
STAGING_DIR=${GAZARR_STAGING_DIR:-/staging}
LIBRARY_DIR=${GAZARR_LIBRARY_DIR:-/library}

ensure_dir() {
    local path="$1"
    local recursive="${2:-false}"
    mkdir -p "$path"
    if [[ "$recursive" == "true" ]]; then
        chown -R "${APP_USER}:${APP_GROUP}" "$path"
    else
        chown "${APP_USER}:${APP_GROUP}" "$path"
    fi
}

run_as_root() {
    ensure_dir "$DATA_DIR" true
    ensure_dir "$COVERS_DIR" true
    ensure_dir "$STAGING_DIR" true
    ensure_dir "$LIBRARY_DIR"
    exec gosu "${APP_USER}:${APP_GROUP}" "$@"
}

run_without_root() {
    echo "WARNING: Gazarr entrypoint running without root privileges; skipping permission fix." >&2
    exec "$@"
}

if [[ "$(id -u)" -ne 0 ]]; then
    run_without_root "$@"
else
    run_as_root "$@"
fi
