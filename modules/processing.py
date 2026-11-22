"""Stable, privacy-safe processing diagnostics; never serialize upstream error text."""
MESSAGES = {
    "external_disabled": "الخدمات الخارجية معطلة لدى المشغّل؛ فعّل ALLOW_EXTERNAL_AI عند الرغبة باستخدامها.",
    "paid_api_disabled": "استخدام API المدفوع معطل؛ يلزم تفعيل ALLOW_PAID_API من المشغّل.",
    "provider_key_missing": "مفتاح مزود التصحيح غير مضبوط؛ اضبط LLM_API_KEY لدى المشغّل.",
    "provider_model_missing": "اسم نموذج التصحيح غير مضبوط؛ اضبط LLM_MODEL لدى المشغّل.",
    "provider_url_invalid": "عنوان المزود غير صالح؛ استخدم LLM_BASE_URL من نوع HTTPS دون بيانات اعتماد أو استعلام.",
    "provider_options_invalid": "إعدادات طلب النموذج غير صالحة؛ راجع حد المخرجات واسم معامل التوكنات والحرارة.",
    "consent_required": "وافق على إرسال الإجابات والتكلفة قبل طلب التصحيح.",
    "organization_ai_disabled": "سياسة المؤسسة لا تسمح بالتصحيح الخارجي؛ يستطيع المدير تعديلها.",
    "source_unsaved": "النص الظاهر غير محفوظ؛ احفظه قبل طلب التصحيح أو اعتماد الدرجة.",
    "answers_unsaved": "إجابات الأسئلة غير محفوظة أو تغيرت؛ احفظ التوزيع قبل التصحيح.",
    "answers_empty": "لا توجد إجابة محفوظة غير فارغة؛ أدخل الإجابة واحفظها أو اعتمد الصفر يدويًا بعد المراجعة.",
    "input_invalid": "الاختبار أو نص الإجابة غير صالح للتصحيح.",
    "provider_auth": "رفض المزود بيانات اعتماده؛ راجع مفتاح المزود وصلاحياته. حسابك في التطبيق ما زال صالحًا.",
    "provider_quota": "رصيد أو حصة مزود التصحيح غير متاحة؛ راجع الفوترة والحصة لدى المزود.",
    "provider_rate_limit": "المزود يحد الطلبات مؤقتًا؛ انتظر قبل إعادة الطلب. لم تُعد المحاولة تلقائيًا.",
    "provider_timeout": "انتهت مهلة مزود التصحيح؛ لم يصل اقتراح صالح. تحقق من الحالة قبل إعادة طلب مدفوع.",
    "provider_connection": "تعذر الاتصال بمزود التصحيح؛ تحقق من الشبكة وعنوانه.",
    "provider_unavailable": "مزود التصحيح غير متاح مؤقتًا؛ احتُفظ بالدرجات المحفوظة.",
    "provider_context_limit": "تجاوز الطلب سعة سياق النموذج؛ قلل النص أو المعايير في الطلب أو اختر نموذجًا بسياق أكبر.",
    "provider_parameters": "معاملات الطلب غير مدعومة لدى النموذج؛ راجع LLM_TOKEN_PARAMETER وحد المخرجات.",
    "provider_truncated": "انتهى حد المخرجات قبل اكتمال التصحيح؛ ارفع LLM_MAX_OUTPUT_TOKENS أو قلل حجم الطلب.",
    "provider_refused": "لم يُرجع المزود تصحيحًا لهذا الطلب؛ راجع الإجابات يدويًا.",
    "provider_response_large": "تجاوز رد المزود حد القراءة المسموح؛ لم يُحفظ اقتراح جزئي.",
    "provider_invalid_response": "رد المزود غير مكتمل أو ليس JSON صالحًا؛ لم يُحفظ اقتراح.",
    "provider_invalid_grade": "رد المزود لا يطابق جميع الأسئلة والمعايير أو حدود الدرجات؛ لم يُحفظ اقتراح.",
    "provider_evidence": "اقتبس المزود دليلًا غير موجود في إجابة السؤال؛ لم يُحفظ اقتراح.",
    "provider_missing_evidence": "منح المزود درجة موجبة دون دليل من الإجابة؛ لم يُحفظ اقتراح.",
    "provider_failed": "تعذر الحصول على اقتراح صالح؛ احتُفظ بالإجابات والدرجات المحفوظة.",
    "ocr_disabled": "محرك OCR غير مفعل؛ اضبط OCR_PROVIDER=tesseract أو أدخل النص يدويًا.",
    "ocr_unavailable": "محرك OCR العربي غير متاح؛ ثبّت Tesseract وحزمة ara أو أدخل النص يدويًا.",
    "ocr_timeout": "انتهت مهلة OCR المحلي؛ أدخل النص يدويًا أو جرّب صورة أوضح.",
    "ocr_empty": "لم يُستخرج نص؛ راجع جودة الصورة أو أدخل الإجابات يدويًا.",
    "ocr_text_too_large": "تجاوز النص المستخرج حد 30000 حرف؛ راجع محتوى الصورة.",
    "ocr_failed": "فشل استخراج النص محليًا؛ احتُفظ بالنص والدرجات المحفوظة.",
    "stale_revision": "تغيرت الورقة أثناء المعالجة؛ لم تُستبدل تعديلات المعلم.",
    "job_permission_revoked": "توقفت المهمة بعد تغير صلاحية صاحبها.",
    "storage_quota": "لا توجد سعة تخزين كافية لإكمال المهمة.",
    "time_limit_120s": "تجاوزت المهمة حد المعالجة الزمني.",
    "worker_interrupted_retry_required": "توقفت عملية العامل أثناء المهمة؛ أعد المحاولة بعد التحقق من تشغيله.",
    "processing_failed_review_inputs": "تعذرت معالجة المهمة؛ راجع مدخلاتها وحالة العامل.",
    "processing_interrupted": "لم تكتمل المهمة؛ راجع حالة العامل.",
}
PROVIDER_ATTEMPTS = {code for code in MESSAGES if code.startswith("provider_")} - {
    "provider_key_missing", "provider_model_missing", "provider_url_invalid", "provider_options_invalid",
}
OCR_CODES = {code for code in MESSAGES if code.startswith("ocr_")}


def safe_code(code, fallback="processing_failed_review_inputs"):
    return code if code in MESSAGES else fallback


def message_for(code):
    return MESSAGES[safe_code(code)]


class ProcessingError(ValueError):
    def __init__(self, code="processing_failed_review_inputs"):
        self.code = safe_code(code)
        self.revision = None
        super().__init__(message_for(self.code))
