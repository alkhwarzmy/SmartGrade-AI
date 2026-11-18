"""Bounded loopback benchmark with real fixture uploads and a single Uvicorn worker."""

from pathlib import Path
import sys

ROOT = next(
    p
    for p in Path(__file__).resolve().parents
    if (p / "api.py").is_file() and (p / "pyproject.toml").is_file()
)
sys.path.insert(0, str(ROOT))


def run():
    import asyncio
    import json
    import math
    import os
    import platform
    import socket
    import subprocess
    import tempfile
    import time
    from urllib.request import urlopen
    import httpx
    from modules.auth import AuthService
    from modules.workspace import Workspace
    from modules.schemas import ExamSpec
    from modules.mcq_grader import template
    from modules.preprocessing import png_bytes

    OUT = ROOT / "work/audit-2026-11-18"
    OUT.mkdir(parents=True, exist_ok=True)
    PASSWORD = "SYNTHETIC-performance-only!2026"

    def memory(pid):
        fields = {}
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith(("VmRSS:", "VmHWM:")):
                name, value, *_ = line.split()
                fields[name.rstrip(":")] = round(int(value) / 1024, 2)
        return fields

    async def measure(base, headers, route, count, concurrency):
        gate = asyncio.Semaphore(concurrency)
        async with httpx.AsyncClient(
            base_url=base,
            headers=headers,
            timeout=45,
            limits=httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency),
        ) as client:

            async def one(_):
                async with gate:
                    started = time.perf_counter()
                    try:
                        response = await client.get(route)
                        return {
                            "seconds": time.perf_counter() - started,
                            "status": response.status_code,
                            "bytes": len(response.content),
                        }
                    except httpx.HTTPError as exc:
                        return {
                            "seconds": time.perf_counter() - started,
                            "status": type(exc).__name__,
                            "bytes": 0,
                        }

            rows = await asyncio.gather(*(one(i) for i in range(count)))
        latencies = sorted(r["seconds"] for r in rows)
        return {
            "requests": count,
            "concurrency": concurrency,
            "statuses": {str(s): sum(r["status"] == s for r in rows) for s in set(r["status"] for r in rows)},
            "p50_ms": round(latencies[math.ceil(count * 0.5) - 1] * 1000, 2),
            "p95_ms": round(latencies[math.ceil(count * 0.95) - 1] * 1000, 2),
            "max_ms": round(max(latencies) * 1000, 2),
            "response_bytes": rows[0]["bytes"],
        }

    with tempfile.TemporaryDirectory(prefix="smartgrade-load-audit-") as temporary:
        root = Path(temporary)
        auth = AuthService(root)
        auth.provision_organization("audit-school", "SYNTHETIC Load", "admin@load.test", "Audit", PASSWORD)
        token = auth.login("audit-school", "admin@load.test", PASSWORD)
        space = Workspace(auth, token)
        eid = space.create_exam(
            ExamSpec(title="SYNTHETIC 50-paper fixture", kind="mcq", answer_key=list("ABCD" * 5))
        )
        raw = png_bytes(template({i: ["ABCD"[i % 4]] for i in range(20)}, True))
        for i in range(50):
            sid = space.upload(eid, f"SYNTHETIC-{i:03}", raw)
            space.approve_mcq(
                sid, list("ABCD" * 5), "ignored-client-reviewer", "synthetic benchmark", 1, True
            )
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        env = {
            **os.environ,
            "SMARTGRADE_DATA_DIR": str(root),
            "SMARTGRADE_MODE": "commercial",
            "ALLOW_EXTERNAL_AI": "false",
            "ALLOW_PAID_API": "false",
        }
        with (OUT / "performance-server.log").open("w") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "api:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--workers",
                    "1",
                    "--no-access-log",
                ],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=log,
            )
            try:
                base = f"http://127.0.0.1:{port}"
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    try:
                        if urlopen(base + "/ready", timeout=1).status == 200:
                            break
                    except OSError:
                        time.sleep(0.15)
                else:
                    raise RuntimeError("Benchmark startup failed")
                headers = {"Authorization": "Bearer " + token}
                report = {
                    "source_commit": "40fe039",
                    "dataset": "SYNTHETIC only: 1 institution, 1 exam, 50 approved papers, 20 questions each",
                    "scope": "loopback HTTP; no TLS/network delay, no OCR/LLM calls, not a production capacity certification",
                    "python": platform.python_version(),
                    "workers": 1,
                    "docker_resource_limits_applied": False,
                    "host_reported_cpu_count": os.cpu_count(),
                    "rss_before_mb": memory(process.pid),
                    "workloads": {},
                }
                report["workloads"]["list_exams"] = asyncio.run(measure(base, headers, "/exams", 100, 8))
                report["workloads"]["list_50_papers"] = asyncio.run(
                    measure(base, headers, f"/exams/{eid}/submissions", 100, 8)
                )
                report["workloads"]["excel_50_students_1000_detail_rows"] = asyncio.run(
                    measure(base, headers, f"/exams/{eid}/report.xlsx", 12, 3)
                )
                report["rss_after_mb"] = memory(process.pid)
                (OUT / "performance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
                print(json.dumps(report, ensure_ascii=False, indent=2))
            finally:
                process.terminate()
                process.wait(timeout=15)


if __name__ == "__main__":
    run()
