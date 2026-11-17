"""Smoke-test the built commercial image with disposable data and no published ports."""

import argparse
import json
from pathlib import Path
import subprocess
from uuid import uuid4


INNER = r"""
import os
import time
from fastapi.testclient import TestClient
from api import create_app
from modules.auth import AuthService
from config import DATA_DIR
from modules.mcq_grader import template
from modules.preprocessing import png_bytes
print('SMOKE: imports ready', flush=True)
assert os.getuid() == 10001
assert not os.path.exists('/run/secrets/proxy_ca')
auth = AuthService(DATA_DIR)
password = 'Disposable-Docker-Smoke!2026'
auth.provision_organization('docker-test', 'مؤسسة اصطناعية', 'admin@docker.test', 'مدير تجريبي', password)
with TestClient(create_app(DATA_DIR)) as client:
    assert client.get('/ready').status_code == 200
    print('SMOKE: API ready', flush=True)
    assert client.get('/exams').status_code == 401
    response = client.post('/auth/login', json={'organization':'docker-test','email':'admin@docker.test','password':password})
    assert response.status_code == 200
    token = response.json()['access_token']
    headers = {'Authorization':'Bearer '+token}
    response = client.post('/exams', json={'title':'اختبار Docker اصطناعي','kind':'mcq','answer_key':['A']*20}, headers=headers)
    assert response.status_code == 201
    eid = response.json()['id']
    response = client.post('/exams/'+eid+'/submissions', data={'student_code':'SYNTHETIC-DOCKER'},
                           files={'file':('answer.png',png_bytes(template({i:['A'] for i in range(20)},True)),'image/png')}, headers=headers)
    assert response.status_code == 201
    sid = response.json()['id']
    response = client.post('/submissions/'+sid+'/approve-mcq', json={'answers':['A']*20,'revision':1,'reviewed':True}, headers=headers)
    assert response.status_code == 200 and response.json()['total'] == 20
    response = client.get('/exams/'+eid+'/report.pdf', headers=headers)
    assert response.status_code == 200 and response.content.startswith(b'%PDF')
    assert client.get('/admin/audit',headers=headers).json()['chain_valid']
print('SMOKE: HTTP pipeline passed', flush=True)
from streamlit.testing.v1 import AppTest
app = AppTest.from_file('/app/app.py',default_timeout=30).run()
assert not app.exception
next(t for t in app.text_input if t.label == 'رمز المؤسسة').set_value('docker-test')
next(t for t in app.text_input if t.label == 'البريد الإلكتروني').set_value('admin@docker.test')
next(t for t in app.text_input if t.label == 'كلمة المرور').set_value(password)
next(b for b in app.button if b.label == 'دخول إلى مساحة العمل').click().run()
assert not app.exception
app.radio(key='navigation').set_value('النتائج والتصدير').run()
assert not app.exception and app.dataframe
try:
    open('/app/unexpected-write','w')
except OSError:
    pass
else:
    raise AssertionError('Image root was writable')
print('DOCKER_SMOKE_PASSED: non-root, read-only, no build CA, authenticated ASGI MCQ 20/20, Arabic PDF, audit, UI login/results')
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="smartgrade:1.0.0-rc1")
    parser.add_argument("--socket", default="unix:///var/run/docker.sock")
    args = parser.parse_args()
    command = ["docker", "--host=" + args.socket]
    identifier = uuid4().hex[:12]
    container, volume = "smartgrade-smoke-" + identifier, "smartgrade-smoke-data-" + identifier
    report = {"image": args.image, "source": "disposable SYNTHETIC Docker data", "passed": False}
    env = __import__("os").environ.copy()
    for key in ["DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"]:
        env.pop(key, None)

    def run(extra, **kwargs):
        return subprocess.run(command + extra, check=True, env=env, **kwargs)

    try:
        run(["volume", "create", volume], capture_output=True)
        completed = run(
            [
                "run",
                "--rm",
                "-i",
                "--name",
                container,
                "--read-only",
                "--memory=2g",
                "--cpus=2",
                "--pids-limit=256",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--tmpfs",
                "/tmp:size=268435456,mode=1777",
                "-v",
                volume + ":/var/lib/smartgrade",
                args.image,
                "python",
                "-",
            ],
            input=INNER,
            text=True,
            capture_output=True,
        )
        if "DOCKER_SMOKE_PASSED" not in completed.stdout:
            raise RuntimeError("Smoke verification marker missing")
        run(
            [
                "run",
                "-d",
                "--name",
                container,
                "--read-only",
                "--memory=2g",
                "--cpus=2",
                "--pids-limit=256",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--tmpfs",
                "/tmp:size=268435456,mode=1777",
                "-v",
                volume + ":/var/lib/smartgrade",
                args.image,
                "python",
                "-m",
                "uvicorn",
                "api:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
                "--no-access-log",
            ],
            capture_output=True,
        )
        probe = """
import json, time
from urllib.request import urlopen, Request
for attempt in range(100):
    try:
        if urlopen('http://127.0.0.1:8000/ready', timeout=1).status == 200:
            break
    except OSError:
        time.sleep(.2)
else:
    raise RuntimeError('HTTP startup timeout')
payload=json.dumps({'organization':'docker-test','email':'admin@docker.test','password':'Disposable-Docker-Smoke!2026'}).encode()
request=Request('http://127.0.0.1:8000/auth/login',data=payload,headers={'Content-Type':'application/json'})
with urlopen(request,timeout=10) as response:
    token=json.load(response)['access_token']
request=Request('http://127.0.0.1:8000/exams',headers={'Authorization':'Bearer '+token})
with urlopen(request,timeout=10) as response:
    assert len(json.load(response)) == 1
print('HTTP_HEALTH_AUTH_OK')
"""
        checked = run(["exec", "-i", container, "python", "-"], input=probe, text=True, capture_output=True)
        assert "HTTP_HEALTH_AUTH_OK" in checked.stdout
        report["passed"] = True
        report["checks"] = [
            "uid=10001",
            "read-only root",
            "build CA absent",
            "unauthenticated API denied",
            "in-container ASGI login/upload/approval 20/20",
            "Arabic PDF",
            "valid audit chain",
            "container UI login/results",
            "real HTTP readiness/login/exams",
        ]
    except subprocess.CalledProcessError as exc:
        print((exc.stderr or "")[-4000:])
        if exc.stdout:
            print(exc.stdout[-4000:])
        raise
    finally:
        subprocess.run(command + ["rm", "-f", container], env=env, capture_output=True)
        subprocess.run(command + ["volume", "rm", volume], env=env, capture_output=True)
        target = Path("outputs/commercial-docker-report.json")
        target.parent.mkdir(exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
