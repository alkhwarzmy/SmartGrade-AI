"""Build a reproducible, self-contained Colab notebook from an explicit source allowlist."""

import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import textwrap
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TOP_FILES = [
    "app.py",
    "config.py",
    "README.md",
    ".env.example",
    ".gitignore",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "requirements-browser.txt",
    "requirements.lock",
    ".streamlit/config.toml",
]
SOURCE_DIRS = ["modules", "database", "scripts", "tests", "docs", "assets"]


def source_files(root=ROOT):
    files = [root / name for name in TOP_FILES]
    for directory in SOURCE_DIRS:
        files.extend(
            p
            for p in (root / directory).rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
        )
    return sorted(files, key=lambda p: p.relative_to(root).as_posix())


def source_bundle(root=ROOT):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in source_files(root):
            name = path.relative_to(root).as_posix()
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    return out.getvalue()


def markdown(text):
    return {"cell_type": "markdown", "metadata": {}, "source": textwrap.dedent(text).strip() + "\n"}


def code(text):
    return {
        "cell_type": "code",
        "metadata": {"cellView": "form"},
        "source": textwrap.dedent(text).strip() + "\n",
        "execution_count": None,
        "outputs": [],
    }


def build_notebook(root=ROOT):
    payload = source_bundle(root)
    encoded = base64.b64encode(payload).decode("ascii")
    digest = hashlib.sha256(payload).hexdigest()
    cells = [
        markdown("""
        # SmartGrade AI — التشغيل على Google Colab
        نسخة ذاتية الاحتواء: كود المشروع ونموذج اللغة العربية مضمنان في الدفتر.
        من **Runtime → Change runtime type** اختر **Python 3 / CPU**، ثم **Run all**.
        لا تحتاج GPU أو GitHub token أو API key لتصحيح الاختيارات والمراجعة اليدوية.

        ستظهر واجهة عربية داخل الدفتر عبر وكيل Colab، دون ngrok أو رابط نشر عام.
        يبقى تشغيل الدفتر وربط Drive داخل حسابك؛ لم تُنفَّذ جلسة Colab حية أثناء إعداد هذه النسخة.

        **البيانات:** عند رفع صورك هنا تُعالج في بيئة Google Colab السحابية. استخدم بيانات مصرحًا بها.
        قرص `/content` مؤقت ويُحذف عند انتهاء الجلسة؛ احتفظ بنسخة ZIP قبل المغادرة.
        لا يتضمن الدفتر أي صور طلاب أو مفاتيح أو قواعد بيانات سابقة، ولا تُستدعى خدمات LLM تلقائيًا.
    """),
        code("""
        # @title 1. إعداد مسارات الجلسة
        import os
        import sys
        from pathlib import Path
        if sys.version_info < (3, 11):
            raise RuntimeError("يلزم Python 3.11 أو أحدث؛ اختر Runtime حديثًا")
        CONTENT_ROOT = Path(os.environ.get("SMARTGRADE_COLAB_ROOT", "/content")).absolute()
        PROJECT_DIR = CONTENT_ROOT / "SmartGrade-AI"
        DATA_DIR = CONTENT_ROOT / "smartgrade-data"
        VENV_DIR = CONTENT_ROOT / "smartgrade-venv"
        PYTHON = VENV_DIR / "bin" / "python"
        PORT = 8501
        AI_ENV = {}
        CONTENT_ROOT.mkdir(parents=True, exist_ok=True)
        print("مساحة المشروع:", PROJECT_DIR)
        print("بيانات الجلسة المؤقتة:", DATA_DIR)
    """),
        markdown("""
        ## 2. استخراج المشروع المضمن
        تتحقق الخلية من البصمة والمسارات. إعادة تشغيلها لا تستبدل ملفات عدلتها؛
        عند وجود اختلاف اختر مجلد مشروع جديدًا بدل حذف عملك.
    """),
    ]
    extraction = """
# @title 2. استخراج كود المشروع المضمن بأمان
import base64
import hashlib
import io
import stat
import zipfile
from pathlib import PurePosixPath
BUNDLE_SHA256 = "__DIGEST__"
BUNDLE_B64 = "__PAYLOAD__"
blob = base64.b64decode(BUNDLE_B64, validate=True)
if hashlib.sha256(blob).hexdigest() != BUNDLE_SHA256:
    raise ValueError("بصمة حزمة المشروع غير صحيحة")
if PROJECT_DIR.is_symlink():
    raise ValueError("مجلد المشروع لا يجوز أن يكون رابطًا رمزيًا")
with zipfile.ZipFile(io.BytesIO(blob)) as archive:
    infos = archive.infolist()
    if len(infos) > 1000 or sum(info.file_size for info in infos) > 50_000_000:
        raise ValueError("حزمة المشروع تتجاوز الحدود")
    seen = set()
    planned = []
    for info in infos:
        relative = PurePosixPath(info.filename)
        if (relative.is_absolute() or ".." in relative.parts or "\\\\" in info.filename
                or str(relative) != info.filename or info.is_dir()
                or stat.S_ISLNK(info.external_attr >> 16) or info.filename in seen):
            raise ValueError("مسار غير آمن في حزمة المشروع")
        seen.add(info.filename)
        target = PROJECT_DIR / info.filename
        if any(parent.is_symlink() for parent in [target, *target.parents]):
            raise ValueError("رابط رمزي غير مسموح في مسار المشروع")
        content = archive.read(info)
        if target.exists() and target.read_bytes() != content:
            raise FileExistsError("ملف موجود مختلف؛ اختر PROJECT_DIR جديدًا للحفاظ على تعديلاتك: " + info.filename)
        planned.append((target, content))
    for target, content in planned:
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))
print("تم تجهيز", len(planned), "ملفًا؛ لم تُستبدل تعديلات موجودة.")
"""
    cells.append(code(extraction.replace("__DIGEST__", digest).replace("__PAYLOAD__", encoded)))
    cells.extend(
        [
            markdown("""
        ## 3. تثبيت المتطلبات ومحرك OCR
        يُنشأ Python افتراضي مستقل لتجنب تغيير NumPy/Pandas في نواة Colab.
        يستخدم Python 3.12 ملف الإصدارات المقفلة المختبر؛ الإصدارات الأخرى تستخدم حدود المتطلبات.
        فشل تثبيت Tesseract لا يمنع MCQ أو إدخال النص يدويًا. يستغرق التثبيت عدة دقائق.
    """),
            code("""
        # @title تثبيت المتطلبات (بدون إعادة تشغيل النواة)
        import subprocess
        import shutil
        INSTALL_LOCAL_OCR = True # @param {type:"boolean"}
        if not PYTHON.exists():
            try:
                subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)], check=True)
            except subprocess.CalledProcessError:
                # بعض جلسات Colab لا تتضمن ensurepip: استخدم virtualenv في مجلد عزل مؤقت.
                bootstrap = CONTENT_ROOT / "smartgrade-bootstrap"
                subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--target",
                                str(bootstrap), "virtualenv>=20,<21"], check=True)
                subprocess.run([sys.executable, "-m", "virtualenv", str(VENV_DIR)],
                               env={**os.environ, "PYTHONPATH": str(bootstrap)}, check=True)
        requirement = "requirements.lock" if sys.version_info[:2] == (3, 12) else "requirements-dev.txt"
        subprocess.run([str(PYTHON), "-m", "pip", "install", "--quiet", "-r", str(PROJECT_DIR / requirement)],
                       cwd=PROJECT_DIR, check=True)
        if INSTALL_LOCAL_OCR:
            try:
                subprocess.run(["apt-get", "update", "-qq"], check=True, timeout=180)
                subprocess.run(["apt-get", "install", "-y", "-qq", "tesseract-ocr", "fonts-noto-core"],
                               check=True, timeout=180, env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"})
            except (OSError, subprocess.SubprocessError):
                print("لم يكتمل تثبيت OCR؛ يمكنك إدخال النص يدويًا. راجع المخرجات عند الحاجة.")
        print("Python المستقل جاهز. OCR:", "متاح" if shutil.which("tesseract") else "إدخال يدوي")
    """),
            markdown("""
        ## 4. استعادة بيانات سابقة — اختياري
        اترك الخيار مغلقًا في أول تشغيل. لاستعادة نسخة أنشأها هذا المشروع، فعّل الخيار واختر ZIP.
        الاستعادة تتطلب مجلد بيانات فارغًا والتطبيق متوقفًا؛ لن تُستبدل بيانات حالية.
        استخدم نسخة موثوقة تخصك، وليست ZIP من مصدر مجهول. لا تُحفظ SQLite مباشرة على Drive.
    """),
            code("""
        # @title استعادة نسخة ZIP موثوقة
        RESTORE_BACKUP = False # @param {type:"boolean"}
        if RESTORE_BACKUP:
            from google.colab import files
            from modules.colab_runtime import restore_data
            from uuid import uuid4
            previous = globals().get("_smartgrade_runtime")
            if previous is not None and previous.process.poll() is None:
                raise RuntimeError("أوقف التطبيق من الخلية الأخيرة قبل الاستعادة")
            if DATA_DIR.exists() and any(DATA_DIR.iterdir()):
                raise RuntimeError("بيانات حالية موجودة؛ اختر DATA_DIR جديدًا وفارغًا أولًا")
            uploaded = files.upload()
            if len(uploaded) != 1:
                raise ValueError("اختر ملف ZIP واحدًا")
            archive = CONTENT_ROOT / ("restore-" + uuid4().hex + ".zip")
            archive.write_bytes(next(iter(uploaded.values())))
            try:
                restore_data(archive, DATA_DIR)
                print("تمت الاستعادة إلى:", DATA_DIR)
            finally:
                archive.unlink(missing_ok=True)
        else:
            print("تخطي الاستعادة؛ بيانات الجلسة الحالية محفوظة.")
    """),
            markdown("""
        ## 5. اختبار التثبيت
        تجري الاختبارات على بيانات مؤقتة واصطناعية. نجاحها لا يثبت جودة خط اليد أو اتفاق درجات AI مع المعلم.
        الاختبار الحي لمزود LLM غير مشمول؛ اتصالات الاختبار محاكاة ولا توجد تكلفة API.
    """),
            code("""
        # @title تشغيل اختبارات المشروع
        RUN_TESTS = True # @param {type:"boolean"}
        if RUN_TESTS:
            subprocess.run([str(PYTHON), "-m", "pytest", "-q"], cwd=PROJECT_DIR, check=True,
                           env={**os.environ, "SMARTGRADE_DATA_DIR": str(CONTENT_ROOT / "smartgrade-test-data"),
                                "ALLOW_EXTERNAL_AI": "false", "ALLOW_PAID_API": "false"})
    """),
            markdown("""
        ## 6. إنشاء قالب الطباعة وعينات للتجربة
        بعد فتح التطبيق أنشئ MCQ بالمفتاح الافتراضي `A B C D` مكررًا خمس مرات.
        ارفع `synthetic-complete.png` من مجلد `outputs/demo` عبر لوحة ملفات Colab،
        أو نزّل العينة من صفحة «قالب الإجابة» في التطبيق. العينة اصطناعية وليست ورقة طالب.
    """),
            code("""
        # @title توليد قالب OMR وعينة اصطناعية
        from IPython.display import Image, display
        subprocess.run([str(PYTHON), "-m", "scripts.generate_omr", "--output", str(PROJECT_DIR / "outputs/demo")],
                       cwd=PROJECT_DIR, check=True)
        display(Image(filename=str(PROJECT_DIR / "outputs/demo/synthetic-complete.png"), width=420))
    """),
            markdown("""
        ## 7. إعداد LLM — اختياري ومعطّل افتراضيًا
        يمكنك التصحيح والمراجعة يدويًا دون هذه الخلية. لتفعيل المزود:
        أضف مفتاحًا باسم `SMARTGRADE_LLM_API_KEY` من لوحة **Secrets** في Colab واسمح لهذا الدفتر بقراءته.
        أدخل اسم النموذج، وفعّل الخيارين فقط بعد اعتماد إرسال النص والتكلفة. لا تكتب المفتاح داخل الكود.
        بعد تغيير الإعدادات أعد تشغيل خلية الواجهة. داخل التطبيق يلزم أيضًا تأكيد الإرسال لكل طلب.
    """),
            code("""
        # @title إعداد مزود المقالات الخارجي
        ENABLE_REMOTE_GRADING = False # @param {type:"boolean"}
        APPROVE_API_COST = False # @param {type:"boolean"}
        LLM_MODEL = "" # @param {type:"string"}
        LLM_BASE_URL = "https://api.openai.com/v1" # @param {type:"string"}
        AI_ENV = {}
        if ENABLE_REMOTE_GRADING:
            if not APPROVE_API_COST or not LLM_MODEL.strip():
                raise ValueError("يلزم اعتماد التكلفة وتحديد النموذج قبل التفعيل")
            from google.colab import userdata
            try:
                key = userdata.get("SMARTGRADE_LLM_API_KEY")
                if not key:
                    raise ValueError("missing")
            except Exception:
                raise RuntimeError("أضف المفتاح إلى Colab Secrets واسمح بقراءته") from None
            AI_ENV = {"LLM_API_KEY": key, "LLM_MODEL": LLM_MODEL.strip(), "LLM_BASE_URL": LLM_BASE_URL,
                      "ALLOW_EXTERNAL_AI": "true", "ALLOW_PAID_API": "true"}
            del key
            print("أُعد المزود دون إرسال أي طلب؛ أعد تشغيل الواجهة لتطبيق الإعدادات.")
        else:
            print("المزود الخارجي معطّل؛ المراجعة اليدوية متاحة.")
    """),
            markdown("""
        ## 8. تشغيل الواجهة العربية
        اترك جلسة Colab متصلة. تظهر الواجهة داخل الخلية باستخدام `serve_kernel_port_as_iframe` الرسمي.
        يحتفظ التشغيل بحماية CORS وXSRF، ويضبط عنوان وكيل الجلسة بدل تعطيلهما.
        إعادة تنفيذ هذه الخلية توقف خادمها السابق فقط ثم تعيد تشغيله بالبيانات نفسها.
    """),
            code("""
        # @title فتح SmartGrade داخل Colab
        from google.colab import output
        from urllib.parse import urlsplit
        from modules.colab_runtime import launch_app
        previous = globals().get("_smartgrade_runtime")
        if previous is not None:
            previous.stop()
        proxy_url = str(output.eval_js(f"google.colab.kernel.proxyPort({PORT})"))
        address = urlsplit(proxy_url).hostname
        if not address:
            raise RuntimeError("لم يُرجع وكيل Colab عنوانًا صالحًا؛ تحقق من اتصال الجلسة")
        _smartgrade_runtime = launch_app(PROJECT_DIR, PYTHON, DATA_DIR, port=PORT,
                                         browser_address=address, extra_env=AI_ENV)
        print("التطبيق جاهز. ابدأ بإنشاء اختبار ثم رفع ورقة ومراجعتها واعتمادها.")
        output.serve_kernel_port_as_iframe(PORT, height=1000, cache_in_notebook=False)
    """),
            markdown("""
        ## 9. حفظ البيانات قبل انتهاء الجلسة — اختياري
        غيّر `BACKUP_ACTION` إلى `download` لتنزيل ZIP على جهازك، أو `drive` لحفظها في Google Drive.
        قد تحتوي النسخة صور الطلاب ونصوصهم ودرجاتهم؛ احتفظ بها في مكان مصرح وآمن.
        يتوقف التطبيق أولًا لتكون الصور وقاعدة البيانات متسقة، وتُلتقط SQLite عبر Backup API مع محتوى WAL.
        لا تتضمن النسخة مفاتيح API أو سجلات التشغيل أو كود المشروع. لا تُرسل إلى Drive إلا باختيارك وربط حسابك.
        بعد الحفظ يمكنك إعادة تشغيل خلية الواجهة لمتابعة العمل.
    """),
            code("""
        # @title نسخ البيانات وتنزيلها أو حفظها في Drive
        BACKUP_ACTION = "none" # @param ["none", "download", "drive"]
        if BACKUP_ACTION != "none":
            from modules.colab_runtime import backup_data
            from datetime import datetime, timezone
            from uuid import uuid4
            previous = globals().get("_smartgrade_runtime")
            if previous is not None:
                previous.stop()
            name = "smartgrade-backup-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8] + ".zip"
            archive = backup_data(DATA_DIR, CONTENT_ROOT / name, app_stopped=True)
            if BACKUP_ACTION == "download":
                from google.colab import files
                files.download(str(archive))
            elif BACKUP_ACTION == "drive":
                from google.colab import drive
                drive.mount(str(CONTENT_ROOT / "drive"))
                folder = CONTENT_ROOT / "drive" / "MyDrive" / "SmartGrade-AI" / "backups"
                folder.mkdir(parents=True, exist_ok=True)
                target = folder / name
                with archive.open("rb") as source, target.open("xb") as destination:
                    shutil.copyfileobj(source, destination)
                print("حُفظت نسخة ZIP في مجلد SmartGrade-AI/backups داخل MyDrive.")
            else:
                raise ValueError("اختر none أو download أو drive")
        else:
            print("لم تُنشأ نسخة احتياطية؛ التطبيق يبقى قيد التشغيل.")
    """),
            markdown("""
        ## 10. إيقاف الخادم عند الحاجة
        لا تُغلق الجلسة قبل تنزيل النسخة المطلوبة. لا يتم مسح البيانات عند إيقاف الخادم،
        لكن انتهاء جلسة Colab قد يمسح `/content` بالكامل.
    """),
            code("""
        # @title إيقاف اختياري
        STOP_APP = False # @param {type:"boolean"}
        if STOP_APP:
            previous = globals().get("_smartgrade_runtime")
            if previous is not None:
                previous.stop()
            print("تم إيقاف التطبيق. بيانات الجلسة لم تُحذف.")
    """),
            markdown("""
        ## ملاحظات وتشخيص
        - إذا لم تظهر الواجهة، تحقق من اتصال Runtime، وشغّل خلية الواجهة مجددًا. قد تمنع سياسات المتصفح أو حساب المؤسسة محتوى iframe.
        - استخدم لوحة ملفات Colab لقراءة `.colab/streamlit.log` عند فشل التشغيل؛ لا تنشر سجلات أو نسخًا تحتوي بيانات خاصة.
        - `Tesseract` خط أساس للنص العربي المطبوع؛ جودة خط اليد غير معتمدة وتحتاج تدقيقًا بشريًا.
        - المقالات دائمًا تحت مراجعة المعلم. نجاح JSON أو اختبارات البرمجيات لا يثبت جودة التقييم.
        - النتائج المعتمدة فقط تظهر في Excel وCSV. بعد تنزيل نسخة احتياطية يمكنك استعادتها في جلسة جديدة من الخطوة 4.
        - ملفات المشروع ووثائقه داخل `/content/SmartGrade-AI`. لا ترفع البيانات أو `.env` إلى GitHub.
    """),
        ]
    )
    for index, cell in enumerate(cells):
        cell["id"] = f"smartgrade-{index:02d}"
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "colab": {"name": "SmartGrade_Colab.ipynb", "provenance": []},
            "kernelspec": {"display_name": "Python 3", "name": "python3"},
            "language_info": {"name": "python"},
            "smartgrade_bundle_sha256": digest,
        },
        "cells": cells,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "notebooks/SmartGrade_Colab.ipynb")
    args = parser.parse_args()
    notebook = build_notebook()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(notebook, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(
        f"Built {args.output.name}; embedded source SHA256={notebook['metadata']['smartgrade_bundle_sha256']}"
    )


if __name__ == "__main__":
    main()
