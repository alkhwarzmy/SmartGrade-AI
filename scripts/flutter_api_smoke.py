"""Exercise the actual Dart HTTP client against a disposable real FastAPI server."""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
from modules.auth import AuthService
from modules.mcq_grader import template
from modules.preprocessing import png_bytes

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix="smartgrade-dart-api-") as temporary:
        root = Path(temporary)
        service = AuthService(root / "data")
        password, viewer_password = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        service.provision_organization("smoke-one", "اختبار", "admin@one.test", "مدير", password)
        service.provision_organization("smoke-two", "اختبار ثان", "admin@two.test", "مدير", password)
        token = service.login("smoke-one", "admin@one.test", password)
        service.create_user(token, "viewer@one.test", "مطلع", viewer_password, "viewer")
        service.logout(token)
        image = root / "synthetic.png"
        image.write_bytes(png_bytes(template({i: ["A"] for i in range(20)}, True)))
        image50 = root / "synthetic50.png"
        image50.write_bytes(png_bytes(template({i: ["A"] for i in range(50)}, True, question_count=50)))
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        address = f"http://127.0.0.1:{port}"
        env = {**os.environ, "SMARTGRADE_DATA_DIR": str(root / "data"),
               "ALLOW_PAID_API": "false", "ALLOW_EXTERNAL_AI": "false"}
        with (root / "server.log").open("w") as log:
            server = subprocess.Popen([sys.executable, "-m", "uvicorn", "api:app",
                                       "--host", "127.0.0.1", "--port", str(port)], cwd=ROOT,
                                      env=env, stdout=log, stderr=log)
            try:
                for _ in range(100):
                    if server.poll() is not None:
                        raise RuntimeError("Temporary API server failed to start")
                    try:
                        with urlopen(address + "/health", timeout=1) as response:
                            if response.status == 200:
                                break
                    except OSError:
                        time.sleep(0.1)
                else:
                    raise RuntimeError("API startup timed out")
                subprocess.run(["dart", "run", "tool/api_smoke.dart"], cwd=ROOT / "flutter_app",
                               input=json.dumps({"server": address, "image": str(image), "image50": str(image50), "password": password,
                                                 "viewer_password": viewer_password}), text=True,
                               check=True, timeout=180)
            finally:
                server.terminate()
                try:
                    server.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()


if __name__ == "__main__":
    main()
