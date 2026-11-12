#!/bin/sh

CONFIG_FILE='/opt/etc/wan-wisp-guard.conf'
NDMQ='/opt/bin/ndmq'

if [ ! -r "$CONFIG_FILE" ]; then
    echo "wan-wisp-guard: configuration not found: $CONFIG_FILE" >&2
    exit 1
fi

# shellcheck disable=SC1090
. "$CONFIG_FILE"

: "${PING_PROFILE:?PING_PROFILE is required}"
: "${WIRED_IF:?WIRED_IF is required}"
: "${WISP_IF:?WISP_IF is required}"
: "${AP_LIST:?AP_LIST is required}"
: "${POLL_SECONDS:=2}"
: "${RETRY_SECONDS:=10}"
: "${BOOT_GRACE_SECONDS:=60}"
: "${DRY_RUN:=1}"
: "${LOG_FILE:=/opt/var/log/wan-wisp-guard.log}"
: "${STATE_FILE:=/tmp/wan-wisp-guard.state}"
: "${LOG_LIMIT_BYTES:=65536}"

rotate_log_if_needed() {
    [ -f "$LOG_FILE" ] || return 0
    size="$(wc -c < "$LOG_FILE" 2>/dev/null | tr -d ' ')"
    case "$size" in
        ''|*[!0-9]*) return 0 ;;
    esac
    if [ "$size" -ge "$LOG_LIMIT_BYTES" ]; then
        mv -f "$LOG_FILE" "$LOG_FILE.1" 2>/dev/null || true
    fi
}

log_message() {
    rotate_log_if_needed
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG_FILE"
}

run_ndm() {
    command="$1"
    if [ "$DRY_RUN" = '1' ]; then
        log_message "DRY-RUN: $command"
        return 0
    fi

    if "$NDMQ" -p "$command" >/dev/null 2>&1; then
        return 0
    fi

    log_message "ERROR: command failed: $command"
    return 1
}

read_ping_status() {
    "$NDMQ" \
        -p "show ping-check $PING_PROFILE" \
        -P 'pingcheck/interface/status' 2>/dev/null \
        | tail -n 1 \
        | tr -d '\r[:space:]' \
        | tr '[:upper:]' '[:lower:]'
}

read_ping_interface() {
    "$NDMQ" \
        -p "show ping-check $PING_PROFILE" \
        -P 'pingcheck/interface/name' 2>/dev/null \
        | tail -n 1 \
        | tr -d '\r[:space:]'
}

apply_wired_mode() {
    failed=0

    # Restore Wi-Fi broadcasting first, then stop the backup WISP connection.
    for ap in $AP_LIST; do
        run_ndm "interface $ap up" || failed=1
    done
    run_ndm "interface $WISP_IF no up" || failed=1

    [ "$failed" -eq 0 ]
}

apply_wisp_mode() {
    failed=0

    # The user's requirement is tied to wired Internet availability, not to a
    # successful WISP connection. Therefore APs are stopped independently and
    # WISP is started even if one of the other commands fails.
    for ap in $AP_LIST; do
        run_ndm "interface $ap no up" || failed=1
    done
    run_ndm "interface $WISP_IF up" || failed=1

    [ "$failed" -eq 0 ]
}

apply_target_mode() {
    target="$1"
    case "$target" in
        wired) apply_wired_mode ;;
        wisp) apply_wisp_mode ;;
        *) return 1 ;;
    esac
}

if [ ! -x "$NDMQ" ]; then
    log_message "FATAL: ndmq is not executable: $NDMQ"
    exit 1
fi

# Never trust a state file left by a previous run or by DRY_RUN mode.
rm -f "$STATE_FILE"
log_message "START: profile=$PING_PROFILE wired=$WIRED_IF wisp=$WISP_IF dry_run=$DRY_RUN"
sleep "$BOOT_GRACE_SECONDS"

last_reported_status='__unset__'
last_reported_binding='__unset__'

while :; do
    bound_interface="$(read_ping_interface)"
    if [ "$bound_interface" != "$WIRED_IF" ]; then
        binding="${bound_interface:-no-interface}"
        if [ "$binding" != "$last_reported_binding" ]; then
            log_message "WAIT: profile=$PING_PROFILE is bound to $binding, expected $WIRED_IF; interfaces are unchanged"
            last_reported_binding="$binding"
        fi
        sleep "$POLL_SECONDS"
        continue
    fi

    last_reported_binding="$bound_interface"
    status="$(read_ping_status)"
    case "$status" in
        pass)
            target='wired'
            ;;
        fail)
            target='wisp'
            ;;
        *)
            target=''
            ;;
    esac

    if [ -z "$target" ]; then
        if [ "$status" != "$last_reported_status" ]; then
            shown_status="${status:-no-data}"
            log_message "WAIT: Ping Check status is $shown_status; interfaces are unchanged"
            last_reported_status="$status"
        fi
        sleep "$POLL_SECONDS"
        continue
    fi

    last_reported_status="$status"
    current=''
    [ -r "$STATE_FILE" ] && current="$(sed -n '1p' "$STATE_FILE")"

    if [ "$current" != "$target" ]; then
        log_message "TRANSITION: ping=$status target=$target"
        if apply_target_mode "$target"; then
            printf '%s\n' "$target" > "$STATE_FILE"
            log_message "APPLIED: mode=$target"
        else
            log_message "RETRY: mode=$target was not applied completely"
            sleep "$RETRY_SECONDS"
            continue
        fi
    fi

    sleep "$POLL_SECONDS"
done
