# سجل التنفيذ والرفع إلى المستودع

هذا السجل يوثق الأعمال المنفذة والأوامر القابلة لإعادتها، دون مفاتيح أو بيانات طلاب. المستودع المطابق للبيئة: `alkhwarzmy/SmartGrade-AI`. بدأ الفرع المحلي `work` من commit التأسيس `d6e328d`. لم تُحذف ملفات المستخدم السابقة ولم يُستخدم force push.

## 1. فحص الأساس

فُحص المستودع والبيئة وغياب AGENTS.md. كان المستودع يحتوي README وgitignore فقط. جرى الحفاظ عليهما وتطويرهما. استُخدم Python 3.12.14 وCPU، مع بيئة `.venv` داخل مساحة العمل.

```bash
python -m venv --system-site-packages .venv
.venv/bin/python -m pip install streamlit sqlalchemy opencv-python-headless pytest ruff
```

ملفات المتطلبات النهائية تحدد مكتبات التشغيل والاختبار والمتصفح بصورة منفصلة. ملف `requirements.lock` مولّد للإصدارات المختبرة على Python 3.12.

## 2. تنفيذ الوظائف

- عقود Pydantic للأسئلة والمعايير والمخرجات وحدود الدرجات والأدلة.
- صورة OMR ثابتة وقالب PDF، ومحاذاة باستخدام أربع علامات والتحقق من اتجاه الورقة وحلقات الإجابة.
- كشف الاختيار والفراغ والتظليل المتعدد والالتباس، مع صورة مراجعة وتعديل واعتماد المعلم.
- SQLite وSQLAlchemy، وعلاقات وسجل قبل التعديل وبعده وكشف التعديل المتزامن.
- سؤال مقالي مع معايير، ونص مصحح، ومقترح منفصل عن الدرجة المعتمدة. تغيير النص يلغي اعتماد الدرجة السابقة.
- Tesseract عربي محلي ومزود LLM اختياري بعقد JSON وموافقة إرسال وتكلفة؛ لا اعتماد مقالي تلقائي.
- خمس صفحات عربية وتنقل خاص بالجوال، وتصدير Excel وCSV بالعربية وحماية من صيغ جداول البيانات.
- مقاييس التقييم واستيراد العينات المحلية ودرجات المصححين المستقلين.

## 3. التحقق من البيانات والنموذج

نُزّل ملف العربية الرسمي من tessdata_fast مع ترخيص Apache 2.0؛ بصمته ومصدره في dataset_report.md. فشلت محاولة فحص AR-AES وKHATT بسبب رفض CONNECT برمز 403 من وكيل الشبكة. لا بيانات منهما أو ترخيص مفترض داخل المستودع.

```bash
python -m scripts.generate_omr --output outputs/demo
python -m scripts.evaluate mcq-synthetic --output outputs/evaluation/mcq-synthetic.json
python -m scripts.prepare_data data/local/samples.json
```

الأمر الأخير واجهة متاحة للاستيراد؛ لم يُشغّل على ملف طلاب حقيقي. نتائج MCQ الاصطناعية واختبار OCR المطبوع موثقة بأدلة منفصلة، دون نسب دقة حقيقية مختلقة.

## 4. اختبارات التطبيق

```bash
UV_CACHE_DIR=work/uv-cache uv pip compile requirements-dev.txt --python-version 3.12 --output-file requirements.lock
UV_CACHE_DIR=work/uv-cache uv venv work/verify-env --python python
UV_CACHE_DIR=work/uv-cache uv pip sync requirements.lock --python work/verify-env/bin/python
work/verify-env/bin/python -m pytest -q
work/verify-env/bin/python -m ruff check .
.venv/bin/python -m scripts.browser_smoke
```

اجتاز الإصدار المحلي الأول 35 اختبارًا، ورحلة Chromium كاملة للاختيارات والمقالات وتصدير Excel والجوال. صُلحت أثناء التحقق محاذاة الصور المصغرة، وتمثيل NULL في JSON، وانتظار اكتمال الرفع، وتأكيد اكتمال حفظ النص، وتغطية القائمة الجانبية للمحتوى على الجوال.

## 5. تكييف Google Colab

أُضيف دفتر ذاتي الاحتواء `notebooks/SmartGrade_Colab.ipynb` ومولّد قابل لإعادة البناء ومدقق للحزمة. يتضمن تهيئة Python معزول، وتثبيت OCR، وتجهيز القالب، والاختبارات، وعرض Streamlit عبر iframe الرسمي، وإعداد Colab Secrets اختياريًا، والنسخ والاستعادة والتنزيل أو Drive اختياريًا.

أُضيفت `modules/colab_runtime.py` لإدارة العملية والنسخ المتسق عبر SQLite Backup API والتحقق قبل الاستعادة. SQLite تبقى على قرص الجلسة، وDrive يستقبل ZIP فقط. لا يُستخدم نفق عام ولا تُعطّل CORS/XSRF.

```bash
python -m scripts.build_colab_notebook
python -m scripts.validate_colab_notebook --execute-smoke
python -m pytest -q
python -m ruff check .
git diff --check
```

