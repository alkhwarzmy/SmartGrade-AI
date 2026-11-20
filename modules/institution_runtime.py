"""Start the authenticated application and its real worker without creating example accounts or data.

Useful in Colab and operator-managed local sessions. Hosting lifetime is supplied
by the caller; authentication does not make a Colab VM permanent.
"""

from dataclasses import dataclass
import os
from pathlib import Path
import signal
import subprocess
import time
from modules.auth import AuthService
from modules.colab_runtime import AppRuntime, launch_app


def _start_time(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (FileNotFoundError, ProcessLookupError):
        return None


def _group_has_live_members(group):
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            if int(fields[2]) == group and fields[0] != "Z":
                return True
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    return False


@dataclass
class InstitutionRuntime:
    web: AppRuntime
    worker: subprocess.Popen
    worker_start: str | None
    stopped: bool = False

    @property
    def process(self):
        return self.web.process

    def stop(self):
        if self.stopped:
            return
        self.web.stop()
        # Worker children inherit its dedicated process group. Stop them before a full snapshot.
        current_start = _start_time(self.worker.pid)
        if current_start is None or current_start == self.worker_start:
            try:
                os.killpg(self.worker.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            deadline = time.monotonic() + 10
            while _group_has_live_members(self.worker.pid) and time.monotonic() < deadline:
                time.sleep(0.05)
            if _group_has_live_members(self.worker.pid):
                try:
                    os.killpg(self.worker.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                deadline = time.monotonic() + 5
                while _group_has_live_members(self.worker.pid) and time.monotonic() < deadline:
                    time.sleep(0.05)
                if _group_has_live_members(self.worker.pid):
                    raise RuntimeError("عامل أو مهمة ما زالت تعمل؛ لا تبدأ النسخ قبل توقفها")
        try:
            self.worker.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(self.worker.pid, signal.SIGKILL)
            self.worker.wait(timeout=5)
        self.stopped = True


def launch_institution(
    project_dir, python, data_dir, *, port=8502, browser_address="", extra_env=None, timeout=45
):
    project_dir, data_dir = Path(project_dir).resolve(), Path(data_dir).resolve()
    if data_dir.is_relative_to(Path("/content/drive")):
        raise ValueError("احفظ قاعدة البيانات الحية محليًا؛ Drive للنسخ المشفرة فقط")
    auth = AuthService(data_dir)
    if not auth.has_organizations():
        raise ValueError("أنشئ المؤسسة وحساب المدير من خطوة الإعداد قبل تشغيل الواجهة")
    web = launch_app(
        project_dir,
        python,
        data_dir,
        port=port,
        browser_address=browser_address,
        extra_env=extra_env,
        timeout=timeout,
        mode="commercial",
    )
    logs = project_dir / ".colab"
    env = {
        **os.environ,
        "SMARTGRADE_DATA_DIR": str(data_dir),
        "SMARTGRADE_MODE": "commercial",
        "ALLOW_EXTERNAL_AI": "false",
        "ALLOW_PAID_API": "false",
    }
    env.pop("LLM_API_KEY", None)
    try:
        with (logs / "institution-worker.log").open("ab") as log:
            worker = subprocess.Popen(
                [str(Path(python).absolute()), "-m", "scripts.worker", "--data-dir", str(data_dir)],
                cwd=project_dir,
                env=env,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        runtime = InstitutionRuntime(web, worker, _start_time(worker.pid))
        started = time.time()
        deadline = time.monotonic() + timeout
        heartbeat = data_dir / "worker-heartbeat"
        while time.monotonic() < deadline:
            if worker.poll() is not None:
                raise RuntimeError("تعذر تشغيل عامل المهام؛ راجع سجل العامل أو تحقق من عدم تشغيل عامل آخر")
            if heartbeat.exists() and heartbeat.stat().st_mtime >= started:
                return runtime
            time.sleep(0.1)
        raise RuntimeError("انتهت مهلة تجهيز عامل المهام")
    except BaseException:
        if "runtime" in locals():
            runtime.stop()
        else:
            web.stop()
        raise
