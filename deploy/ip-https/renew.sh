#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec 9>"$script_dir/renew.lock"
flock -n 9 || exit 0
bash "$script_dir/manage.sh" run --rm --no-deps certbot renew --quiet --non-interactive --no-random-sleep-on-renew
bash "$script_dir/manage.sh" exec -T nginx nginx -t
bash "$script_dir/manage.sh" exec -T nginx nginx -s reload
