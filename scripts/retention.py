"""Operator retention job. Dry run by default; caller owns scheduling and backup retention."""

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from config import DATA_DIR
from modules.auth import AuthService
from modules.review import Store
from modules.workspace import tenant_write_lock


def purge(root, slug, days, *, execute=False, now=None):
    if not 1 <= days <= 3650:
        raise ValueError("مدة الاحتفاظ يجب أن تكون بين يوم و3650 يومًا")
    auth = AuthService(root)
    with auth.connection() as db:
        org = db.execute("SELECT id FROM organizations WHERE slug=?", (slug,)).fetchone()
    if not org:
        raise ValueError("المؤسسة غير موجودة")
    data = auth.root / "organizations" / org[0]
    if not (data / "smartgrade.sqlite3").exists():
        return {"candidates": 0, "deleted": 0, "dry_run": not execute}
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    count = deleted = 0
    with tenant_write_lock(data):
        store = Store(data)
        for row in store.submissions():
            if datetime.fromisoformat(row.created_at) >= cutoff:
                continue
            count += 1
            if execute:
                with auth.connection(write=True) as db:
                    auth._audit(db, org[0], "operator", "retention.delete_requested", row.id)
                store.delete_submission(row.id, row.revision)
                with auth.connection(write=True) as db:
                    auth._audit(db, org[0], "operator", "retention.deleted", row.id)
                deleted += 1
    return {"candidates": count, "deleted": deleted, "dry_run": not execute}


def main():
    parser = argparse.ArgumentParser(
        description="Dry run by default. Deletes paper data, never users or identity audit."
    )
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--days", type=int, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    import json

    print(json.dumps(purge(args.data_dir, args.slug, args.days, execute=args.execute)))


if __name__ == "__main__":
    main()
