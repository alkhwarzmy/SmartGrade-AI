"""Server-side identity, tenant membership, sessions, entitlements and audit records.

No default accounts, no client-supplied roles, no passwords or raw tokens in SQLite.
Identity migrations are versioned independently from each tenant's exam database.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import sqlite3
import time
from uuid import uuid4

PASSWORD_ITERATIONS = 600_000
PLAN_LIMITS = {
    "pilot": {"users": 5, "exams": 100, "submissions": 1000, "storage_mb": 1024},
    "school": {"users": 25, "exams": 1000, "submissions": 25000, "storage_mb": 10240},
    "campus": {"users": 100, "exams": 5000, "submissions": 100000, "storage_mb": 51200},
}
PERMISSIONS = {
    "admin": {"read", "write", "approve", "delete", "users", "audit", "settings"},
    "teacher": {"read", "write", "approve"},
    "viewer": {"results"},
}


class AuthError(ValueError):
    pass


class PermissionDenied(AuthError):
    pass


class LoginRateLimited(AuthError):
    pass


class CapacityError(ValueError):
    pass


@dataclass(frozen=True)
class Actor:
    user_id: str
    organization_id: str
    organization_name: str
    name: str
    email: str
    role: str
    must_change_password: bool

    def can(self, permission):
        return permission in PERMISSIONS[self.role]


def hash_password(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 128:
        raise ValueError("كلمة المرور يجب أن تكون بين 12 و128 حرفًا")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password, encoded):
    try:
        algorithm, iterations, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256" or not 100_000 <= int(iterations) <= 2_000_000:
            return False
        if not isinstance(password, str) or len(password) > 128:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError, AttributeError):
        return False


# Cost-equivalent work for unknown accounts. A random salt is not an account or credential.
DUMMY_PASSWORD = "pbkdf2_sha256$600000$" + "00" * 16 + "$" + "00" * 32


def _email(value):
    value = str(value).strip().lower()
    if len(value) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        raise ValueError("البريد الإلكتروني غير صالح")
    return value


def _slug(value):
    value = str(value).strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,47}", value):
        raise ValueError("رمز المؤسسة: 3 إلى 48 حرفًا إنجليزيًا صغيرًا أو رقمًا أو شرطة")
    return value


def _name(value):
    value = str(value).strip()
    if not 1 <= len(value) <= 100:
        raise ValueError("الاسم مطلوب وبحد أقصى 100 حرف")
    return value


class AuthService:
    def __init__(self, root, *, clock=time.time, session_hours=8, idle_minutes=30):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        (self.root / "organizations").mkdir(exist_ok=True, mode=0o700)
        self.path = self.root / "identity.sqlite3"
        self.clock = clock
        self.session_seconds = max(300, min(int(session_hours) * 3600, 86400))
        self.idle_seconds = max(60, min(int(idle_minutes) * 60, self.session_seconds))
        self._migrate()

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=15000")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def _migrate(self):
        with self.connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 2:
                raise RuntimeError("قاعدة الهوية أحدث من إصدار التطبيق؛ لا يمكن خفضها")
            db.execute("PRAGMA journal_mode=WAL")
            if version == 0:
                db.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS organizations (
                    id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
                    plan TEXT NOT NULL CHECK(plan IN ('pilot','school','campus')),
                    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
                    renewal_until INTEGER, external_ai_allowed INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, organization_id TEXT NOT NULL REFERENCES organizations(id),
                    email TEXT NOT NULL, name TEXT NOT NULL, password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('admin','teacher','viewer')),
                    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
                    must_change_password INTEGER NOT NULL DEFAULT 1,
                    created_at INTEGER NOT NULL, UNIQUE(organization_id,email));
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, last_seen INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
                CREATE TABLE IF NOT EXISTS login_limits (
                    bucket TEXT PRIMARY KEY, failures INTEGER NOT NULL, window_start INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS audit_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, organization_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL, action TEXT NOT NULL, object_id TEXT NOT NULL,
                    created_at INTEGER NOT NULL, previous_hash TEXT NOT NULL, event_hash TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS audit_org ON audit_events(organization_id,sequence);
                CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_events
                    BEGIN SELECT RAISE(ABORT,'audit is append only'); END;
                CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_events
                    BEGIN SELECT RAISE(ABORT,'audit is append only'); END;
                PRAGMA user_version=1;
                COMMIT;
                """)
        with self.connection(write=True) as db:
            db.execute("CREATE TABLE IF NOT EXISTS audit_receipts(event_id TEXT PRIMARY KEY)")
            db.execute("PRAGMA user_version=2")
        self.path.chmod(0o600)

    def ready(self):
        with self.connection() as db:
            return (
                db.execute("PRAGMA user_version").fetchone()[0] == 2
                and db.execute("SELECT 1").fetchone()[0] == 1
            )

    def has_organizations(self):
        with self.connection() as db:
            return bool(db.execute("SELECT 1 FROM organizations LIMIT 1").fetchone())

    def _audit(self, db, organization_id, actor_id, action, object_id=""):
        last = db.execute(
            "SELECT event_hash FROM audit_events WHERE organization_id=? ORDER BY sequence DESC LIMIT 1",
            (organization_id,),
        ).fetchone()
        previous = last[0] if last else "0" * 64
        timestamp = int(self.clock())
        content = [organization_id, actor_id, action, str(object_id), timestamp, previous]
        digest = hashlib.sha256(json.dumps(content, separators=(",", ":")).encode()).hexdigest()
        db.execute(
            "INSERT INTO audit_events(organization_id,actor_id,action,object_id,created_at,previous_hash,event_hash) "
            "VALUES(?,?,?,?,?,?,?)",
            (*content[:-1], previous, digest),
        )

    def audit(self, actor, action, object_id="", *, event_id=None):
        with self.connection(write=True) as db:
            if event_id:
                inserted = db.execute("INSERT OR IGNORE INTO audit_receipts VALUES(?)", (event_id,))
                if not inserted.rowcount:
                    return
            self._audit(db, actor.organization_id, actor.user_id, action, object_id)

    def provision_organization(
        self, slug, name, admin_email, admin_name, password, *, plan="pilot", renewal_until=None
    ):
        """Operator-only Python/CLI entry point. Never exposed as an unauthenticated HTTP/UI action."""
        slug, name, email, admin_name = _slug(slug), _name(name), _email(admin_email), _name(admin_name)
        if plan not in PLAN_LIMITS or (renewal_until is not None and renewal_until <= self.clock()):
            raise ValueError("الخطة أو تاريخ الترخيص غير صالح")
        encoded = hash_password(password)
        oid, uid, timestamp = uuid4().hex, uuid4().hex, int(self.clock())
        with self.connection(write=True) as db:
            try:
                db.execute(
                    "INSERT INTO organizations(id,slug,name,plan,renewal_until,created_at) VALUES(?,?,?,?,?,?)",
                    (oid, slug, name, plan, renewal_until, timestamp),
                )
                db.execute(
                    "INSERT INTO users(id,organization_id,email,name,password_hash,role,must_change_password,created_at) "
                    "VALUES(?,?,?,?,?,'admin',0,?)",
                    (uid, oid, email, admin_name, encoded, timestamp),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("رمز المؤسسة مستخدم") from exc
            self._audit(db, oid, "operator", "organization.provisioned", oid)
        return oid

    def _active_organization(self, row):
        return row["org_active"] and (row["renewal_until"] is None or row["renewal_until"] > self.clock())

    def login(self, slug, email, password, *, client_id="local"):
        slug, email = str(slug).strip().lower()[:48], str(email).strip().lower()[:254]
        identifier = hashlib.sha256((slug + ":" + email).encode()).hexdigest()
        client = hashlib.sha256((slug + ":" + str(client_id)).encode()).hexdigest()
        buckets = [("user:" + identifier, 5), ("client:" + client, 30)]
        timestamp = int(self.clock())
        # Password verification is deliberately outside the write lock; recheck limits and account before commit.
        with self.connection() as db:
            user = db.execute(
                "SELECT u.*,o.name AS org_name,o.active AS org_active,o.renewal_until "
                "FROM users u JOIN organizations o ON o.id=u.organization_id "
                "WHERE o.slug=? AND u.email=?",
                (slug, email),
            ).fetchone()
            limited = any(
                (
                    r := db.execute(
                        "SELECT failures,window_start FROM login_limits WHERE bucket=?", (b,)
                    ).fetchone()
                )
                and timestamp - r["window_start"] < 900
                and r["failures"] >= maximum
                for b, maximum in buckets
            )
        valid = (
            verify_password(password, user["password_hash"] if user else DUMMY_PASSWORD)
            if not limited
            else False
        )
        error = None
        token = None
        with self.connection(write=True) as db:
            if limited:
                error = LoginRateLimited("محاولات كثيرة؛ انتظر 15 دقيقة ثم أعد المحاولة")
            elif not user or not valid or not user["active"] or not self._active_organization(user):
                for bucket, _ in buckets:
                    db.execute(
                        "INSERT INTO login_limits(bucket,failures,window_start) VALUES(?,1,?) "
                        "ON CONFLICT(bucket) DO UPDATE SET failures=CASE WHEN ?-window_start>=900 THEN 1 ELSE failures+1 END, "
                        "window_start=CASE WHEN ?-window_start>=900 THEN ? ELSE window_start END",
                        (bucket, timestamp, timestamp, timestamp, timestamp),
                    )
                error = AuthError("تعذر تسجيل الدخول؛ تحقق من بياناتك وحالة المؤسسة")
            else:
                fresh = db.execute(
                    "SELECT password_hash,active,role FROM users WHERE id=?", (user["id"],)
                ).fetchone()
                organization = db.execute(
                    "SELECT active AS org_active,renewal_until FROM organizations WHERE id=?",
                    (user["organization_id"],),
                ).fetchone()
                if (
                    not fresh["active"]
                    or fresh["password_hash"] != user["password_hash"]
                    or not self._active_organization(organization)
                ):
                    error = AuthError("تعذر تسجيل الدخول؛ حاول مجددًا")
                else:
                    # Recheck lockouts set by concurrent requests.
                    for bucket, maximum in buckets:
                        row = db.execute(
                            "SELECT failures,window_start FROM login_limits WHERE bucket=?", (bucket,)
                        ).fetchone()
                        if row and timestamp - row["window_start"] < 900 and row["failures"] >= maximum:
                            error = LoginRateLimited("محاولات كثيرة؛ انتظر 15 دقيقة ثم أعد المحاولة")
                    if error is None:
                        token = secrets.token_urlsafe(32)
                        db.execute(
                            "INSERT INTO sessions VALUES(?,?,?,?,?)",
                            (
                                hashlib.sha256(token.encode()).hexdigest(),
                                user["id"],
                                timestamp,
                                timestamp + self.session_seconds,
                                timestamp,
                            ),
                        )
                        db.execute("DELETE FROM login_limits WHERE bucket=?", ("user:" + identifier,))
                        db.execute(
                            "DELETE FROM sessions WHERE expires_at<=? OR last_seen<=?",
                            (timestamp, timestamp - self.idle_seconds),
                        )
                        self._audit(db, user["organization_id"], user["id"], "auth.login")
        if error:
            raise error
        return token

    def actor(self, token, *, permission=None, allow_password_change=False):
        if not isinstance(token, str) or not 32 <= len(token) <= 128:
            raise AuthError("يلزم تسجيل الدخول")
        digest = hashlib.sha256(token.encode()).hexdigest()
        timestamp = int(self.clock())
        with self.connection() as db:
            row = db.execute(
                "SELECT s.*,u.organization_id,u.name,u.email,u.role,u.active,u.must_change_password,"
                "o.name AS org_name,o.active AS org_active,o.renewal_until FROM sessions s "
                "JOIN users u ON u.id=s.user_id JOIN organizations o ON o.id=u.organization_id "
                "WHERE s.token_hash=?",
                (digest,),
            ).fetchone()
            if (
                not row
                or not row["active"]
                or not self._active_organization(row)
                or row["expires_at"] <= timestamp
                or row["last_seen"] + self.idle_seconds <= timestamp
            ):
                raise AuthError("انتهت الجلسة أو أُلغيت؛ سجّل الدخول مجددًا")
        # Verify membership on every request, but avoid serializing all readers behind an idle-clock write.
        if timestamp - row["last_seen"] >= min(30, self.idle_seconds // 2):
            with self.connection(write=True) as db:
                db.execute(
                    "UPDATE sessions SET last_seen=? WHERE token_hash=? AND last_seen<?",
                    (timestamp, digest, timestamp),
                )
        actor = Actor(
            row["user_id"],
            row["organization_id"],
            row["org_name"],
            row["name"],
            row["email"],
            row["role"],
            bool(row["must_change_password"]),
        )
        if actor.must_change_password and not allow_password_change:
            raise PermissionDenied("غيّر كلمة المرور المؤقتة قبل استخدام مساحة العمل")
        if permission and not actor.can(permission):
            raise PermissionDenied("ليست لديك صلاحية لهذا الإجراء")
        return actor

    def logout(self, token):
        digest = hashlib.sha256(str(token).encode()).hexdigest()
        with self.connection(write=True) as db:
            row = db.execute(
                "SELECT u.organization_id,u.id FROM sessions s JOIN users u ON u.id=s.user_id "
                "WHERE token_hash=?",
                (digest,),
            ).fetchone()
            db.execute("DELETE FROM sessions WHERE token_hash=?", (digest,))
            if row:
                self._audit(db, row["organization_id"], row["id"], "auth.logout")

    def change_password(self, token, current_password, new_password):
        actor = self.actor(token, allow_password_change=True)
        encoded = hash_password(new_password)
        with self.connection(write=True) as db:
            old = db.execute("SELECT password_hash FROM users WHERE id=?", (actor.user_id,)).fetchone()[0]
            if not verify_password(current_password, old):
                raise AuthError("كلمة المرور الحالية غير صحيحة")
            if verify_password(new_password, old):
                raise ValueError("اختر كلمة مرور جديدة مختلفة")
            db.execute(
                "UPDATE users SET password_hash=?,must_change_password=0 WHERE id=?", (encoded, actor.user_id)
            )
            db.execute("DELETE FROM sessions WHERE user_id=?", (actor.user_id,))
            self._audit(db, actor.organization_id, actor.user_id, "auth.password_changed")

    def organization(self, token):
        actor = self.actor(token)
        with self.connection() as db:
            row = dict(
                db.execute("SELECT * FROM organizations WHERE id=?", (actor.organization_id,)).fetchone()
            )
        row["limits"] = dict(PLAN_LIMITS[row["plan"]])
        return row

    def list_users(self, token):
        actor = self.actor(token, permission="users")
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT id,email,name,role,active,must_change_password FROM users "
                    "WHERE organization_id=? ORDER BY created_at",
                    (actor.organization_id,),
                )
            ]

    def create_user(self, token, email, name, password, role):
        actor = self.actor(token, permission="users")
        if role not in PERMISSIONS:
            raise ValueError("دور غير صالح")
        email, name, encoded = _email(email), _name(name), hash_password(password)
        with self.connection(write=True) as db:
            plan = db.execute(
                "SELECT plan FROM organizations WHERE id=?", (actor.organization_id,)
            ).fetchone()[0]
            count = db.execute(
                "SELECT count(*) FROM users WHERE organization_id=? AND active=1", (actor.organization_id,)
            ).fetchone()[0]
            if count >= PLAN_LIMITS[plan]["users"]:
                raise CapacityError("وصلت المؤسسة إلى الحد الأقصى للمستخدمين النشطين")
            uid = uuid4().hex
            try:
                db.execute(
                    "INSERT INTO users(id,organization_id,email,name,password_hash,role,created_at) VALUES(?,?,?,?,?,?,?)",
                    (uid, actor.organization_id, email, name, encoded, role, int(self.clock())),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("البريد مستخدم داخل المؤسسة") from exc
            self._audit(db, actor.organization_id, actor.user_id, "user.created", uid)
            return uid

    def update_user(self, token, user_id, *, role, active):
        actor = self.actor(token, permission="users")
        if role not in PERMISSIONS or not isinstance(active, bool):
            raise ValueError("بيانات الدور غير صالحة")
        with self.connection(write=True) as db:
            row = db.execute(
                "SELECT * FROM users WHERE id=? AND organization_id=?", (user_id, actor.organization_id)
            ).fetchone()
            if not row:
                raise ValueError("المستخدم غير موجود")
            if row["id"] == actor.user_id and (not active or role != "admin"):
                raise ValueError("لا يمكنك تعطيل حسابك الإداري أو خفض صلاحياته بنفسك")
            if row["role"] == "admin" and row["active"] and (role != "admin" or not active):
                admins = db.execute(
                    "SELECT count(*) FROM users WHERE organization_id=? AND role='admin' AND active=1",
                    (actor.organization_id,),
                ).fetchone()[0]
                if admins <= 1:
                    raise ValueError("يجب الإبقاء على مدير نشط واحد على الأقل")
            if active and not row["active"]:
                plan = db.execute(
                    "SELECT plan FROM organizations WHERE id=?", (actor.organization_id,)
                ).fetchone()[0]
                count = db.execute(
                    "SELECT count(*) FROM users WHERE organization_id=? AND active=1",
                    (actor.organization_id,),
                ).fetchone()[0]
                if count >= PLAN_LIMITS[plan]["users"]:
                    raise CapacityError("حد المستخدمين النشطين ممتلئ")
            db.execute("UPDATE users SET role=?,active=? WHERE id=?", (role, int(active), user_id))
            db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            self._audit(db, actor.organization_id, actor.user_id, "user.updated_sessions_revoked", user_id)

    def reset_password(self, token, user_id, temporary_password):
        actor = self.actor(token, permission="users")
        encoded = hash_password(temporary_password)
        with self.connection(write=True) as db:
            row = db.execute(
                "SELECT id FROM users WHERE id=? AND organization_id=?", (user_id, actor.organization_id)
            ).fetchone()
            if not row:
                raise ValueError("المستخدم غير موجود")
            db.execute(
                "UPDATE users SET password_hash=?,must_change_password=1 WHERE id=?", (encoded, user_id)
            )
            db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            self._audit(
                db, actor.organization_id, actor.user_id, "user.password_reset_sessions_revoked", user_id
            )

    def set_ai_policy(self, token, enabled):
        actor = self.actor(token, permission="settings")
        if not isinstance(enabled, bool):
            raise ValueError("قيمة سياسة غير صالحة")
        with self.connection(write=True) as db:
            db.execute(
                "UPDATE organizations SET external_ai_allowed=? WHERE id=?",
                (int(enabled), actor.organization_id),
            )
            self._audit(db, actor.organization_id, actor.user_id, "organization.ai_policy_changed")

    def set_license(self, slug, plan, *, active=True, renewal_until=None):
        """Operator CLI only. This is manual contract provisioning, not a payment integration."""
        if plan not in PLAN_LIMITS:
            raise ValueError("خطة غير صالحة")
        with self.connection(write=True) as db:
            row = db.execute("SELECT id FROM organizations WHERE slug=?", (_slug(slug),)).fetchone()
            if not row:
                raise ValueError("المؤسسة غير موجودة")
            db.execute(
                "UPDATE organizations SET plan=?,active=?,renewal_until=? WHERE id=?",
                (plan, int(active), renewal_until, row[0]),
            )
            self._audit(db, row[0], "operator", "organization.license_changed")

    def audit_events(self, token, limit=200):
        actor = self.actor(token, permission="audit")
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM audit_events WHERE organization_id=? ORDER BY sequence DESC LIMIT ?",
                    (actor.organization_id, min(max(limit, 1), 1000)),
                )
            ]

    def verify_audit(self, token):
        actor = self.actor(token, permission="audit")
        previous = "0" * 64
        with self.connection() as db:
            for row in db.execute(
                "SELECT * FROM audit_events WHERE organization_id=? ORDER BY sequence",
                (actor.organization_id,),
            ):
                content = [
                    row["organization_id"],
                    row["actor_id"],
                    row["action"],
                    row["object_id"],
                    row["created_at"],
                    previous,
                ]
                expected = hashlib.sha256(json.dumps(content, separators=(",", ":")).encode()).hexdigest()
                if row["previous_hash"] != previous or row["event_hash"] != expected:
                    return False
                previous = row["event_hash"]
        return True

    def revoke_organization_sessions(self, slug):
        """Operator-only recovery action, including after restoring identity snapshots."""
        with self.connection(write=True) as db:
            row = db.execute("SELECT id FROM organizations WHERE slug=?", (_slug(slug),)).fetchone()
            if not row:
                raise ValueError("المؤسسة غير موجودة")
            result = db.execute(
                "DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE organization_id=?)",
                (row[0],),
            )
            self._audit(db, row[0], "operator", "organization.sessions_revoked")
            return result.rowcount

    def recover_admin(self, slug, email, temporary_password):
        """Operator-only, for an EXISTING administrator; does not alter a suspended organization/license."""
        slug, email = _slug(slug), _email(email)
        encoded = hash_password(temporary_password)
        with self.connection(write=True) as db:
            row = db.execute(
                "SELECT u.id,u.organization_id FROM users u JOIN organizations o ON o.id=u.organization_id WHERE o.slug=? AND u.email=? AND u.role='admin'",
                (slug, email),
            ).fetchone()
            if not row:
                raise ValueError("حساب مدير مطابق غير موجود")
            db.execute(
                "UPDATE users SET password_hash=?,active=1,must_change_password=1 WHERE id=?",
                (encoded, row["id"]),
            )
            db.execute("DELETE FROM sessions WHERE user_id=?", (row["id"],))
            identifier = hashlib.sha256((slug + ":" + email).encode()).hexdigest()
            db.execute("DELETE FROM login_limits WHERE bucket=?", ("user:" + identifier,))
            self._audit(db, row["organization_id"], "operator", "admin.recovered_sessions_revoked", row["id"])
