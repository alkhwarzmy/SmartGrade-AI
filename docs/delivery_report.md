# تقرير التسليم

تم تنفيذ نسخة أولية محلية في المستودع، مع نجاح دورة MCQ من إنشاء الاختبار إلى التصدير، ودورة المقالات من رفع الصورة إلى OCR/تصحيح النص والمراجعة والاعتماد اليدوي. التطبيق مهيأ للتجربة المحلية وليس منتجًا مؤسسيًا معتمدًا.

## الملفات الرئيسية

- app.py: الصفحات الخمس، RTL، تنقل متجاوب للجوال، الرفع والمراجعة والاعتماد والتصدير والإعدادات.
- config.py و.env.example و.streamlit/config.toml: الإعدادات وحدود الملفات والتشغيل المحلي دون إرسال تلقائي.
- modules/: عقود البيانات، فحص الصور، OMR، OCR، مزود المقالات، المراجعة، التقارير، المقاييس والعينات المحلية.
- database/: النماذج والقيود والجلسات وعلاقات السجلات.
- scripts/: القالب والعينات الاصطناعية والتقييم والاستيراد واختبار المتصفح المعزول.
- tests/: 44 اختبارًا للوحدات والتكامل والواجهة.
- assets/tessdata/: نموذج اللغة العربية الرسمي وترخيص Apache 2.0.
- requirements*.txt وrequirements.lock وpyproject.toml: متطلبات التشغيل والاختبار ونسخ مثبتة.
- README.md وdocs/: دليل التشغيل والاستخدام والبنية والقيود والبيانات وأدلة التحقق.
- طُوّر README وgitignore الموجودان دون حذف ملفات المشروع السابقة.

## الأوامر المنفذة والنتائج

```text
python -m venv --system-site-packages .venv
.venv/bin/python -m pip install streamlit sqlalchemy opencv-python-headless pytest ruff
uv pip compile requirements-dev.txt --python-version 3.12 --output-file requirements.lock
uv venv work/verify-env --python python
uv pip sync requirements.lock --python work/verify-env/bin/python
work/verify-env/bin/python -m pytest -q
=> 44 passed
work/verify-env/bin/python -m ruff check .
=> All checks passed!
git diff --check
=> success
.venv/bin/python -m scripts.generate_omr --output outputs/demo
=> printable template and explicitly synthetic samples created
.venv/bin/python -m scripts.evaluate mcq-synthetic --output outputs/evaluation/mcq-synthetic.json
=> computed report; synthetic-only interpretation
.venv/bin/python -m scripts.browser_smoke
=> passed: MCQ, Excel download, actual local printed Arabic OCR, essay manual approval,
   invalidation after text edit, mobile navigation and layout
.venv/bin/python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501
=> running locally; opened and visually checked in shared Chromium
```

متغير UV_CACHE_DIR ضُبط داخل work لتجنب الكتابة خارج مساحة العمل. جرى تنزيل نموذج Tesseract وترخيصه من مستودعه الرسمي، والتحقق من بصمته. فحوص مصدرَي AR-AES وKHATT فشلت بسبب CONNECT 403 من وكيل الشبكة؛ لم تُنزّل بياناتهما.

## الحدود والقرارات المتبقية

- لم يُختبر خط يد عربي حقيقي أو اتفاق درجات LLM مع مصححين بشريين؛ نجاح النموذج المطبوع لا يثبت دعم خط اليد.
- مزود LLM منفذ واختُبر بمحاكاة HTTP، دون طلبات مدفوعة أو بيانات طلاب خارجية. تشغيله الحي يحتاج إعداد المزود وموافقة البيانات والتكلفة.
- لا نشر عام أو رفع للمستودع أو دمج أو تكلفة أُجريت.
- ما أُجّل وفق النطاق: تدريب HTR، دعم قوالب/دفعات/PDF مرفوع، نتائج PDF عربي، مستخدمون ومؤسسات، بنية إنتاجية.
- الخطوة التالية للمعايرة: عينات محلية مصرح بها مع نصوص مدققة ودرجات معلمين، ثم تقييم مستقل قبل أي استخدام فعلي للدرجات المقترحة.

## إضافة Colab والرفع

أضيف دفتر ذاتي الاحتواء، ومولّد ومدقق قابلان للإعادة، وإدارة تشغيل ونسخ واستعادة آمنة للبيانات. خطوات الاستخدام في colab_guide.md، وكل خطوات التنفيذ والتحقق والرفع في development_log.md. لم تُنفذ جلسة Colab فعلية مسجلة بحساب المستخدم؛ التحقق من المصدر المستخرج والتطبيق والنسخ والاستعادة أُجري محليًا.
