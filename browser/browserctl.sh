#!/bin/sh
set -eu

PROFILE_DIR="${CHROME_PROFILE_DIR:-/tmp/chrome-data}"
CHROME_PORT="${CHROME_INTERNAL_PORT:-19222}"
PUBLIC_PORT="${CHROME_PUBLIC_PORT:-9222}"
CHROME_PID_FILE="/var/run/chromium.pid"
SOCAT_PID_FILE="/var/run/socat-cdp.pid"

log() {
  printf '%s\n' "$1" >&2
}

is_running_from_pid_file() {
  pid_file="$1"
  [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null
}

wait_for_cdp() {
  attempts=20
  while [ "$attempts" -gt 0 ]; do
    if curl -fsS "http://127.0.0.1:${PUBLIC_PORT}/json/version" >/dev/null 2>&1; then
      return 0
    fi
    attempts=$((attempts - 1))
    sleep 1
  done
  return 1
}

start_chrome() {
  mkdir -p "$PROFILE_DIR"
  if is_running_from_pid_file "$CHROME_PID_FILE"; then
    log "chromium already running"
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

  if ! wait_for_cdp; then
    log "cdp endpoint did not become ready in time"
    stop_chrome
    exit 1
  fi
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
  if is_running_from_pid_file "$CHROME_PID_FILE"; then
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
  health)
    wait_for_cdp
    ;;
  *)
    echo "usage: browserctl {start|stop|restart|status|reset-profile|health}" >&2
    exit 1
    ;;
esac
