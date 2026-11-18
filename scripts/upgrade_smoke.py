"""Actual RC1 -> RC2 migration and snapshot rollback, with disposable synthetic data only."""

import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from modules.full_backup import create_key, backup_full, restore_full
from modules.auth import AuthService
from modules.workspace import Workspace

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "SYNTHETIC-Upgrade-Pass!2026"


def main():
    with tempfile.TemporaryDirectory(prefix="smartgrade-upgrade-") as temp:
        folder = Path(temp)
        old = folder / "rc1"
        old.mkdir()
        archive = subprocess.run(
            ["git", "archive", "40fe03940c42c696577c4866f8769349fdb0dca6"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        ).stdout
        with tarfile.open(fileobj=io.BytesIO(archive)) as files:
            files.extractall(old, filter="data")
        data = folder / "data"
        program = """
import json,sys
from pathlib import Path
from modules.auth import AuthService
from modules.workspace import Workspace
from modules.schemas import ExamSpec
from modules.mcq_grader import template
from modules.preprocessing import png_bytes
root=Path(sys.argv[1]); auth=AuthService(root)
auth.provision_organization('old-school','SYNTHETIC Old','admin@old.test','Admin',sys.argv[2])
token=auth.login('old-school','admin@old.test',sys.argv[2]); space=Workspace(auth,token)
eid=space.create_exam(ExamSpec(title='SYNTHETIC Legacy',kind='mcq',answer_key=['A']*20))
sid=space.upload(eid,'SYNTHETIC',png_bytes(template()))
space.approve_mcq(sid,['A']*20,'','',1,True)
(root/'fixture.json').write_text(json.dumps({'sid':sid,'token':token}))
"""
        # Synthetic password is passed through stdin, never a real credential or process argument.
        program = program.replace("sys.argv[2]", "password")
        program = "password=input()\n" + program
        subprocess.run(
            [sys.executable, "-c", program, str(data)],
            input=PASSWORD + "\n",
            text=True,
            cwd=old,
            check=True,
            capture_output=True,
        )
        fixture = json.loads((data / "fixture.json").read_text())
        key = create_key(folder / "backup.key")
        snapshot = backup_full(data, folder / "pre-upgrade.sgb", key, services_stopped=True)
        upgraded = AuthService(data)
        token = upgraded.login("old-school", "admin@old.test", PASSWORD)
        space = Workspace(upgraded, token)
        assert space.submission(fixture["sid"]).total == 20
        assert upgraded.verify_audit(token)
        rolled_back = restore_full(snapshot, folder / "rollback", key)
        with sqlite3.connect(rolled_back / "identity.sqlite3") as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 1
            assert db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
        verify = """
import sys,json
from modules.auth import AuthService
from modules.workspace import Workspace
password=input(); auth=AuthService(sys.argv[1]); token=auth.login('old-school','admin@old.test',password)
space=Workspace(auth,token); assert space.submissions()[0].total==20
assert auth.verify_audit(token)
print('RC1_ROLLBACK_VERIFIED')
"""
        result = subprocess.run(
            [sys.executable, "-c", verify, str(rolled_back)],
            input=PASSWORD + "\n",
            cwd=old,
            text=True,
            capture_output=True,
            check=True,
        )
        assert "RC1_ROLLBACK_VERIFIED" in result.stdout
        report = {
            "source": "SYNTHETIC disposable data",
            "actual_rc1_code_executed": True,
            "upgrade_grade_20_preserved": True,
            "image_preserved": space.image_path(space.submission(fixture["sid"]).image_name).exists(),
            "rollback_with_actual_rc1_code": True,
            "sessions_revoked_on_restore": True,
            "passed": True,
        }
        out = ROOT / "outputs/rc2-upgrade.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
