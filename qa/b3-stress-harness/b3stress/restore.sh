#!/usr/bin/env bash
# Strict restore: deploy --env qa (DRY_RUN=true), then the all-request gate on the restored version (600s budget).
S=__SCRATCHPAD__; D=$S/b3stress; cd $S/b3qa/greenside-entry-allocator && export WRANGLER_SEND_METRICS=false; ts() { date -u +%T; }
[ "$(git rev-parse HEAD)" = 610e1899f352c09848c3bbc79630a0a9289d5658 ] && [ -z "$(git status --porcelain --untracked-files=all)" ] || { echo "ABORT: repo not clean at 610e189"; exit 1; }
date -u +%s%3N > $D/trestore.txt; echo "=== restore: deploy --env qa ($(ts)) ==="; npx wrangler deploy --env qa > $D/restore.log 2>&1; echo "    exit=$?"; grep -E 'DRY_RUN' $D/restore.log | sed 's/^/    /'
RV=$(grep -oE 'Current Version ID: [0-9a-f-]+' $D/restore.log | awk '{print $4}'); echo "$RV" > $D/restorever.txt; echo "    restored version: $RV"; [ -n "$RV" ] || { echo "RESTORE_DEPLOY_FAILED"; exit 1; }
if [ "$($D/strictgate.sh "$RV" true 600 $(date +%s) restore | tee -a $D/restoregate.log | tail -1)" = PASSED ]; then cat $D/restoregate.log | grep -v '^PASSED$'; echo "RESTORE GATE PASSED ($(ts))"; echo PASSED > $D/restoregate.txt; else cat $D/restoregate.log; echo "RESTORE GATE NOT MET ($(ts))"; echo FAILED > $D/restoregate.txt; fi
