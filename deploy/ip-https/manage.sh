#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$project_dir"
exec docker compose -f compose.yaml -f compose.public-access.yaml -f compose.ip-https.yaml "$@"
