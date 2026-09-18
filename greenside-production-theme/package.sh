#!/usr/bin/env bash
#
# Builds greenside-production-theme.zip for upload at
# Shopify admin → Online Store → Themes → Add theme → Upload zip file.
#
# Only the directories Shopify recognises go in. docs/, tests/ and this script
# stay in the repository: Shopify rejects archives containing unexpected
# top-level entries.

set -euo pipefail

cd "$(dirname "$0")"

NAME="greenside-production-theme.zip"
OUT="../${NAME}"

THEME_DIRS=(assets blocks config layout locales sections snippets templates)

for dir in "${THEME_DIRS[@]}"; do
  [ -d "$dir" ] || { echo "missing required directory: $dir" >&2; exit 1; }
done

rm -f "$OUT"

zip -r -q -X "$OUT" "${THEME_DIRS[@]}" \
  -x '*.DS_Store' \
  -x '__MACOSX/*' \
  -x '*/.*'

echo "Built ${NAME}"
echo "  size:  $(du -h "$OUT" | cut -f1)"
echo "  files: $(unzip -l "$OUT" | tail -1 | awk '{print $2}')"
echo
echo "Top-level entries (Shopify accepts only these):"
unzip -l "$OUT" | awk 'NR>3 && $4 != "" {split($4,a,"/"); print a[1]}' | sort -u | sed 's/^/  /'
