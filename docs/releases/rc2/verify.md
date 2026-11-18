# إعادة التحقق

من جذر المستودع مع المتطلبات المقفلة:

```bash
python -m pip install --require-hashes -r requirements.lock
python -m pytest -q
python -m ruff check .
python -m scripts.build_colab_notebook
python -m scripts.validate_colab_notebook --execute-smoke
python -m scripts.upgrade_smoke
```

المتصفح يحتاج Playwright وChromium، والتجربة المحلية المشفرة تستخرج Caddy من صورته أو تقبل `--caddy` لمساره:

```bash
python -m pip install -r requirements-browser.txt
python -m scripts.commercial_browser_smoke
python -m scripts.staging_smoke
```

صورة Docker قبل فحصها:

```bash
docker build -t smartgrade:1.0.0-rc2 .
python -m scripts.docker_smoke
python -m scripts.acceptance_load
```

عند وجود CA مؤسسية للبناء، استخدم BuildKit secret المناسب للمضيف؛ لا تُعطّل TLS ولا تُضمّن شهادة الشبكة الخاصة في الصورة. يطلب اختبار الحمل 2 CPU و2 GiB ويتحقق من الحد المشاهد أيضًا. الرمز2 يعني أن النتائج الوظيفية قد تنجح لكن قبول الذاكرة غير مثبت؛ افحص JSON ولا تعتبر غياب أخطاء HTTP نجاحًا لكل شروط السعة.

أدوات Docker تنشئ بيانات اصطناعية وحاويات وVolumes مؤقتة تخصها وتزيلها. لا تختبر خادم عميل ولا تحتاج خدمات AI مدفوعة. أدوات الترقية تحتاج Git والكود التاريخي. النتائج التشغيلية تحت `outputs/` و`work/` مستثناة من Git؛ الأدلة المنشورة المختارة تحت `evidence/` ولا تحوي بيانات حقيقية أو جلسات أو مفاتيح.

فحص Python وSBOM:

```bash
pip-audit --no-deps --disable-pip -r requirements-runtime.lock --format json --output python-audit.json
cyclonedx-py requirements requirements-runtime.lock --of JSON -o sbom.cdx.json
```

حدود المصدر والمقاييس والقبول في `implementation.md`. Workflow في `.github/workflows/verification.yml` يكرر الفحوص ويوقف بوابة نظام الصورة عند ثغرات HIGH/CRITICAL لها إصلاح متاح، مع حفظ التقرير الكامل. الثغرات التي لم يتوفر لها إصلاح تبقى في التقرير للمراجعة؛ اجتياز البوابة لا يثبت خلو الصورة من كل الثغرات.
