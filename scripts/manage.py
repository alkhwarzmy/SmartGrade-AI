"""Operator CLI; credentials are read interactively or from a protected file, never CLI arguments."""

import argparse
from datetime import datetime
from zoneinfo import ZoneInfo
from getpass import getpass
from pathlib import Path
import json
from config import DATA_DIR
from modules.auth import AuthService, PLAN_LIMITS
from modules.colab_runtime import backup_data, restore_data
from modules.workspace import tenant_write_lock


def _expiry(value):
    if value is None:
        return None
    return int(
        datetime.strptime(value, "%Y-%m-%d")
        .replace(hour=23, minute=59, second=59, tzinfo=ZoneInfo("Asia/Riyadh"))
        .timestamp()
    )


def main():
    parser = argparse.ArgumentParser(description="SmartGrade operator tools — no web bootstrap endpoint")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    sub = parser.add_subparsers(dest="command", required=True)
    setup = sub.add_parser("init", help="Provision an organization and its first administrator")
    setup.add_argument("--slug", required=True)
    setup.add_argument("--name", required=True)
    setup.add_argument("--email", required=True)
    setup.add_argument("--admin-name", default="مدير المؤسسة")
    setup.add_argument("--password-file", type=Path)
    setup.add_argument("--plan", choices=PLAN_LIMITS, default="pilot")
    setup.add_argument("--until", help="Optional contract expiry at end of day in Asia/Riyadh, YYYY-MM-DD")
    license_cmd = sub.add_parser("license", help="Manual contract entitlement; does not charge a card")
    license_cmd.add_argument("--slug", required=True)
    license_cmd.add_argument("--plan", choices=PLAN_LIMITS, required=True)
    license_cmd.add_argument("--until")
    license_cmd.add_argument("--suspend", action="store_true")
    sub.add_parser(
        "check", help="Check identity schema and provisioned organizations without revealing accounts"
    )
    revoke = sub.add_parser(
        "revoke-sessions", help="Operator recovery: revoke every session for one organization"
    )
    revoke.add_argument("--slug", required=True)
    backup = sub.add_parser("backup-tenant")
    backup.add_argument("--slug", required=True)
    backup.add_argument("--output", type=Path, required=True)
    migrate = sub.add_parser(
        "import-legacy", help="Import an existing MVP into an EMPTY organization workspace"
    )
    migrate.add_argument("--slug", required=True)
    migrate.add_argument("--source", type=Path, required=True)
    keygen = sub.add_parser("backup-keygen")
    keygen.add_argument("--output", type=Path, required=True)
    full = sub.add_parser("backup-full")
    full.add_argument("--output", type=Path, required=True)
    full.add_argument("--key-file", type=Path, required=True)
    full.add_argument("--services-stopped", action="store_true")
    restore = sub.add_parser("restore-full")
    restore.add_argument("--archive", type=Path, required=True)
    restore.add_argument("--destination", type=Path, required=True)
    restore.add_argument("--key-file", type=Path, required=True)
    recovery = sub.add_parser("recover-admin")
    recovery.add_argument("--slug", required=True)
    recovery.add_argument("--email", required=True)
    recovery.add_argument("--password-file", type=Path)
    sub.add_parser("monitor")
    args = parser.parse_args()
    if args.command in {"backup-keygen", "backup-full", "restore-full"}:
        from modules.full_backup import create_key, backup_full, restore_full

        if args.command == "backup-keygen":
            create_key(args.output)
        elif args.command == "backup-full":
            backup_full(args.data_dir, args.output, args.key_file, services_stopped=args.services_stopped)
        else:
            restore_full(args.archive, args.destination, args.key_file)
        print("Completed. No credentials were printed.")
        return
    auth = AuthService(args.data_dir)
    if args.command == "monitor":
        from modules.operations import storage_ready, tenant_status
        from modules.review import Store

        with auth.connection() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM organizations")]
        report = {
            oid: tenant_status(Store(auth.root / "organizations" / oid))
            for oid in ids
            if (auth.root / "organizations" / oid / "smartgrade.sqlite3").exists()
        }
        ready = auth.ready() and storage_ready(auth.root)
        print(json.dumps({"ready": ready, "organizations": report}))
        if not ready or any(item["alerts"] for item in report.values()):
            raise SystemExit(1)
        return
    if args.command == "recover-admin":
        if args.password_file:
            if args.password_file.is_symlink() or args.password_file.stat().st_mode & 0o077:
                raise ValueError("ملف كلمة المرور يجب أن يكون خاصًا بالمالك")
            password = args.password_file.read_text().rstrip("\r\n")
        else:
            password = getpass("كلمة مرور بديلة مؤقتة للمدير: ")
            if password != getpass("تأكيد كلمة المرور: "):
                raise ValueError("كلمتا المرور غير متطابقتين")
        auth.recover_admin(args.slug, args.email, password)
        print("Administrator recovered; sessions revoked; password change required.")
        return
    if args.command == "init":
        if args.password_file:
            if args.password_file.stat().st_mode & 0o077:
                raise ValueError("ملف كلمة المرور يجب أن يكون مقروءًا للمالك فقط (chmod 600)")
            password = args.password_file.read_text().rstrip("\r\n")
        else:
            password = getpass("كلمة مرور المدير (12 حرفًا على الأقل): ")
            if password != getpass("تأكيد كلمة المرور: "):
                raise ValueError("كلمتا المرور غير متطابقتين")
        oid = auth.provision_organization(
            args.slug,
            args.name,
            args.email,
            args.admin_name,
            password,
            plan=args.plan,
            renewal_until=_expiry(args.until),
        )
        print(f"Organization provisioned: {args.slug} ({oid}). No password was printed.")
    elif args.command == "license":
        auth.set_license(args.slug, args.plan, active=not args.suspend, renewal_until=_expiry(args.until))
        print("Contract entitlement updated; no payment transaction performed.")
    elif args.command == "revoke-sessions":
        print(json.dumps({"revoked": auth.revoke_organization_sessions(args.slug)}))
    elif args.command == "check":
        print(
            json.dumps(
                {
                    "identity_schema": 2,
                    "ready": auth.ready(),
                    "organizations_configured": auth.has_organizations(),
                }
            )
        )
    else:
        with auth.connection() as db:
            org = db.execute("SELECT id FROM organizations WHERE slug=?", (args.slug,)).fetchone()
        if not org:
            raise ValueError("المؤسسة غير موجودة")
        root = auth.root / "organizations" / org[0]
        if args.command == "backup-tenant":
            with tenant_write_lock(root):
                backup_data(root, args.output, app_stopped=True)
            print("Tenant data snapshot created. Back up the identity database separately as documented.")
        else:
            # Keep the original source intact and refuse to overwrite an existing tenant database.
            if root.exists() and any(root.iterdir()):
                raise ValueError("مساحة المؤسسة ليست فارغة؛ لن تُستبدل بياناتها")
            import tempfile

            with tempfile.TemporaryDirectory() as directory:
                archive = backup_data(args.source, Path(directory) / "legacy.zip", app_stopped=True)
                restore_data(archive, root)
            print(
                "Legacy data copied into the organization. Original data retained; verify before retiring the old instance."
            )


if __name__ == "__main__":
    main()
