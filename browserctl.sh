#!/bin/sh
set -eu

PROFILE_DIR="${CHROME_PROFILE_DIR:-/tmp/chrome-data}"
CHROME_PORT="${CHROME_INTERNAL_PORT:-19222}"
PUBLIC_PORT="${CHROME_PUBLIC_PORT:-9222}"
CHROME_PID_FILE="/var/run/chromium.pid"
SOCAT_PID_FILE="/var/run/socat-cdp.pid"

start_chrome() {
  mkdir -p "$PROFILE_DIR"
  if [ -f "$CHROME_PID_FILE" ] && kill -0 "$(cat "$CHROME_PID_FILE")" 2>/dev/null; then
    echo "chromium already running"
    exit 0
  fi

  DISPLAY="${DISPLAY:-:99}" HOME="/root" chromium \
    --no-sandbox \
    --disable-gpu \
    --disable-software-rasterizer \
    --disable-dev-shm-usage \
    --remote-debugging-address=0.0.0.0 \
    --remote-debugging-port="${CHROME_PORT}" \
    --window-size=1280,800 \
    --start-maximized \
    --no-first-run \
    --disable-translate \
    --disable-default-apps \
    --user-data-dir="$PROFILE_DIR" \
    >/proc/1/fd/1 2>/proc/1/fd/2 &
  echo "$!" > "$CHROME_PID_FILE"

  socat TCP-LISTEN:${PUBLIC_PORT},fork,reuseaddr,bind=0.0.0.0 TCP:127.0.0.1:${CHROME_PORT} \
    >/proc/1/fd/1 2>/proc/1/fd/2 &
  echo "$!" > "$SOCAT_PID_FILE"
}

stop_process() {
  pid_file="$1"
  if [ -f "$pid_file" ]; then
    pid="$(cat "$pid_file")"
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      sleep 1
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$pid_file"
  fi
}

stop_chrome() {
  stop_process "$SOCAT_PID_FILE"
  stop_process "$CHROME_PID_FILE"
}

status_chrome() {
  if [ -f "$CHROME_PID_FILE" ] && kill -0 "$(cat "$CHROME_PID_FILE")" 2>/dev/null; then
    echo "running"
  else
    echo "stopped"
  fi
}

reset_profile() {
  mkdir -p "$PROFILE_DIR"
  find "$PROFILE_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
}

case "${1:-}" in
  start)
    start_chrome
    ;;
  stop)
    stop_chrome
    ;;
  restart)
    stop_chrome
    start_chrome
    ;;
  status)
    status_chrome
    ;;
  reset-profile)
    reset_profile
    ;;
  *)
    echo "usage: browserctl {start|stop|restart|status|reset-profile}" >&2
    exit 1
    ;;
esac
