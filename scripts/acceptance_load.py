"""Bounded Docker acceptance: 1,000 synthetic papers, 20 users/4 tenants, 2 CPU and 2 GiB total."""

import json
from pathlib import Path
import subprocess
import sys
import re
import threading
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]

INNER = r"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4
from time import perf_counter
from contextlib import closing
import json,os,sys,time,shutil,subprocess,sqlite3,math
import httpx
from modules.auth import AuthService,AuthError
from modules.workspace import Workspace
from modules.schemas import ExamSpec
from modules.preprocessing import png_bytes
from modules.mcq_grader import template
from database.models import Submission,FileAsset,ReviewHistory,now
from modules.full_backup import create_key,backup_full,restore_full
root=Path('/var/lib/smartgrade/acceptance')
auth=AuthService(root)
password='SYNTHETIC-Load-Password!2026'
sessions=[]; expected={}; exams={}; organizations=[]
raw=png_bytes(template({i:['A'] for i in range(20)},True))
for number in range(4):
    slug=f'load-school-{number}'
    oid=auth.provision_organization(slug,'SYNTHETIC Load',f'admin@{number}.test','Admin',password)
    organizations.append(oid)
    admin=auth.login(slug,f'admin@{number}.test',password)
    space=Workspace(auth,admin)
    eid=space.create_exam(ExamSpec(title='SYNTHETIC Acceptance',kind='mcq',answer_key=['A']*20))
    sid=space.upload(eid,'SYNTHETIC-000',raw)
    space.approve_mcq(sid,['A']*20,'','',1,True)
    row=space.submission(sid); ids={sid}
    # Fixture seeding is intentionally NOT a throughput test of 1,000 uploads.
    with space._core.sessions.begin() as db:
        for item in range(1,250):
            sid=uuid4().hex; ids.add(sid); names=[]
            for old in (row.image_name,row.aligned_name):
                name=uuid4().hex+'.png'; path=space._core.images/name
                shutil.copyfile(space.image_path(old),path); names.append(name)
                db.add(FileAsset(name='images/'+name,size=path.stat().st_size))
            record=Submission(id=sid,exam_id=eid,student_code=f'SYNTHETIC-{item:03}',image_name=names[0],aligned_name=names[1],status='approved',original=row.original,current=row.current,approved=row.approved,total=20,revision=2)
            db.add(record)
            db.flush()
            db.add(ReviewHistory(submission_id=sid,action='synthetic_fixture',reviewer=auth.actor(admin).user_id,before={},after={'status':'approved'},reason='SYNTHETIC LOAD FIXTURE'))
    tokens=[admin]
    for person in range(4):
        email=f'teacher{person}@{number}.test'
        uid=auth.create_user(admin,email,'SYNTHETIC Teacher',password,'teacher')
        # Bypass first-change only in seeded synthetic accounts, outside the timed workloads.
        with auth.connection(write=True) as db:
            db.execute('UPDATE users SET must_change_password=0 WHERE id=?',(uid,))
        tokens.append(auth.login(slug,email,password))
    for token in tokens:
        sessions.append((token,eid,oid)); expected[oid]=ids; exams[oid]=eid
env={**os.environ,'SMARTGRADE_DATA_DIR':str(root),'ALLOW_EXTERNAL_AI':'false','ALLOW_PAID_API':'false'}
log=open('/var/lib/smartgrade/acceptance-server.log','w')
api=subprocess.Popen([sys.executable,'-m','uvicorn','api:app','--host','127.0.0.1','--port','8000','--workers','1','--no-access-log','--no-proxy-headers'],env=env,stdout=log,stderr=log)
try:
    for _ in range(150):
        try:
            if httpx.get('http://127.0.0.1:8000/ready',trust_env=False).status_code==200:break
        except httpx.HTTPError:pass
        time.sleep(.2)
    else:raise RuntimeError('startup failed')
    def workload(fixture):
        token,eid,oid=fixture; rows=[]
        with httpx.Client(base_url='http://127.0.0.1:8000',headers={'Authorization':'Bearer '+token},trust_env=False,timeout=15) as client:
            for item in range(25):
                started=perf_counter(); response=client.get(f'/exams/{eid}/submissions',params={'limit':50,'offset':item%5*50}); duration=perf_counter()-started
                correct=response.status_code==200 and len(response.json())==50 and all(row['id'] in expected[oid] for row in response.json())
                rows.append({'seconds':duration,'status':response.status_code,'scoped':correct})
        return rows
    with ThreadPoolExecutor(max_workers=20) as pool:
        results=[row for group in pool.map(workload,sessions) for row in group]
    latencies=sorted(row['seconds'] for row in results)
    errors=sum(row['status']!=200 for row in results); leaks=sum(not row['scoped'] for row in results)
    queued=[]
    for token,eid,oid in sessions[::5]:
        with httpx.Client(base_url='http://127.0.0.1:8000',headers={'Authorization':'Bearer '+token},trust_env=False) as client:
            response=client.post(f'/exams/{eid}/report-jobs',json={'format':'xlsx'}); assert response.status_code==202,response.text
            queued.append((token,response.json()['id']))
    subprocess.run([sys.executable,'-m','scripts.worker','--data-dir',str(root),'--once'],check=True,stdout=subprocess.DEVNULL,timeout=180)
    for token,jid in queued:
        with httpx.Client(base_url='http://127.0.0.1:8000',headers={'Authorization':'Bearer '+token},trust_env=False) as client:
            state=client.get(f'/jobs/{jid}').json(); assert state['state']=='succeeded',state
            output=client.get(f'/jobs/{jid}/result'); assert output.status_code==200 and output.content[:2]==b'PK'
finally:
    api.terminate();api.wait(timeout=15);log.close()
