#!/bin/sh
set -eu

BASE_DIR=${BASE_DIR:-/volume1/docker/knowledge-service}
pid_file="$BASE_DIR/data/service.pid"
if [ -f "$pid_file" ]; then
  pid=$(cat "$pid_file")
  kill "$pid" 2>/dev/null || true
  rm -f "$pid_file"
fi

