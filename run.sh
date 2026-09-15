#!/usr/bin/env bash
# Lance l'atelier Pliq sur le projet dbt local.
set -euo pipefail
cd "$(dirname "$0")"
exec .venv/bin/python -m pliq "$@"
