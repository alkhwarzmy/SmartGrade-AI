# SmartGrade AI · المؤسسات

**الإصدار 1.0.0-rc2 — مرشح تجربة تجارية مُدارة.** منصة عربية لتصحيح MCQ ومراجعة المقالات، مع حسابات وصلاحيات وعزل بيانات المؤسسات وتقارير معتمدة من المعلم. جودة خط اليد والتقييم المقالي الآلي ليست معتمدة على بيانات طلاب حقيقية؛ راجع حدود الإصدار قبل استخدامه مع عملاء.

**تحديث الاعتمادية:** [تنفيذ الخطة ونتائج RC2 وحدود التحقق](docs/releases/rc2/implementation.md) · [تشغيل الطابور والنسخ والاستعادة والترقية](docs/releases/rc2/operations.md).

**واجهات العمل:** [دليل الويب وAndroid ومسارات المعلم](docs/COMMERCIAL_WORKSPACE_AR.md) — بحث وتصفية الأوراق، معاينة الصور قبل الحفظ، وتنقل مباشر بين خطوات التصحيح.

## ما يعمل في هذا الإصدار

| المجال | السلوك المنفذ |
|---|---|
| المؤسسات | قاعدة بيانات وصور منفصلة لكل مؤسسة؛ اختيار المؤسسة من عضوية جلسة موثقة |
| الدخول | كلمات مرور PBKDF2-SHA256، جلسات عشوائية ببصمات مخزنة، انتهاء مطلق وخمول، وتقييد محاولات الدخول |
| الفريق | مدير، معلم/مصحح، ومشاهد للنتائج المعتمدة؛ تغيير كلمة المرور المؤقتة إلزامي |
| المراجعة | لا اعتماد مقالي تلقائي؛ هوية المراجع من الحساب؛ تغيير النص يلغي الاعتماد السابق، وحفظ النص نفسه يبقيه |
| MCQ | قالب OMR ثابت 20×4، محاذاة، فراغ ومتعدد والتباس، ومراجعة الصورة |
| المقالات | OCR عربي محلي كخط أساس، تدقيق النص، معايير ودرجات وأدلة متحققة، ومزود LLM اختياري |
| التقارير | Excel وCSV بالتفاصيل وPDF عربي بملخص المعتمد، مع خط مضمن |
| الحصص | حدود للمستخدمين والاختبارات والأوراق، وفحص مساحة رفع الصور، بقفل عبر عمليات المضيف |
| التدقيق | سجل تغييرات الدرجات، وسجل مؤسسة إضافي متسلسل البصمات لا يقبل UPDATE/DELETE عبر SQL المعتاد |
| التشغيل | Docker غير جذري وبجذر ملفات للقراءة، Compose وCaddy لـHTTPS، وAPI محمي |
| البيانات | نسخ كامل مشفر AES-256-GCM، واستعادة الهوية والمؤسسات مع إلغاء الجلسات، وترقية وتراجع متحققان على عينة |
| الاعتمادية | Outbox ومنع تكرار الرفع وحذف قابل للاستكمال؛ طابور OCR/تقارير دائم وصفحات بيانات محدودة |

## بدء محلي آمن

المتطلبات: **Linux وPython 3.12** للإصدار التجاري الأصلي، أو Docker على نظام يدعمه. وظائف CPU الأساسية لا تحتاج GPU أو API key.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r requirements-runtime.lock
python -m scripts.manage init --slug my-school --name "اسم المؤسسة" --email admin@example.com
python -m streamlit run app.py
# شغّل في طرفية ثانية بنفس مجلد البيانات:
python -m scripts.worker
```

يطلب الإعداد كلمة مرور المدير بشكل مخفي (12–128 حرفًا). لا حسابات افتراضية ولا تسجيل مؤسسة مفتوح للعامة. افتح `http://localhost:8501` وسجّل الدخول برمز المؤسسة والبريد وكلمتك. يدير مشغل النظام إنشاء مؤسسات إضافية من الأداة نفسها. لا تفتح منفذ HTTP المحلي مباشرة للإنترنت؛ إعداد HTTPS أدناه.

لـOCR يلزم برنامج Tesseract على المضيف. ملف اللغة العربية الرسمي مضمن بترخيصه. غياب البرنامج يُبقي إدخال النص اليدوي متاحًا. صورة Docker تتضمن Tesseract بالفعل.

## تشغيل Docker مع HTTPS

