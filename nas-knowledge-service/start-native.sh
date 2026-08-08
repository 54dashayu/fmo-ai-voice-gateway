#!/bin/sh
set -eu

BASE_DIR=${BASE_DIR:-/volume1/docker/knowledge-service}
cd "$BASE_DIR"
mkdir -p data/documents data/temporary-audio data/logs

if [ -f data/service.pid ] && kill -0 "$(cat data/service.pid)" 2>/dev/null; then
  exit 0
fi

set -a
. "$BASE_DIR/.env"
set +a
export FMO_KB_DATA_DIR="$BASE_DIR/data"

python_bin=""
for candidate in \
  /var/packages/Python3.13/target/usr/local/bin/python3.13 \
  /var/packages/Python3.13/target/usr/local/bin/python3 \
  /var/packages/python313/target/usr/local/bin/python3.13 \
  /usr/local/bin/python3.13 \
  /usr/local/bin/python3
do
  if [ -x "$candidate" ]; then
    python_bin=$candidate
    break
  fi
done

if [ -z "$python_bin" ]; then
  echo "Python 3 runtime not found" >&2
  exit 2
fi

nohup "$python_bin" "$BASE_DIR/service.py" >>"$BASE_DIR/data/logs/service.log" 2>&1 &
echo $! > "$BASE_DIR/data/service.pid"

