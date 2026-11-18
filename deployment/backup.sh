#!/usr/bin/env bash
# Operator maintenance window: all three writers stop; an EXIT trap restarts them even on failure.
set -euo pipefail
: "${SMARTGRADE_BACKUP_DIR:?Set a directory writable by container UID 10001}"
: "${SMARTGRADE_BACKUP_KEY:?Set an existing 32-byte key file, mode 600, readable by UID 10001}"
SMARTGRADE_VOLUME="${SMARTGRADE_VOLUME:-smartgrade_smartgrade_data}"
SMARTGRADE_IMAGE="${SMARTGRADE_IMAGE:-smartgrade:1.0.0-rc2}"
backup_name="smartgrade-$(date -u +%Y%m%dT%H%M%SZ).sgb"
# Validate before stopping the application.
test -d "$SMARTGRADE_BACKUP_DIR"
test -f "$SMARTGRADE_BACKUP_KEY"
docker volume inspect "$SMARTGRADE_VOLUME" >/dev/null
trap 'docker compose start web api worker' EXIT
docker compose stop web api worker
docker run --rm --read-only --network none --cap-drop ALL --security-opt no-new-privileges \
  --tmpfs /tmp:size=268435456,mode=1777 \
  -v "$SMARTGRADE_VOLUME:/var/lib/smartgrade" \
  -v "$SMARTGRADE_BACKUP_DIR:/backups" \
  -v "$SMARTGRADE_BACKUP_KEY:/run/secrets/backup.key:ro" \
  "$SMARTGRADE_IMAGE" python -m scripts.manage backup-full --output "/backups/$backup_name" \
  --key-file /run/secrets/backup.key --services-stopped
