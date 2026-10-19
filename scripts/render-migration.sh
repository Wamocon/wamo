#!/usr/bin/env bash
# Rendert eine Migration fuer ein Ziel-Schema.
# Nutzung: scripts/render-migration.sh wamo_dev [migration.sql] > out.sql
set -euo pipefail
schema="${1:?Schema angeben: wamo_dev | wamo_test | wamo_prod}"
file="${2:-supabase/migrations/0001_wamo_init.sql}"
case "$schema" in
  wamo_dev|wamo_test|wamo_prod) ;;
  *) echo "Unbekanntes Schema: $schema" >&2; exit 1 ;;
esac
sed "s/__SCHEMA__/${schema}/g" "$file"
