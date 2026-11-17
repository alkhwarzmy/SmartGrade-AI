# API إصدار المؤسسات

الأصل المحلي `http://127.0.0.1:8000`؛ خلف Caddy استخدم `https://your-domain/api`. الحسابات والمؤسسات يجهزها مشغل الخدمة، ولا توجد API عامة للتسجيل أو تغيير الترخيص.

## تسجيل الدخول

`POST /auth/login`

```json
{"organization":"my-school","email":"user@example.com","password":"your-own-password"}
```

يعيد `access_token` و`token_type=bearer` و`expires_in` و`must_change_password`. استخدم `Authorization: Bearer <token>` لكل مسار محمي. لا تضع الرمز في URL أو سجلاتك. إذا كان تغيير كلمة المرور مطلوبًا، لا يُسمح بمساحة العمل حتى `POST /auth/change-password` ثم تسجيل دخول جديد. مسار التغيير يتلقى `current_password` و`new_password`، ويلغي كل جلسات المستخدم. `POST /auth/logout` يلغي الرمز الحالي.

## المسارات

| المسار | الصلاحية والسلوك |
|---|---|
| GET /health، /ready | صحة عامة دون بيانات حسابات |
| GET /auth/me | الحساب الحالي؛ لا كلمة مرور أو hash |
| GET /organization/usage | سعة المؤسسة وحدودها وخطتها |
| GET /exams | المؤسسة الحالية؛ المشاهد يحصل على بيانات عنوان/نوع دون مفتاح أو rubric |
| POST /exams | مدير/معلم؛ ExamSpec متحقق منه |
| POST /exams/{id}/submissions | multipart: student_code وfile، صورة PNG/JPEG حتى10MB |
| GET /exams/{id}/submissions | مدير/معلم كل الحالات؛ مشاهد المعتمد فقط |
| GET /submissions/{id}، /image | مدير/معلم فقط؛ لا معرف صورة مباشر أو مسار من العميل |
| PUT /submissions/{id}/text | text وrevision؛ يلغي الاعتماد السابق |
| POST /submissions/{id}/ocr | تشغيل OCR محلي وحفظه؛ body يتضمن revision الحالي فقط |
| POST /submissions/{id}/proposal | revision وconsent_to_external_processing_and_cost=true؛ سياسة المؤسسة والمشغل مطلوبة |
| POST /submissions/{id}/approve-mcq | answers بطول20، revision، reviewed=true، reason اختياري |
| POST /submissions/{id}/approve-essay | proposal منظّم لكل معيار، revision، reviewed=true، reason اختياري |
| DELETE /submissions/{id}?revision=N | مدير فقط؛ حذف الورقة الحالية وصورها وتاريخ درجاتها |
| GET /exams/{id}/report.xlsx أو .csv أو .pdf | المعتمد فقط، ضمن المؤسسة والصلاحية؛ PDF حتى2000 نتيجة |
| GET/POST /admin/users | مدير؛ إنشاء بريد واسم ودور وtemporary_password، مع تغيير إلزامي لاحقًا |
| PATCH /admin/users/{id} | مدير؛ role وactive، وإلغاء جلسات العضو؛ حماية آخر مدير |
| POST /admin/users/{id}/reset-password | مدير؛ temporary_password وإلغاء الجلسات |
| PUT /admin/ai-policy | مدير؛ enabled، دون إعداد مفتاح أو تنفيذ طلب مدفوع |
| GET /admin/audit | مدير؛ آخر200 حدث وفحص تسلسل البصمات |

المؤسسة لا تقبل من header أو جسم عملية التصحيح؛ تُشتق من الجلسة. معرف أجنبي/غير موجود لا يكشف سجلات مؤسسة أخرى. لا يتوفر اختيار reviewer أوrole من العميل لاعتماد الدرجات.

## حالات الاستجابة

- 401: لا جلسة صالحة أو بيانات دخول غير صحيحة/مؤسسة غير متاحة.
- 403: صلاحية غير كافية، أو تغيير كلمة المرور مطلوب، أو سياسة إرسال المؤسسة غير معتمدة.
- 409: رقم مراجعة قديم أو تعارض حفظ.
- 413: تجاوز الحجم الفعلي، حتى مع غياب Content-Length.
- 422: حقول غير صحيحة، دون إعادة قيمة كلمة المرور في الأخطاء.
- 429: تقييد محاولات دخول أو امتلاء حصة منتج؛ تقييد الدخول يعيد Retry-After.
- 400: صورة/سؤال/قيمة أعمال غير صالحة أو معرف غير موجود داخل المؤسسة.

كل الردود عليها no-store. لا يوجد CORS عام أو auth cookie للـAPI؛ إذا بنيت عميلًا آخر فاحفظ الرمز بطريقة ملائمة، واستخدم TLS. التوثيق التفاعلي اختياري عبر `SMARTGRADE_API_DOCS=true` في بيئة موثوقة.
