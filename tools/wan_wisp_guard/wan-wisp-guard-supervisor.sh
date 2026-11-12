#!/bin/sh

WORKER='/opt/sbin/wan-wisp-guard-worker.sh'
LOG_FILE='/opt/var/log/wan-wisp-guard.log'
child_pid=''
stopping=0

log_message() {
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG_FILE"
}

stop_child() {
    stopping=1
    if [ -n "$child_pid" ] && kill -0 "$child_pid" 2>/dev/null; then
        kill "$child_pid" 2>/dev/null || true
        wait "$child_pid" 2>/dev/null || true
    fi
    exit 0
}

trap stop_child INT TERM HUP

while [ "$stopping" -eq 0 ]; do
    "$WORKER" &
    child_pid=$!
    wait "$child_pid"
    exit_code=$?
    child_pid=''

    [ "$stopping" -eq 1 ] && break
    log_message "SUPERVISOR: worker exited with code $exit_code; restart in 5 seconds"
    sleep 5
done
