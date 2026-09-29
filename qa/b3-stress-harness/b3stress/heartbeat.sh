#!/usr/bin/env bash
# Sends a tagged /health probe every 4s so tail liveness is observable; stops when $D/stop exists.
D="$1"; U=https://greenside-entry-allocator-qa.hidden-cherry-619e.workers.dev; n=0
while [ ! -f "$D/stop" ]; do n=$((n+1)); r=$(curl -sS --max-time 5 "$U/health?probe=hb-$n" | jq -r .dry_run 2>/dev/null); echo "$(date -u +%T) hb-$n dry_run=$r" >> "$D/heartbeat.log"; sleep 4; done