```bash
cp .env.example .env
# أضف SMARTGRADE_DOMAIN بنطاق تملكه وتوجه DNS إلى الخادم.
docker compose build
docker compose run --rm --no-deps web python -m scripts.manage init --slug my-school --name "اسم المؤسسة" --email admin@example.com
docker compose up -d
```

لا ينشئ هذا المستودع نطاقًا أو استضافة. يلزم فتح 80/443 على خادمك وDNS صحيح لإصدار الشهادة. لا تعرض منافذ web/API الداخلية مباشرة. اقرأ [دليل تشغيل RC2 والاستعادة](docs/releases/rc2/operations.md) قبل خدمة مستخدمين حقيقيين. عند وجود CA مؤسسية في شبكة البناء يمكن تمريرها كـBuildKit secret دون حفظها في الصورة أو تعطيل TLS.

## الاستخدام

1. أنشئ اختبار MCQ بالمفتاح الافتراضي `A B C D` خمس مرات، أو سؤالًا مقاليًا ومعايير صحيحة.
2. من «قالب الإجابة» نزّل قالب A4 والعينة الاصطناعية. القالب ثابت 20 سؤالًا وأربعة اختيارات.
3. ارفع صورة PNG/JPEG (حتى 10 MB و16 مليون بكسل)، ثم راجع الصورة والنص والإجابات.
4. اعتمد النتيجة بحساب المعلم أو المدير. لا تُقبل درجة خارج حدود المعايير أو دليل غير موجود حرفيًا.
5. صدّر المعتمد فقط. في الوضع التجاري تنتقل الصور والتقارير داخل عرض الجلسة دون تسجيلها بروابط الوسائط العامة لـStreamlit.
6. يدير المدير أعضاء الفريق وسياسة AI وسجل المؤسسة. المشاهد لا يرى الصور الخام أو المسودات أو أدوات التعديل.

## العقود والسعة

الخطط حدود تشغيل محلية، **وليست بوابة دفع أو اشتراكًا محصّلًا آليًا**:

| الخطة | مستخدمون نشطون | اختبارات | أوراق محفوظة | حد رفع التخزين MB |
|---|---:|---:|---:|---:|
| pilot | 5 | 100 | 1,000 | 1,024 |
| school | 25 | 1,000 | 25,000 | 10,240 |
| campus | 100 | 5,000 | 100,000 | 51,200 |

هذه حصص منتج، وليست نتائج اختبار أحمال أو ضمان استيعاب متزامن. التخزين يُفحص عند الرفع والتعديلات الكبيرة وتوليد التقارير؛ توجد عدادات ملفات وتنبيه عند80% واحتياط للقرص. تحتاج الذاكرة وحدود الموارد إلى قبول على مضيف التشغيل؛ راجع تقريرRC2. يحدد المشغل العقود والتسعير والسعة وتواريخ السريان:

```bash
python -m scripts.manage license --slug my-school --plan school --until 2027-12-31
```

يتوقف الدخول والوصول عند تعطيل المؤسسة أو انتهاء ترخيصها. لا يوجد زر شراء شكلي أو رسوم تلقائية. [النطاق التجاري وما قبل الإطلاق](docs/commercial_release.md).

## API

```bash
python -m uvicorn api:app --host 127.0.0.1 --port 8000 --no-access-log
```

المسارات تتطلب Bearer token من `/auth/login`، عدا `/health` و`/ready`. لا يُقبل دور أو مؤسسة من ترويسة العميل. عند التشغيل خلف Caddy تصبح المسارات تحت `/api/`. التوثيق التفاعلي مغلق افتراضيًا؛ فعّل `SMARTGRADE_API_DOCS=true` في بيئة تطوير موثوقة فقط. راجع [عقد API الحالي والمهام الخلفية](docs/releases/rc2/operations.md)، و[مرجع المسارات الأساسي](docs/api.md).

## مزود المقالات الاختياري

الإعدادات في `.env.example`. لا تُطبع المفاتيح ولا تُضمّن في Git أو Notebook. يلزم تفعيل `ALLOW_EXTERNAL_AI` و`ALLOW_PAID_API` من المشغل، واعتماد سياسة المؤسسة من المدير، ثم موافقة المراجع على إرسال النص والتكلفة لكل طلب. إعداد المفتاح وحده لا يرسل طلبًا. لم تُجرَ طلبات مدفوعة أثناء التطوير. حدود إنفاق المزود واتفاقية معالجة بياناته مسؤولية المشغل؛ لا توجد فاتورة أو دقة تصحيح مختلقة.

## الاختبارات

