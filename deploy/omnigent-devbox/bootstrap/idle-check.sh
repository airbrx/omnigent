#!/bin/bash
# Runs on a 5-minute timer. Accumulates idle minutes and stops the instance
# once the threshold is crossed. Any activity resets the counter.
set -uo pipefail

CHECK_INTERVAL_MIN=5
STATE=/var/lib/omnigent-devbox-idle.count
IDLE_MINUTES=60
LOAD_THRESHOLD=0.4
LOG_WINDOW_MIN=10
LOG_DIR=/home/michael/.omnigent/logs
# shellcheck disable=SC1091
[ -f /etc/omnigent/devbox-idle.conf ] && . /etc/omnigent/devbox-idle.conf

reason=""

is_active() {
  # 1. A human is attached over SSM. This is the ONLY reliable "someone is
  #    here" signal: SSM shells create no utmp entry, and `who` is poisoned by
  #    tmux panes that outlive every session.
  if pgrep -f 'ssm-session-worker' >/dev/null 2>&1; then
    reason="ssm session attached"; return 0
  fi

  # 2. Something is actually burning CPU (agent turn, build, test run).
  #    Field 2 of /proc/loadavg is the 5-minute average.
  local load
  load=$(cut -d' ' -f2 /proc/loadavg)
  if awk -v l="$load" -v t="$LOAD_THRESHOLD" 'BEGIN{exit !(l>t)}'; then
    reason="load ${load} > ${LOAD_THRESHOLD}"; return 0
  fi

  # 3. A session log was written recently -- catches an agent turn that is
  #    waiting on the network rather than burning CPU.
  if [ -d "$LOG_DIR" ] && \
     [ -n "$(find "$LOG_DIR" -type f -newermt "-${LOG_WINDOW_MIN} min" -print -quit 2>/dev/null)" ]; then
    reason="session log written within ${LOG_WINDOW_MIN}m"; return 0
  fi

  return 1
}

if is_active; then
  echo 0 > "$STATE"
  logger -t omnigent-idle "active (${reason}); counter reset"
  exit 0
fi

n=$(cat "$STATE" 2>/dev/null || echo 0)
case "$n" in ''|*[!0-9]*) n=0 ;; esac
n=$((n + CHECK_INTERVAL_MIN))
echo "$n" > "$STATE"
logger -t omnigent-idle "idle ${n}/${IDLE_MINUTES} min"

if [ "$n" -ge "$IDLE_MINUTES" ]; then
  logger -t omnigent-idle "threshold reached; stopping instance"
  # InstanceInitiatedShutdownBehavior=stop -- this STOPS, keeping the EBS
  # volume and every bit of setup. Waking is `aws ec2 start-instances`.
  /sbin/shutdown -h now "omnigent dev box idle ${IDLE_MINUTES}m"
fi
