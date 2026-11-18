"""Isolated loopback HTTPS/WSS check with Caddy's test CA; not a public-domain acceptance test."""

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from uuid import uuid4
import httpx
from playwright.sync_api import sync_playwright, expect
from modules.auth import AuthService
from modules.mcq_grader import template
from modules.preprocessing import png_bytes

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--caddy", type=Path)
    args = parser.parse_args()
    out = ROOT / "outputs/rc2-staging"
    out.mkdir(parents=True, exist_ok=True)
    caddy = args.caddy
    if not caddy:
        caddy = ROOT / "work/caddy-rc2"
        container = "smartgrade-caddy-extract-" + uuid4().hex[:10]
        subprocess.run(
            ["docker", "create", "--name", container, "caddy:2.10"], check=True, capture_output=True
        )
        try:
            subprocess.run(
                ["docker", "cp", container + ":/usr/bin/caddy", str(caddy)], check=True, capture_output=True
            )
            caddy.chmod(0o700)
        finally:
            subprocess.run(["docker", "rm", container], check=True, capture_output=True)
    with tempfile.TemporaryDirectory(prefix="smartgrade-tls-") as temp, ExitStack() as stack:
        folder = Path(temp)
        data = folder / "data"
        auth = AuthService(data)
        password = "SYNTHETIC-TLS-Password!2026"
        auth.provision_organization("tls-school", "SYNTHETIC TLS", "admin@tls.test", "Admin", password)
        web, api, tls = free_port(), free_port(), free_port()
        env = {
            **os.environ,
            "SMARTGRADE_DATA_DIR": str(data),
            "SMARTGRADE_MODE": "commercial",
            "ALLOW_EXTERNAL_AI": "false",
            "ALLOW_PAID_API": "false",
            "SMARTGRADE_TRUSTED_PROXY_CIDRS": "127.0.0.1/32",
            "XDG_DATA_HOME": str(folder / "xdg-data"),
            "XDG_CONFIG_HOME": str(folder / "xdg-config"),
        }
        config = folder / "Caddyfile"
        config.write_text(f"""{{
    admin off
    auto_https disable_redirects
    skip_install_trust
}}
localhost:{tls} {{
    bind 127.0.0.1
    tls internal
    handle_path /api/* {{
        reverse_proxy 127.0.0.1:{api} {{
            header_up X-SmartGrade-Client-IP {{remote_host}}
        }}
    }}
    handle {{
        reverse_proxy 127.0.0.1:{web} {{
            header_up X-SmartGrade-Client-IP {{remote_host}}
        }}
    }}
}}
""")
        processes = []

        def stop(proc):
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()

        commands = [
            [
                sys.executable,
                "-m",
                "uvicorn",
                "api:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(api),
                "--no-proxy-headers",
                "--no-access-log",
            ],
            [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                "app.py",
                "--server.address",
                "127.0.0.1",
                "--server.port",
                str(web),
                "--server.fileWatcherType",
                "none",
            ],
            [sys.executable, "-m", "scripts.worker"],
            [str(caddy), "run", "--config", str(config), "--adapter", "caddyfile"],
        ]
        for index, command in enumerate(commands):
            log = stack.enter_context((out / f"process-{index}.log").open("w"))
            proc = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=log)
            processes.append(proc)
            stack.callback(stop, proc)
        certificate = folder / "xdg-data/caddy/pki/authorities/local/root.crt"
        deadline = time.monotonic() + 45
        while not certificate.exists():
            if any(p.poll() is not None for p in processes) or time.monotonic() > deadline:
                raise RuntimeError("Local staging failed; inspect output logs")
            time.sleep(0.2)
        context = ssl.create_default_context(cafile=str(certificate))
        url = f"https://localhost:{tls}"
        with httpx.Client(base_url=url, verify=context, trust_env=False, timeout=15) as client:
            while True:
                try:
                    if client.get("/api/ready").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if time.monotonic() > deadline:
                    raise RuntimeError("HTTPS readiness timeout")
                time.sleep(0.2)
            response = client.post(
                "/api/auth/login",
                json={"organization": "tls-school", "email": "admin@tls.test", "password": password},
            )
            assert response.status_code == 200, response.text
            client.headers["Authorization"] = "Bearer " + response.json()["access_token"]
            eid = client.post(
                "/api/exams", json={"title": "SYNTHETIC HTTPS", "kind": "mcq", "answer_key": ["A"] * 20}
            ).json()["id"]
            image = png_bytes(template({i: ["A"] for i in range(20)}, True))
            sid = client.post(
                f"/api/exams/{eid}/submissions",
                data={"student_code": "SYNTHETIC"},
                files={"file": ("sheet.png", image, "image/png")},
                headers={"Idempotency-Key": "https-once"},
            ).json()["id"]
            response = client.post(
                f"/api/submissions/{sid}/approve-mcq",
                json={"revision": 1, "answers": ["A"] * 20, "reviewed": True},
            )
            assert response.status_code == 200 and response.json()["total"] == 20
            jid = client.post(f"/api/exams/{eid}/report-jobs", json={"format": "xlsx"}).json()["id"]
            deadline = time.monotonic() + 45
            while True:
                status = client.get(f"/api/jobs/{jid}").json()
                if status["state"] in {"succeeded", "failed"}:
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError("Real child worker did not finish")
                time.sleep(0.25)
            assert status["state"] == "succeeded", status
            content = client.get(f"/api/jobs/{jid}/result")
            assert content.status_code == 200 and content.content.startswith(b"PK")
            assert client.get("/api/admin/audit").json()["chain_valid"]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True, executable_path="/usr/bin/chromium", args=["--no-sandbox"]
            )
            # The HTTPS API above verifies the private test CA and hostname. Browser uses a test-only CA exception.
            browser_context = browser.new_context(
                ignore_https_errors=True, viewport={"width": 1280, "height": 900}
            )
            page = browser_context.new_page()
            sockets = []
            page.on("websocket", lambda socket: sockets.append(socket.url))
            page.goto(url)
            page.get_by_label("رمز المؤسسة", exact=True).fill("tls-school")
            page.get_by_label("البريد الإلكتروني", exact=True).fill("admin@tls.test")
            page.get_by_label("كلمة المرور", exact=True).fill(password)
            page.get_by_label("كلمة المرور", exact=True).press("Enter")
            expect(page.get_by_label("عنوان الاختبار", exact=True)).to_be_visible(timeout=30000)
            assert any(s.startswith("wss://localhost:") for s in sockets)
            page.screenshot(path=str(out / "https-workspace.png"), full_page=True)
            browser.close()
        report = {
            "source": "isolated SYNTHETIC loopback staging only",
            "public_domain_verified": False,
            "https_api_ca_and_hostname_verified": True,
            "browser_private_ca_exception": True,
            "wss_ui_and_keyboard_login": True,
            "mcq_score": 20,
            "background_xlsx_real_child_process": True,
            "private_download_verified": True,
            "audit_chain_valid": True,
            "test_ca_sha256": hashlib.sha256(certificate.read_bytes()).hexdigest(),
            "passed": True,
        }
        (out / "report.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
