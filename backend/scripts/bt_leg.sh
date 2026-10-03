#!/usr/bin/env bash
# One backtest leg under a memory watch. The runner logs cannot be read from the session that drives the run, and a
# runner that runs out of memory is lost without a word ("The operation was canceled", 2026-10-03). So the leg is
# stopped (TERM) before that, when the memory left falls under BT_MIN_AVAIL_MB, and what was seen goes into job
# annotations, which can be read: the memory left, the largest processes, the last progress lines.
# The run keeps its checkpoint every week, so a stopped leg loses at most the week it was in.
set -u
LOG="${BT_LEG_LOG:-leg.log}"
MIN_MB="${BT_MIN_AVAIL_MB:-700}"
: > "$LOG"
setsid "$@" >> "$LOG" 2>&1 &
pid=$!
tail -n +1 -f "$LOG" &
tailpid=$!
low=""
least=999999
while kill -0 "$pid" 2>/dev/null; do
  avail=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)
  [ "$avail" -lt "$least" ] && least=$avail
  if [ "$avail" -lt "$MIN_MB" ]; then
    low="$avail"
    echo "::error title=backtest leg stopped: memory::${avail} MB left; largest: $(ps -eo rss=,comm= --sort=-rss | head -4 | awk '{printf "%s %d MB; ", $2, $1/1024}')"
    kill -TERM -- "-$pid" 2>/dev/null
    sleep 20
    kill -KILL -- "-$pid" 2>/dev/null
    break
  fi
  sleep 3
done
wait "$pid"
rc=$?
kill "$tailpid" 2>/dev/null
last=$(grep -E '^\{' "$LOG" | tail -3 | tr '\n' ' ' | cut -c1-900)
err=$(grep -E 'Error|Traceback|Killed' "$LOG" | tail -3 | tr '\n' ' ' | cut -c1-600)
echo "::notice title=backtest leg::exit ${rc}; least memory left ${least} MB; last: ${last}"
[ -n "$err" ] && echo "::warning title=backtest leg errors::${err}"
[ -n "$low" ] && exit 75
exit "$rc"
