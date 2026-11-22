"""Build the complete authenticated Colab notebook, including Android source."""
import json
from scripts.build_colab_notebook import ROOT, code, markdown
from scripts.build_institution_notebook import build_institution_notebook


def build_complete_notebook(root=ROOT):
    notebook = build_institution_notebook(root)
    notebook["cells"][0] = markdown("""
    # SmartGrade AI — الدفتر الشامل
    نسخة المؤسسات الفعلية مع جميع التحديثات: أسئلة مقالية متعددة، إجابة ومعايير ودرجة لكل سؤال،
    مراجعة واعتماد المعلم، اختيارات OMR، OCR عربي، تقارير، حسابات وصلاحيات، نسخ مشفر، ومصدر Flutter Android.
    اختر Python 3 / CPU ثم **Runtime → Run all**. أدخل حساب مؤسستك عند الطلب وافتح رابط الواجهة في نافذة مستقلة.
    يبدأ التشغيل بقاعدة فارغة؛ لا يتصل بمزود تصحيح مدفوع تلقائيًا.

    **يشغّل Colab الخادم وواجهة الويب.** ملفات تطبيق Android مضمنة للتنزيل والبناء وفق flutter_app/README.md.
    تطبيق الهاتف يحتاج خادم HTTPS يمكنه الوصول إليه؛ وكيل Colab المحمي بحساب Google ليس عنوان API للهاتف.
    **احفظ نسخة .sgb ومفتاحها قبل إنهاء الجلسة.** Colab مؤقت، وليس استضافة دائمة.
    يظل اقتراح الدرجة خاضعًا لمراجعة المعلم. لم يُقَس هنا اتفاق النموذج مع مصححين عرب أو دقة خط اليد.
    """)
    for cell in notebook["cells"]:
        if cell["cell_type"] == "code":
            cell["source"] = cell["source"].replace(
                "output.serve_kernel_port_as_iframe(PORT, height=1000, cache_in_notebook=False)",
                "output.serve_kernel_port_as_window(PORT)",
            )
        elif "## 8. تشغيل نسخة المؤسسات" in cell["source"]:
            cell["source"] = cell["source"].replace("داخل وكيل Colab", "في نافذة مستقلة عبر وكيل Colab")
    notebook["cells"].extend([
        markdown("""
        ## فحص التصحيح دون طلب مدفوع — اختياري
        يتحقق هذا الفحص من العينات المرجعية ويقيس OCR على صورة مطبوعة معروفة.
        يعرض الإعداد الناقص ورمز الخطأ، ويكتب تقريرًا دون نصوص الطلاب أو مفاتيح المزود.
        لا يستدعي نموذج التصحيح ولا ينتج نسبة دقة وهمية. زر «فحص سبب تعثر التصحيح» داخل الواجهة يفحص ورقتك الحالية.
        """),
        code("""
        # @title تشخيص مسار التصحيح محليًا دون تكلفة API
        RUN_GRADING_DIAGNOSTIC = False # @param {type:"boolean"}
        MEASURE_PRINTED_OCR = True # @param {type:"boolean"}
        if RUN_GRADING_DIAGNOSTIC:
            import subprocess
            diagnostic_path = CONTENT_ROOT / "smartgrade-grading-diagnostic.json"
            arguments = [str(PYTHON), "-m", "scripts.diagnose_grading", "--output", str(diagnostic_path)]
            if MEASURE_PRINTED_OCR:
                arguments.append("--ocr-sample")
            subprocess.run(arguments, cwd=PROJECT_DIR, env={**os.environ, **AI_ENV}, check=True)
            print("تقرير الفحص:", diagnostic_path)
        """),
        markdown("""
        ## تنزيل الكود الكامل — اختياري
        فعّل الخيار في الخلية التالية لتنزيل ZIP يضم الخادم والواجهة والاختبارات والتوثيق ومصدر Android.
        لا يضم حساباتك أو أوراق الطلاب أو بيانات الجلسة أو مفاتيحها؛ لحفظ عملك استخدم النسخ المشفر في الخطوة9.
        لبناء تطبيق الهاتف اتبع flutter_app/README.md. يمكن أيضًا تنزيل APK الاختبار من تبويب Actions في المستودع.
        """),
        code("""
        # @title تنزيل جميع ملفات المصدر بما فيها Flutter Android
        DOWNLOAD_ALL_SOURCE = False # @param {type:"boolean"}
        if DOWNLOAD_ALL_SOURCE:
            from scripts.build_colab_notebook import source_bundle
            source_zip = CONTENT_ROOT / "SmartGrade-Complete-Source.zip"
            source_zip.write_bytes(source_bundle(PROJECT_DIR))
            from google.colab import files
            files.download(str(source_zip))
        """),
    ])
    for index, cell in enumerate(notebook["cells"]):
        cell["id"] = f"smartgrade-complete-{index:02d}"
    notebook["metadata"]["colab"]["name"] = "SmartGrade_Complete_Colab.ipynb"
    notebook["metadata"]["smartgrade_features"] = [
        "commercial-auth", "multi-question-essay", "scoped-evidence", "teacher-approval",
        "grading-diagnostics", "arabic-ocr", "omr20", "encrypted-backup", "flutter-android-source", "separate-window",
    ]
    return notebook


def main():
    target = ROOT / "notebooks/SmartGrade_Complete_Colab.ipynb"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(build_complete_notebook(), ensure_ascii=False, indent=1) + "\n")
    print("Built complete commercial notebook with Android source.")


if __name__ == "__main__":
    main()