key=create_key(Path('/var/lib/smartgrade/key'))
started=perf_counter();snapshot=backup_full(root,Path('/var/lib/smartgrade/snapshot.sgb'),key,services_stopped=True);backup_seconds=perf_counter()-started
started=perf_counter();restored=restore_full(snapshot,Path('/var/lib/smartgrade/restored'),key);restore_seconds=perf_counter()-started
recovered=AuthService(restored)
with recovered.connection() as db:
    assert db.execute('SELECT count(*) FROM sessions').fetchone()[0]==0
papers=0
for oid in organizations:
    with closing(sqlite3.connect(restored/'organizations'/oid/'smartgrade.sqlite3')) as db:
        papers+=db.execute('SELECT count(*) FROM submissions WHERE total=20').fetchone()[0]
assert papers==1000
peak=int(Path('/sys/fs/cgroup/memory.peak').read_text());limit=int(Path('/sys/fs/cgroup/memory.max').read_text())
p95=latencies[math.ceil(len(latencies)*.95)-1]*1000
report={'source':'SYNTHETIC fixtures; 4 actual uploads plus 996 DB/file clones, NOT 1000-upload throughput',
'image_python':sys.version.split()[0],'papers':1000,'users':20,'independent_sessions':20,'organizations':4,'questions_per_paper':20,
'cpu_max':Path('/sys/fs/cgroup/cpu.max').read_text().strip(),'inner_cgroup_limit_bytes_unscoped':limit,'inner_cgroup_peak_bytes_unscoped':peak,
'paginated_read_requests':len(results),'concurrency':20,'errors':errors,'tenant_scope_violations':leaks,'p50_ms':round(latencies[len(latencies)//2]*1000,2),'p95_ms':round(p95,2),
'background_excel_jobs':4,'backup_seconds':round(backup_seconds,3),'restore_seconds':round(restore_seconds,3),'encrypted_snapshot_bytes':snapshot.stat().st_size,
'restored_approved_papers':papers,'restored_image_files':len(list(restored.rglob('*.png'))),'all_old_sessions_revoked':True,
'passed':errors==0 and leaks==0 and p95<1000}
print(json.dumps(report,indent=2),flush=True)
assert report['passed'],report
"""


def main():
    suffix = uuid4().hex[:10]
    container, volume = "smartgrade-acceptance-" + suffix, "smartgrade-acceptance-data-" + suffix
    subprocess.run(["docker", "volume", "create", volume], check=True, capture_output=True)
    try:
        command = [
            "docker",
            "run",
            "--rm",
            "-i",
            "--name",
            container,
            "--read-only",
            "--cpus=2",
            "--memory=2g",
            "--pids-limit=256",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--tmpfs",
            "/tmp:size=268435456,mode=1777",
            "-v",
            volume + ":/var/lib/smartgrade",
            "smartgrade:1.0.0-rc2",
            "python",
            "-",
        ]
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        result = {}

        def collect_output():
            result["stdout"], result["stderr"] = process.communicate(INNER)

        thread = threading.Thread(target=collect_output)
        thread.start()
        while process.poll() is None:
            probe = subprocess.run(
                ["docker", "inspect", container, "--format", "{{.HostConfig.Memory}}"],
                capture_output=True,
                text=True,
            )
            if probe.returncode == 0:
                configured_limit = int(probe.stdout.strip())
                break
            time.sleep(0.2)
        else:
            thread.join()
            raise RuntimeError(result.get("stderr", "Container did not start"))
        samples = []
        while thread.is_alive():
            sample = subprocess.run(
                ["docker", "stats", "--no-stream", "--format", "{{json .}}", container],
                capture_output=True,
                text=True,
            )
            if sample.returncode == 0 and sample.stdout.strip():
                samples.append(json.loads(sample.stdout.strip()))
            time.sleep(0.3)
        thread.join()
        if process.returncode:
            print(result["stdout"])
            print(result["stderr"], file=sys.stderr)
            raise SystemExit(process.returncode)
        report = json.loads(result["stdout"])

        def byte_value(text):
            match = re.fullmatch(r"([0-9.]+)([A-Za-z]+)", text.strip())
            units = {
                "B": 1,
                "KiB": 1024,
                "MiB": 1024**2,
                "GiB": 1024**3,
                "kB": 1000,
                "MB": 1000**2,
                "GB": 1000**3,
            }
            return round(float(match[1]) * units[match[2]])

        values = [
            (byte_value(sample["MemUsage"].split("/")[0]), byte_value(sample["MemUsage"].split("/")[1]))
            for sample in samples
        ]
        peak = max((usage for usage, limit in values), default=0)
        report.update(
            {
                "docker_configured_limit_bytes": configured_limit,
                "docker_reported_limit_bytes": max((limit for usage, limit in values), default=0),
                "docker_stats_observed_peak_bytes_unattributed": peak,
                "docker_stats_samples": len(values),
                "memory_sampling_note": "Docker CLI samples. If reported limit differs from requested limit, usage is NOT attributable to this application.",
            }
        )
        report["memory_acceptance_passed"] = (
            bool(values)
            and all(limit == 2 * 1024**3 for usage, limit in values)
            and peak < 0.8 * configured_limit
        )
        report["functional_and_latency_passed"] = report["passed"]
        report["memory_limit_verification"] = (
            "verified_samples" if report["memory_acceptance_passed"] else "unverified_environment_scope"
        )
        report["passed"] = report["passed"] and report["memory_acceptance_passed"]
        out = ROOT / "outputs/rc2-load.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        if not report["passed"]:
            raise SystemExit(2)
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)
        subprocess.run(["docker", "volume", "rm", volume], capture_output=True)


if __name__ == "__main__":
    main()