أضيفت تسعة اختبارات للنسخ والاستعادة والعبث والمسارات والروابط الرمزية وتشغيل الخادم وإيقافه، فأصبح الإجمالي 44 اختبارًا. تحققت إعادة فك المصدر المضمن، ومنع الكتابة فوق تعديل موجود، وتوليد القالب وتشغيل مجموعة الاختبارات من نسخة مستخرجة. تشغيل حساب Colab وDrive وSecrets وiframe الفعلي يبقى خطوة المستخدم؛ لا ندعي إتمامه.

## 6. إعداد الرفع والتحقق منه

يُفحص remote وفرع main الحقيقيان باستخدام `git ls-remote` و`git fetch`، وتُراجع قائمة الملفات التي ستُرفع. تُستبعد `.env` و`storage/` و`data/` و`outputs/` و`work/` و`.venv/` و`.colab/` وقواعد البيانات والسجلات والنسخ الخاصة. الدفتر المنشور بلا مخرجات؛ حزمة المصدر داخله قائمة ملفات معلومة فقط.

أوامر الرفع المقصودة لهذا التسليم، بعد اجتياز الفحوص:

```bash
git fetch origin main
git add .gitignore README.md .env.example .streamlit app.py assets config.py database docs modules notebooks pyproject.toml requirements*.txt requirements.lock scripts tests
git diff --cached --check
git commit -m "Build SmartGrade MVP with self-contained Colab workflow and documentation"
git merge-base --is-ancestor origin/main HEAD
git push origin HEAD:main
git ls-remote origin refs/heads/main
```

تُقارن البصمة البعيدة مع `git rev-parse HEAD` بعد الرفع. إذا تغير main أثناء العمل، لا يُستخدم force؛ تُدمج التغييرات وتُعاد الفحوص اللازمة. نتيجة الرفع الفعلية وبصمة commit تُذكر في رسالة التسليم، ويمكن التحقق منها من سجل GitHub. هذه الأوامر لا تنشر تطبيقًا عامًا أو ترفع بيانات الطلاب أو تُنشئ PR/رسائل خارجية.


## 7. مرشح المؤسسات التجاري 1.0.0-rc1

أُنشئ فرع تطوير `commercial-v1` من الإصدار السابق `abac231` للحفاظ على نسخة MVP. أُضيفت:

- هوية مؤسسات ومستخدمين وجلسات وصلاحيات مدير/معلم/مشاهد، وتغيير إلزامي لكلمة المرور المؤقتة وإلغاء الجلسات وتقييد الدخول.
- `Workspace` كنطاق خدمات موحد للواجهة وAPI، مع قواعد/صور منفصلة للمؤسسات، وهوية مراجع من الحساب، وحصص تحت قفل Linux عبر العمليات.
- API موثق ومحمي، وحد جسم طلب فعلي وأخطاء لا تعيد كلمات المرور وno-store، ورفض المراجعات القديمة.
- سياسة إرسال AI للمؤسسة تُفحص قبل الاتصال، إضافة إلى موافقات المشغل والمراجع.
- PDF عربي بخط مضمن مرخص، وصور/تنزيلات تجارية inline بدل MediaFileManager العام.
- لوحة فريق وسعة وسجل مؤسسة، وأدوات إعداد/ترخيص/نسخ/استيراد/إلغاء جلسات واحتفاظ dry-run.
- صورة Docker غير جذريّة مع جذر قراءة، وCompose وCaddy لإعداد HTTPS. لم يُنشر نطاق عام أو تُحصّل رسوم.

أُعيد توليد متطلبات الاختبار وملف runtime منفصل دون أدوات الاختبار للصورة. فشل بناء Docker أولًا بسبب CA لوكيل الشبكة؛ عولج بتركيب CA موثوقة كـBuildKit secret، دون تعطيل TLS أو حفظها في الصورة. أثناء فحص الموارد خُفِّض OpenCV إلى عاملين افتراضيًا بدل الاعتماد على عدد CPUs للمضيف. كُشفت كذلك صلاحيات ملفات COPY المتشددة التي منعت UID10001 من قراءة المصدر، وعُولجت بقراءة644 ومجلدات755 مع ملكيةroot وجذر تشغيل للقراءة. تجري اختبارات الحاويات ببيانات ومجلدات اصطناعية تُنظف بعدها.

أوامر التحقق القابلة للإعادة:

```bash
uv pip compile requirements-dev.txt --python-version 3.12 --output-file requirements.lock
uv pip compile requirements.txt --python-version 3.12 --output-file requirements-runtime.lock
uv pip sync requirements.lock --python work/verify-env/bin/python
work/verify-env/bin/python -m pytest -q
work/verify-env/bin/python -m ruff check .
.venv/bin/python -m scripts.commercial_browser_smoke
SMARTGRADE_DOMAIN=smartgrade.example.test docker compose config --quiet
docker build --secret id=proxy_ca,src=/path/to/trusted-ca-bundle.pem -t smartgrade:1.0.0-rc1 .
python -m scripts.docker_smoke
python -m scripts.build_colab_notebook
python -m scripts.validate_colab_notebook --execute-smoke
```

نتائج النسخة التجارية وحدود ما اختُبر في `commercial_release.md` وأدلتها في docs/evidence. شارة Colab تبقى للتجربة بوضع demo صريح، مع تضمين المصدر الجديد واختباراته. بعد اجتياز التحقق تُراجع ملفات الرفع لاستبعاد البيانات والمفاتيح والسجلات، ويُدفع main دونforce مع وسم مرشح الإصدار والتحقق من البصمة البعيدة.
