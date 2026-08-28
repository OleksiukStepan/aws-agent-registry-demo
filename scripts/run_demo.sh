#!/usr/bin/env bash
# End-to-end demo: sync IAM, provision, publish, discover. Pass --teardown to clean up.
set -euo pipefail

cd "$(dirname "$0")/.."
PYTHON=".venv/bin/python"

if [[ "${1:-}" == "--teardown" ]]; then
  echo "== removing records =="
  $PYTHON src/publish.py --teardown
  echo "== removing registry =="
  $PYTHON infra/registry.py --teardown
  exit 0
fi

echo "== syncing IAM policies =="
$PYTHON infra/iam_sync.py

echo; echo "== provisioning registry =="
$PYTHON infra/registry.py --create

echo; echo "== registry configuration =="
$PYTHON infra/registry.py --describe

echo; echo "== publishing records =="
$PYTHON src/publish.py

echo; echo "== consumer: browse =="
$PYTHON src/discover.py list

echo; echo "== consumer: semantic search =="
$PYTHON src/discover.py search "tool that can ship a parcel"

echo; echo "== governance check =="
$PYTHON -m pytest tests -q
