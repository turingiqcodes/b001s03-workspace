#!/usr/bin/env bash
# Export the tables the screens use to one JSON file per table (read-only: SELECT only).
# Connection: the usual PG* variables, with the password in ~/.pgpass.
#
#   svg/build/export_data.sh svg/build/data
set -euo pipefail

out=${1:?usage: export_data.sh <output dir>}
mkdir -p "$out"

export PGHOST=${PGHOST:-turingiq-training-rds-pubs-b001.cx0ywsig8do8.us-east-1.rds.amazonaws.com}
export PGDATABASE=${PGDATABASE:-pubs_b001s03}
export PGUSER=${PGUSER:-b001s03}
export PGSSLMODE=${PGSSLMODE:-require}

for t in authors titles titleauthor publishers pub_info employee jobs stores sales discounts roysched app_title_removals; do
  psql -X -A -t -v ON_ERROR_STOP=1 -c "SELECT coalesce(json_agg(x), '[]') FROM public.$t x;" > "$out/$t.json"
  echo "exported $t"
done
