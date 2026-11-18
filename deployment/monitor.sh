#!/usr/bin/env bash
set -euo pipefail
# Nonzero exit means a storage, worker or pending-cleanup alert; connect this to the operator's alert service.
docker compose exec -T api python -m scripts.manage monitor
