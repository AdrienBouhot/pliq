#!/usr/bin/env bash
# Lance la suite de tests de Pliq.
#   ./test.sh                    tout, dbt compris
#   ./test.sh -m "not dbt"       la boucle rapide (aucun dbt lancé)
#   ./test.sh tests/test_recipes_execution.py -k join
set -euo pipefail
cd "$(dirname "$0")"
exec .venv/bin/python -m pytest "$@"