```bash
python -m pip install --require-hashes -r requirements.lock
python -m pytest -q
python -m ruff check .
python -m scripts.commercial_browser_smoke  # Chromium + Playwright، متطلبات requirements-browser.txt
python -m scripts.docker_smoke             # بعد بناء صورة smartgrade:1.0.0-rc2
python -m scripts.build_colab_notebook
python -m scripts.build_institution_notebook
python -m scripts.validate_colab_notebook --execute-smoke
python -m scripts.validate_colab_notebook --notebook notebooks/SmartGrade_Institution_Colab.ipynb
```

تستخدم الاختبارات بيانات ومؤسسات وكلمات مرور اصطناعية معزولة؛ لا تُنشئ حسابات على نسخة الإنتاج. راجع [نتائج RC2 وحدود التحقق](docs/releases/rc2/implementation.md)، و[الأدلة المحفوظة](docs/releases/rc2/evidence/)، و[أوامر إعادة التحقق](docs/releases/rc2/verify.md).

## تشغيل المؤسسات على Colab

[![Open Institutions In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/alkhwarzmy/SmartGrade-AI/blob/main/notebooks/SmartGrade_Institution_Colab.ipynb)

[افتح نسخة المؤسسات بحسابات فعلية](https://colab.research.google.com/github/alkhwarzmy/SmartGrade-AI/blob/main/notebooks/SmartGrade_Institution_Colab.ipynb)، واختر CPU ثم Run all. أدخل بيانات مؤسستك وكلمة مرور مخفية؛ يبدأ التطبيق ببيانات فارغة، مع عامل المهام والنسخ الكامل المشفر. [دليل الإعداد والحفظ والاستئناف](docs/institution_colab.md).

التشغيل داخل Colab يستمر مع جلسة Google، والبيانات المحلية مؤقتة؛ احفظ النسخة المشفرة ومفتاحها لاستئناف العمل. التشغيل الدائم يستخدم نسخة الخادم بقرص دائم وHTTPS.

## تجربة Colab بدون حسابات

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/alkhwarzmy/SmartGrade-AI/blob/main/notebooks/SmartGrade_Colab.ipynb)

الدفتر ذاتي الاحتواء يعمل صراحةً بوضع **demo** للتجربة، مع CPU وRun all، وليس بديلًا لخادم المؤسسات. وضع demo بدون حسابات؛ استخدم عينات اصطناعية. الحفظ على Drive اختياري ويقتصر على ZIP؛ SQLite الحية تبقى على قرص الجلسة. [دليل Colab](docs/colab_guide.md).

## وثائق المشروع

- [تشغيل المؤسسات الفعلي داخل Colab](docs/institution_colab.md)

- [تقرير الاختبار والتحسينات — 18 نوفمبر 2026](docs/audits/2026-11-18/report.md)
- [خطة تنفيذ التحسينات ومعايير القبول](docs/audits/2026-11-18/action_plan.md) · [التقرير والخطة بصيغة PDF](docs/audits/2026-11-18/report_and_plan.pdf)
- [تنفيذ خطة التحسين ونتائج RC2](docs/releases/rc2/implementation.md)
- [تشغيل RC2 والنسخ الكامل والتعافي](docs/releases/rc2/operations.md)
- [بروتوكول تقييم بيانات حقيقية](docs/releases/rc2/evaluation.md)
- [دليل التشغيل السابق RC1](docs/operations.md)
- [بنية المؤسسات وحدود الثقة](docs/architecture.md)
- [دليل المعلم والأدوار](docs/user_guide.md)
- [النطاق التجاري وحدود التحقق](docs/commercial_release.md)
- [خطوات التنفيذ والرفع](docs/development_log.md)
- [البيانات والتراخيص](docs/dataset_report.md)
- [التقييم العلمي وحدوده](docs/evaluation_report.md)

لا تتضمن النسخة تدريب HTR معتمدًا، أو SSO/MFA، أو دفعًا محصّلًا، أو بنية موزعة، أو اعتمادًا قانونيًا/أمنيًا. يجب إكمال تقييم الجودة والأمن والبيانات والتشغيل الخاص بالعميل قبل إطلاق عام أو استخدام درجات AI دون مراجعة.

## الدفتر الشامل وAndroid

[افتح أحدث دفتر شامل في Colab](https://colab.research.google.com/github/alkhwarzmy/SmartGrade-AI/blob/main/notebooks/SmartGrade_Complete_Colab.ipynb) — تشغيل المؤسسات الفعلي، وتعدد الأسئلة، وجميع مصادر Flutter Android مضمنة. [دليل التشغيل](docs/complete_colab.md) · [Android](flutter_app/README.md).
