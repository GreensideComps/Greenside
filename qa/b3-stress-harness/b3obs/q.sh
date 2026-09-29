#!/bin/bash
# Validation-only helper (read-only): q.sh NAME 'JSON-BODY'  -> saves $O/NAME.json, prints http code + summary
O=$(dirname "$0"); curl -sS -X POST -o $O/$1.json -w "%{http_code}" -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" -H "Content-Type: application/json" \
  "https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/workers/observability/telemetry/query" --data "$2"
echo " $(jq -c '{success, err:[.errors[]?|.message], count:.result.events.count, returned:(.result.events.events|length?)}' $O/$1.json 2>/dev/null)"
