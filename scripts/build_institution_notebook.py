"""Self-contained Colab entry point for the authenticated institution app, with empty real data."""

import argparse
import json
from pathlib import Path
from scripts.build_colab_notebook import ROOT, build_notebook, code, markdown


def build_institution_notebook(root=ROOT):
    notebook = build_notebook(root)
    replacements = {
        "# SmartGrade AI — التشغيل على Google Colab": markdown("""
        # SmartGrade AI — تشغيل نسخة المؤسسات على Google Colab
        يشغّل هذا الدفتر **وضع commercial الفعلي**: مؤسسة وحساب مدير تختارهما، دخول وصلاحيات،
        أوراق ودرجاتك، وعامل مهام OCR والتقارير. يبدأ بقاعدة فارغة دون اختبارات أو طلاب جاهزين.
        اختر Python 3 / CPU ثم Run all، وأدخل بيانات مؤسستك وكلمة المرور المخفية عندما تُطلب.

        **مدة التشغيل:** تعمل الواجهة ما دامت جلسة Colab متصلة. تفعيل الحسابات لا يحوّل Colab إلى استضافة دائمة.
        لحفظ العمل بين الجلسات استخدم النسخة الكاملة المشفرة ومفتاحها المنفصل في الخطوتين4 و9.
        لخدمة مستمرة24/7 استخدم نشر Docker على جهازك أو خادمك وفق دليل التشغيل المضمن.
        لا رابط عام أو tunnel ولا اتصال مدفوع تلقائي. الصور تُعالج في بيئة Google السحابية؛ استخدم بيانات مصرحًا بها.
    """),
        "# @title 1. إعداد مسارات الجلسة": code("""
        # @title 1. مسارات نسخة المؤسسات
        import os
        import sys
        from pathlib import Path
        if sys.version_info < (3, 11):
            raise RuntimeError("يلزم Python 3.11 أو أحدث")
        CONTENT_ROOT = Path(os.environ.get("SMARTGRADE_COLAB_ROOT", "/content")).absolute()
        PROJECT_DIR = CONTENT_ROOT / "SmartGrade-Institutions"
        DATA_DIR = CONTENT_ROOT / "smartgrade-institution-data"
        VENV_DIR = CONTENT_ROOT / "smartgrade-venv"
        PYTHON = VENV_DIR / "bin" / "python"
        BACKUP_KEY_FILE = CONTENT_ROOT / "smartgrade-institution-backup.key"
        PORT = 8502
        AI_ENV = {}
        CONTENT_ROOT.mkdir(parents=True, exist_ok=True)
        print("بيانات المؤسسات المحلية لهذه الجلسة:", DATA_DIR)
    """),
        "## 4. استعادة بيانات سابقة": markdown("""
        ## 4. استئناف العمل من نسخة مشفرة — اختياري
        في أول تشغيل اترك الخيار مغلقًا. للاستعادة ارفع snapshot.sgb ومفتاحه إلى جلسة Colab من لوحة الملفات،
        أو اربط Drive بنفسك وحدد مسار النسخة. تُفك البيانات على القرص المحلي، ولا توضع SQLite الحية على Drive.
        الوجهة يجب أن تكون جديدة؛ لا تُستبدل بيانات موجودة. تُلغى جلسات الدخول القديمة بعد الاستعادة.
    """),
        "# @title استعادة نسخة ZIP موثوقة": code("""
        # @title استعادة حسابات المؤسسات وبياناتها من نسخة كاملة مشفرة
        RESTORE_INSTITUTION = False # @param {type:"boolean"}
        RESTORE_ARCHIVE = "" # @param {type:"string"}
        RESTORE_KEY_FILE = "" # @param {type:"string"}
        _institution_restore_complete = False
        if RESTORE_INSTITUTION:
            previous = globals().get("_smartgrade_institution_runtime")
            if previous is not None and not previous.stopped:
                raise RuntimeError("أوقف الواجهة والعامل قبل الاستعادة")
            if DATA_DIR.exists():
                raise RuntimeError("اختر DATA_DIR جديدًا؛ لا تُستبدل بيانات موجودة")
            archive = Path(RESTORE_ARCHIVE)
            supplied_key = Path(RESTORE_KEY_FILE)
            if not RESTORE_ARCHIVE or not RESTORE_KEY_FILE or not archive.is_file() or not supplied_key.is_file():
                raise ValueError("حدد مسار النسخة .sgb ومسار ملف مفتاحها")
            if supplied_key.stat().st_size != 32 or BACKUP_KEY_FILE.is_symlink():
                raise ValueError("حدد ملف مفتاح32 بايتًا ومسار مفتاح محليًا غير رمزي")
            key_material = supplied_key.read_bytes()
            try:
                if len(key_material) != 32:
                    raise ValueError("ملف المفتاح يجب أن يحتوي32 بايتًا")
                if BACKUP_KEY_FILE.exists():
                    if BACKUP_KEY_FILE.read_bytes() != key_material:
                        raise ValueError("مفتاح مختلف موجود؛ احتفظ به واختر BACKUP_KEY_FILE جديدًا")
                    BACKUP_KEY_FILE.chmod(0o600)
                else:
                    descriptor = os.open(BACKUP_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(descriptor, "wb") as handle:
                        handle.write(key_material)
            finally:
                del key_material
            subprocess.run([str(PYTHON), "-m", "scripts.manage", "restore-full", "--archive", str(archive),
                            "--destination", str(DATA_DIR), "--key-file", str(BACKUP_KEY_FILE)], cwd=PROJECT_DIR, check=True)
            _institution_restore_complete = True
            print("استُعيدت البيانات والحسابات. سجّل الدخول بكلمة مرورك السابقة.")
        else:
            print("تخطي الاستعادة. لن تُستبدل بيانات موجودة.")
    """),
        "# @title تشغيل اختبارات المشروع": code("""
        # @title تحقق التثبيت — مستقل عن بيانات مؤسستك
        RUN_TESTS = False # @param {type:"boolean"}
        if RUN_TESTS:
            subprocess.run([str(PYTHON), "-m", "pytest", "-q"], cwd=PROJECT_DIR, check=True,
                           env={**os.environ, "SMARTGRADE_DATA_DIR": str(CONTENT_ROOT / "smartgrade-isolated-test-data"),
                                "ALLOW_EXTERNAL_AI": "false", "ALLOW_PAID_API": "false"})
    """),
        "## 6. إنشاء قالب الطباعة": markdown("""
        ## 6. إنشاء مؤسستك وحساب المدير
        لا توجد بيانات دخول افتراضية. في أول تشغيل أدخل رمز مؤسستك واسمها وبريد المدير،
        ثم اختر كلمة مرور من12 إلى128 حرفًا؛ تُطلب مخفية ولا تُكتب في مصدر الدفتر.
        عند الاستئناف لا يُعاد إنشاء الحساب ولا تتغير كلمة المرور.
        سعة school إعداد محلي للمؤسسة وليست عملية شراء أو دفع.

        يُنشأ مفتاح مستقل للنسخ المشفرة وتُتاح إمكانية تنزيله. احتفظ به بعيدًا عن النسخ؛ لا يُستعاد العمل بدونه.
        البيانات تبدأ فارغة. نزّل قالب MCQ من الواجهة وأدخل اختبارك وأوراقك، أو استخدم التصحيح المقالي.
    """),
        "# @title توليد قالب OMR وعينة اصطناعية": code("""
        # @title إنشاء حسابك الحقيقي ومفتاح الاستعادة دون أمثلة جاهزة
        from getpass import getpass
        from modules.auth import AuthService
        from google.colab import files
        if globals().get("RESTORE_INSTITUTION") and not globals().get("_institution_restore_complete"):
            raise RuntimeError("أكمل الاستعادة بنجاح قبل إنشاء حساب أو تشغيل الواجهة")
        identity = AuthService(DATA_DIR)
        if not identity.has_organizations():
            organization_slug = input("رمز المؤسسة بالإنجليزية (مثل school-name): ").strip()
            organization_name = input("اسم المؤسسة: ").strip()
            admin_email = input("البريد الإلكتروني للمدير: ").strip()
            admin_name = input("اسم المدير: ").strip()
            password = confirmation = None
            try:
                password = getpass("اختر كلمة مرور المدير (12 حرفًا على الأقل): ")
                confirmation = getpass("تأكيد كلمة المرور: ")
                if password != confirmation:
                    raise ValueError("كلمتا المرور غير متطابقتين")
                identity.provision_organization(organization_slug, organization_name, admin_email, admin_name,
                                                password, plan="school")
            finally:
                del password, confirmation
            print("أُنشئت المؤسسة. استخدم الرمز والبريد وكلمة المرور في شاشة الدخول.")
        else:
            print("حسابات موجودة؛ أُبقيت بياناتها وكلمات مرورها كما هي.")
        if not BACKUP_KEY_FILE.exists():
            descriptor = os.open(BACKUP_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(os.urandom(32))
            files.download(str(BACKUP_KEY_FILE))
            print("نُزّل مفتاح النسخ أو طُلب تنزيله من المتصفح. احتفظ بالملف منفصلًا عن النسخ المشفرة.")
        if not BACKUP_KEY_FILE.is_file() or BACKUP_KEY_FILE.is_symlink() or BACKUP_KEY_FILE.stat().st_mode & 0o077 or BACKUP_KEY_FILE.stat().st_size != 32:
            raise ValueError("مفتاح النسخ غير صالح أو غير محمي؛ استخدم الملف الخاص ذي32 بايتًا")
    """),
        "## 8. تشغيل الواجهة العربية": markdown("""
        ## 8. تشغيل نسخة المؤسسات
        يشغّل هذا القسم Streamlit بوضع commercial وعامل المهام بالبيانات نفسها.
        تظهر شاشة دخول فعلية داخل وكيل Colab، مع إبقاء CORS وXSRF مفعّلين، ودون رابط نشر عام.
        ادخل برمز المؤسسة والبريد وكلمة المرور التي اخترتها. يُطلب منك إنشاء اختبارك الأول بنفسك.
        إعادة تشغيل الخلية توقف عمليتيها السابقتين فقط وتحافظ على الحسابات والأوراق.
    """),
        "# @title فتح SmartGrade داخل Colab": code("""
        # @title تشغيل الواجهة المحمية وعامل OCR والتقارير
        from google.colab import output
        from urllib.parse import urlsplit
        from modules.institution_runtime import launch_institution
        previous = globals().get("_smartgrade_institution_runtime")
        if previous is not None:
            previous.stop()
        proxy_url = str(output.eval_js(f"google.colab.kernel.proxyPort({PORT})"))
        address = urlsplit(proxy_url).hostname
        if not address:
            raise RuntimeError("تعذر تحديد عنوان وكيل Colab؛ تحقق من اتصال الجلسة")
        _smartgrade_institution_runtime = launch_institution(PROJECT_DIR, PYTHON, DATA_DIR, port=PORT,
                                                            browser_address=address, extra_env=AI_ENV)
        print("نسخة المؤسسات تعمل. سجّل الدخول بحسابك. العامل يعالج المهام في الخلفية.")
        output.serve_kernel_port_as_iframe(PORT, height=1000, cache_in_notebook=False)
    """),
        "## 9. حفظ البيانات قبل انتهاء الجلسة": markdown("""
        ## 9. حفظ العمل الحقيقي بين الجلسات
        اختر download لتنزيل نسخة.sgb كاملة مشفرة، أو drive لحفظها في MyDrive بعد موافقتك على ربط Drive.
        يتوقف الخادم والعامل أولًا. تُحفظ الحسابات والمؤسسات والصور والدرجات والسجل والتقارير.
        المفتاح يبقى منفصلًا ولا يُضمَّن في النسخة. إذا حُظر تنزيله أول مرة فعّل DOWNLOAD_BACKUP_KEY هنا.
        لا توجد SQLite حية على Drive، ولا حفظ تلقائي مضمون عند انقطاع Colab المفاجئ.
        بعد النسخ أعد تشغيل خلية الواجهة لمتابعة العمل.
    """),
        "# @title نسخ البيانات وتنزيلها أو حفظها في Drive": code("""
        # @title نسخة كاملة مشفرة للحسابات والعمل
        BACKUP_ACTION = "none" # @param ["none", "download", "drive"]
        DOWNLOAD_BACKUP_KEY = False # @param {type:"boolean"}
        if BACKUP_ACTION not in {"none", "download", "drive"}:
            raise ValueError("اختر none أو download أو drive")
        if BACKUP_ACTION != "none":
            from datetime import datetime, timezone
            from uuid import uuid4
            from google.colab import files
            previous = globals().get("_smartgrade_institution_runtime")
            if previous is not None:
                previous.stop()
            name = "smartgrade-institution-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8] + ".sgb"
            archive = CONTENT_ROOT / name
            subprocess.run([str(PYTHON), "-m", "scripts.manage", "--data-dir", str(DATA_DIR), "backup-full",
                            "--output", str(archive), "--key-file", str(BACKUP_KEY_FILE), "--services-stopped"],
                           cwd=PROJECT_DIR, check=True)
            if BACKUP_ACTION == "download":
                files.download(str(archive))
            else:
                from google.colab import drive
                drive.mount(str(CONTENT_ROOT / "drive"))
                folder = CONTENT_ROOT / "drive" / "MyDrive" / "SmartGrade-AI" / "institution-backups"
                folder.mkdir(parents=True, exist_ok=True)
                with archive.open("rb") as source, (folder / name).open("xb") as destination:
                    shutil.copyfileobj(source, destination)
                print("حُفظت النسخة المشفرة في MyDrive/SmartGrade-AI/institution-backups")
        if DOWNLOAD_BACKUP_KEY:
            from google.colab import files
            files.download(str(BACKUP_KEY_FILE))
        if BACKUP_ACTION == "none":
            print("الواجهة تظل تعمل. احفظ نسخة مشفرة قبل إنهاء الجلسة.")
    """),
        "# @title إيقاف اختياري": code("""
        # @title إيقاف واجهة المؤسسات وعاملها
        STOP_APP = False # @param {type:"boolean"}
        if STOP_APP:
            previous = globals().get("_smartgrade_institution_runtime")
            if previous is not None:
                previous.stop()
            print("توقفت الواجهة والعامل. البيانات باقية على قرص الجلسة حتى انتهائها.")
    """),
        "## ملاحظات وتشخيص": markdown("""
        ## التشغيل والمتابعة
        - ابدأ بإنشاء اختبارك ورفع أوراقك. MCQ يدعم من 1 إلى 50 سؤالًا ×4 اختيارات بقالب مطابق للعدد، والاعتماد النهائي للمعلم.
        - الحسابات والاختبارات حقيقية وظيفيًا؛ لا ينشئ هذا الدفتر مستخدمين أو درجات نموذجية.
        - OCR عربي محلي، أو إدخال يدوي. اقتراح المقالات الخارجي يحتاج تفعيل المشغل وسياسة المؤسسة وموافقتك على الطلب والتكلفة.
        - احتفظ بالنسخة.sgb ومفتاحها خارج Colab. في الجلسة التالية استعدهما قبل إعداد الحساب؛ لا تنشئ مؤسسة بديلة ثم تحاول استبدالها.
        - جودة الخط اليدوي والتصحيح الآلي تحتاج تقييمًا مستقلًا. تشغيل commercial لا يثبت دقة نموذج أو صلاحية استضافة24/7.
        - لم يُنفّذ هذا الدفتر داخل حساب Google الخاص بك أثناء إعداده؛ اختُبرت الحسابات والعامل والنسخ والاستعادة محليًا.
        - ملفات السجل الخاصة داخل PROJECT_DIR/.colab. لا تشاركها أو ترفع البيانات أو المفاتيح إلىGit.
    """),
    }
    matched = set()
    for index, cell in enumerate(notebook["cells"]):
        source = cell["source"].lstrip()
        for marker, replacement in replacements.items():
            if source.startswith(marker):
                notebook["cells"][index] = replacement
                matched.add(marker)
                break
    if matched != set(replacements):
        raise AssertionError("Institution notebook sections no longer match the shared generator")
    for index, cell in enumerate(notebook["cells"]):
        cell["id"] = f"smartgrade-institution-{index:02d}"
    notebook["metadata"]["colab"]["name"] = "SmartGrade_Institution_Colab.ipynb"
    notebook["metadata"]["smartgrade_mode"] = "commercial"
    return notebook


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "notebooks/SmartGrade_Institution_Colab.ipynb")
    args = parser.parse_args()
    notebook = build_institution_notebook()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(notebook, ensure_ascii=False, indent=1) + "\n")
    print(
        f"Built {args.output.name}; mode=commercial; embedded source SHA256={notebook['metadata']['smartgrade_bundle_sha256']}"
    )


if __name__ == "__main__":
    main()
