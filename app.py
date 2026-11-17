"""SmartGrade AI: a local-first, Arabic teacher review workspace."""

import os
import re
from pathlib import Path
import pandas as pd
import streamlit as st
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from config import DATA_DIR
from modules.schemas import ExamSpec, Criterion, GradeProposal
from modules.review import Store
from modules.mcq_grader import template, template_pdf, annotate, score_answers
from modules.preprocessing import png_bytes, decode_image
from modules.handwriting_ocr import recognize, ocr_status
from modules.essay_grader import propose_grade, provider_status, ProviderError
from modules.reports import frames, excel_bytes, csv_bytes

st.set_page_config(page_title="SmartGrade | مساحة التصحيح", page_icon="✦", layout="wide")
st.markdown(
    """
<style>
html,body,[class*="css"],.stApp {font-family:'Noto Sans Arabic',sans-serif;}
[data-testid="stMain"] {direction:rtl;}
[data-testid="stSidebar"] {direction:rtl;border-left:1px solid #dce5e8;background:#fff;}
.block-container {padding-top:2.2rem;padding-bottom:3rem;max-width:1440px;}
h1,h2,h3,p,label {text-align:right;}
h1 {font-size:2rem!important;font-weight:700!important;letter-spacing:-.6px;}
[data-testid="stMetric"] {background:#fff;border:1px solid #e1e8eb;border-radius:14px;padding:20px;}
[data-testid="stMetricLabel"] {color:#657781;}
[data-testid="stMetricValue"] {color:#123b40;}
[data-testid="stForm"] {background:white;border:1px solid #e0e8eb;border-radius:16px;padding:24px;}
[data-baseweb="tab-list"] {gap:22px;border-bottom:1px solid #dde6e9;}
.stButton>button,.stDownloadButton>button {border-radius:9px;min-height:42px;}
.hero {padding:27px 30px;background:#103e43;border-radius:18px;margin:14px 0 27px;color:#fff;}
.hero h2 {font-size:1.65rem;color:white;margin:0 0 10px;}
.hero p {color:#c8e2df;margin:0;line-height:1.9;}
.eyebrow {font-size:12px;color:#68d3bc;letter-spacing:2px;direction:ltr;text-align:right;}
.brand {font-size:25px;font-weight:700;color:#124a4c;margin:8px 0 2px;direction:ltr;text-align:right;}
.subbrand {font-size:12px;color:#74858d;margin-bottom:28px;}
.step {font-size:13px;color:#768a92;margin:16px 0 2px;}
footer {visibility:hidden;}
.st-key-mobile_nav {display:none;}
@media (max-width:768px) {
  [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"] {display:none!important;}
  .st-key-mobile_nav {display:block!important;}
  .block-container {padding:1.2rem 1rem 3rem;}
  h1 {font-size:1.6rem!important;}
  .hero {padding:22px 18px;}
}
</style>
""",
    unsafe_allow_html=True,
)


@st.cache_resource
def get_store(path):
    return Store(Path(path))


store = get_store(str(DATA_DIR))
STATUS = {"review": "بانتظار المراجعة", "approved": "معتمد", "failed": "فشلت المعالجة"}
STATE = {"single": "اختيار واحد", "blank": "فارغ", "multiple": "تظليل متعدد", "unclear": "غير واضح"}
PAGES = ["الاختبارات", "رفع الأوراق", "التصحيح والمراجعة", "النتائج والتصدير", "الإعدادات"]


def notify_error(exc):
    if isinstance(exc, ValidationError):
        st.error(" • ".join(error["msg"] for error in exc.errors(include_input=False)))
    elif isinstance(exc, SQLAlchemyError):
        st.error("تعذر حفظ التغيير؛ قد تكون البيانات تغيرت في جلسة أخرى. أعد تحميل الصفحة.")
    elif isinstance(exc, OSError):
        st.error("تعذر الوصول إلى الملف المحلي؛ تحقق من التخزين وأعد المحاولة.")
    else:
        st.error(str(exc))


def choose_exam(label="اختر الاختبار", key="exam"):
    exams = store.exams()
    if not exams:
        st.info("أنشئ اختبارك الأول من صفحة الاختبارات للبدء.")
        return None
    mapping = {e.id: e for e in exams}
    eid = st.selectbox(
        label,
        list(mapping),
        key=key,
        format_func=lambda x: (
            f"{mapping[x].title} · {'اختيارات' if mapping[x].kind == 'mcq' else 'مقالي'} · {x[:6]}"
        ),
    )
    return mapping[eid]


def downloads():
    st.markdown("#### قالب موحد، قراءة أوضح")
    st.caption("20 سؤالًا × 4 اختيارات. اطبع A4 رأسيًا، ظلل بالكامل، وأبقِ العلامات الأربع ظاهرة.")
    a, b, c = st.columns(3)
    a.download_button(
        "تنزيل القالب PDF", template_pdf(), "smartgrade-omr-v1.pdf", "application/pdf", width="stretch"
    )
    b.download_button(
        "تنزيل القالب PNG", png_bytes(template()), "smartgrade-omr-v1.png", "image/png", width="stretch"
    )
    c.download_button(
        "عينة اصطناعية للتجربة",
        png_bytes(template({i: ["ABCD"[i % 4]] for i in range(20)}, True)),
        "synthetic-omr-demo.png",
        "image/png",
        width="stretch",
    )
    st.caption("مفتاح العينة الاصطناعية: A B C D A B C D A B C D A B C D A B C D. ليست بيانات طلاب حقيقية.")


def exams_page():
    st.markdown(
        '<div class="hero"><div class="eyebrow">SMARTGRADE / TEACHER WORKSPACE</div>'
        "<h2>وقت أقل للتصحيح. مساحة أكبر للتعليم.</h2>"
        "<p>اجمع أوراق الاختبار، راجع الإجابات، واعتمد النتائج من مكان واحد.</p></div>",
        unsafe_allow_html=True,
    )
    exams, submissions = store.exams(), store.submissions()
    a, b, c, d = st.columns(4)
    a.metric("الاختبارات", len(exams))
    b.metric("الأوراق المستلمة", len(submissions))
    c.metric("بانتظار المراجعة", sum(s.status != "approved" for s in submissions))
    d.metric("النتائج المعتمدة", sum(s.status == "approved" for s in submissions))
    st.write("")
    new, existing, templates = st.tabs(["إنشاء اختبار", "اختباراتي", "قالب الإجابة"])
    with new:
        mode = st.radio("نوع الاختبار", ["اختيار من متعدد", "إجابة مقالية"], horizontal=True)
        with st.form("new_exam", clear_on_submit=False):
            title = st.text_input(
                "عنوان الاختبار", placeholder="مثال: اختبار العلوم — الفصل الأول", max_chars=150
            )
            if mode == "اختيار من متعدد":
                st.caption("قالب OMR v1 · 20 سؤالًا · تُحتسب الإجابة الصحيحة فقط، دون خصم للخطأ.")
                answer_key = st.text_area(
                    "مفتاح الإجابة بالترتيب",
                    value=" ".join("ABCD" * 5),
                    help="20 حرفًا من A أو B أو C أو D، تفصل بينها مسافات",
                )
                points = st.number_input(
                    "درجة السؤال الواحد", min_value=0.25, max_value=1000.0, value=1.0, step=0.25
                )
            else:
                question = st.text_area("نص السؤال", max_chars=10000)
                model_answer = st.text_area("الإجابة النموذجية", max_chars=20000)
                instructions = st.text_area("تعليمات المعلم", max_chars=5000)
                maximum = st.number_input(
                    "الدرجة الكاملة", min_value=0.5, max_value=20000.0, value=10.0, step=0.5
                )
                rubric = st.data_editor(
                    pd.DataFrame(
                        [
                            {"المعيار": "صحة المحتوى", "الدرجة القصوى": 4.0},
                            {"المعيار": "التفسير والأدلة", "الدرجة القصوى": 4.0},
                            {"المعيار": "الوضوح والترابط", "الدرجة القصوى": 2.0},
                        ]
                    ),
                    num_rows="dynamic",
                    hide_index=True,
                    width="stretch",
                    key="new_rubric",
                    column_config={
                        "الدرجة القصوى": st.column_config.NumberColumn(
                            min_value=0.25, max_value=1000.0, step=0.25
                        )
                    },
                )
            submitted = st.form_submit_button("إنشاء الاختبار", type="primary", width="stretch")
        if submitted:
            if mode == "اختيار من متعدد":
                spec = ExamSpec(
                    title=title,
                    kind="mcq",
                    answer_key=re.split(r"[\s,،;]+", answer_key.strip().upper()),
                    points_per_question=points,
                )
            else:
                criteria = [
                    Criterion(id=f"c{i + 1}", description=str(r["المعيار"]), maximum=r["الدرجة القصوى"])
                    for i, r in enumerate(rubric.to_dict("records"))
                    if pd.notna(r["المعيار"]) and str(r["المعيار"]).strip()
                ]
                spec = ExamSpec(
                    title=title,
                    kind="essay",
                    question=question,
                    model_answer=model_answer,
                    instructions=instructions,
                    maximum=maximum,
                    criteria=criteria,
                )
            store.create_exam(spec)
            st.session_state["_flash"] = "تم إنشاء الاختبار. انتقل إلى رفع الأوراق لبدء التصحيح."
            st.rerun()
    with existing:
        if not exams:
            st.info("ستظهر اختباراتك هنا بعد إنشاء أول اختبار.")
        for exam in exams:
            spec = ExamSpec.model_validate(exam.spec)
            with st.expander(
                f"{exam.title} · {'اختيارات' if exam.kind == 'mcq' else 'مقالي'} · {spec.maximum:g} درجة"
            ):
                if exam.kind == "mcq":
                    st.dataframe(
                        pd.DataFrame({"السؤال": range(1, 21), "الإجابة": spec.answer_key}), hide_index=True
                    )
                else:
                    st.write(spec.question)
                    st.write("الإجابة النموذجية: " + spec.model_answer)
                    st.dataframe(
                        pd.DataFrame(
                            [{"المعيار": c.description, "الحد الأعلى": c.maximum} for c in spec.criteria]
                        ),
                        hide_index=True,
                    )
                st.caption(
                    "للحفاظ على اتساق النتائج، أنشئ اختبارًا جديدًا عند تغيير مفتاح الإجابة أو المعايير."
                )
    with templates:
        downloads()


def upload_page():
    st.title("رفع أوراق الاختبار")
    st.caption("صورة واضحة هي بداية تصحيح موثوق.")
    exam = choose_exam(key="upload_exam")
    if not exam:
        return
    st.info(
        "اختيارات: ارفع قالب OMR v1 كاملًا وباتجاه رأسي."
        if exam.kind == "mcq"
        else "مقالي: ارفع صورة مقصوصة لإجابة سؤال واحد. ستراجع النص قبل اعتماد الدرجة."
    )
    file = st.file_uploader(
        "صورة الإجابة", type=["png", "jpg", "jpeg"], help="PNG أو JPEG · حتى 10 MB · حتى 16 مليون بكسل"
    )
    with st.form("upload", clear_on_submit=False):
        code = st.text_input("رمز الطالب", placeholder="مثال: ST-024 — استخدم رمزًا بدل الاسم", max_chars=100)
        submit = st.form_submit_button(
            "رفع وبدء المعالجة", type="primary", width="stretch", disabled=file is None
        )
    if submit:
        if not file:
            st.warning("اختر صورة الإجابة أولًا.")
        else:
            with st.spinner("جارٍ فحص الصورة ومعالجة الإجابات…"):
                sid = store.upload(
                    exam.id,
                    code,
                    file.getvalue(),
                    st.session_state.get("blank_threshold", 0.12),
                    st.session_state.get("marked_threshold", 0.55),
                )
            st.success(f"تم حفظ الورقة ({sid[:8]}) وهي بانتظار المراجعة. انتقل إلى التصحيح والمراجعة.")
    if exam.kind == "mcq":
        with st.expander("تحميل القالب وعينة للتجربة"):
            downloads()


def mcq_review(row, spec, reviewer):
    readings = row.original["readings"]
    unresolved = sum(r["state"] != "single" for r in readings)
    st.info(f"{unresolved} سؤالًا فارغًا أو متعددًا أو غير واضح. راجع الصورة قبل الاعتماد.")
    st.caption("الفارغ والتظليل المتعدد = صفر، ما لم يصحح المعلم الإجابة بعد مراجعة الصورة.")
    values = row.current["answers"]
    data = pd.DataFrame(
        [
            {
                "السؤال": i + 1,
                "حالة القراءة": STATE[r["state"]],
                "الإجابة المعتمدة": values[i] or "—",
                "مفتاح التصحيح": spec.answer_key[i],
            }
            for i, r in enumerate(readings)
        ]
    )
    with st.form(f"mcq_{row.id}_{row.revision}"):
        edited = st.data_editor(
            data,
            hide_index=True,
            width="stretch",
            disabled=["السؤال", "حالة القراءة", "مفتاح التصحيح"],
            column_config={
                "الإجابة المعتمدة": st.column_config.SelectboxColumn(
                    options=["—", "A", "B", "C", "D"], required=True
                )
            },
            key=f"answers_{row.id}_{row.revision}",
        )
        reason = st.text_input("ملاحظة المراجع / سبب التعديل", max_chars=1000)
        checked = st.checkbox("راجعت الصورة والحالات غير الواضحة وأوافق على اعتماد الإجابات")
        approve = st.form_submit_button("اعتماد النتيجة", type="primary", width="stretch")
    st.caption(
        f"الدرجة قبل التعديلات: {score_answers(values, spec.answer_key, spec.points_per_question):g} / {spec.maximum:g}"
    )
    if approve:
        answers = [None if x == "—" else x for x in edited["الإجابة المعتمدة"]]
        store.approve_mcq(row.id, answers, reviewer, reason, row.revision, checked)
        st.rerun()


def essay_review(row, spec, reviewer):
    st.markdown("#### 1. تدقيق نص الإجابة")
    st.write(spec.question)
    with st.form(f"text_{row.id}_{row.revision}"):
        text = st.text_area(
            "النص المستخدم للتصحيح",
            value=row.text,
            height=190,
            max_chars=30000,
            key=f"text_input_{row.id}_{row.revision}",
        )
        save = st.form_submit_button("حفظ النص")
    if save:
        store.save_text(row.id, text, reviewer, row.revision)
        st.session_state["_flash"] = "تم حفظ النص. الورقة بانتظار مراجعة الدرجات."
        st.rerun()
    if st.button(
        "استخراج النص بمحرك OCR المحلي", disabled=os.getenv("OCR_PROVIDER", "tesseract") != "tesseract"
    ):
        try:
            with st.spinner("جارٍ استخراج النص محليًا…"):
                extracted = recognize(store.image_path(row.image_name).read_bytes())
            store.save_text(row.id, extracted, reviewer, row.revision, source="ocr")
            st.session_state["_flash"] = "تم استخراج النص محليًا. راجعه وصحح الأخطاء قبل التصحيح."
            st.rerun()
        except ValueError:
            store.record_processing_failure(row.id, "ocr", row.revision)
            st.warning("تعذر استخراج نص صالح. أدخل النص يدويًا واحفظه؛ لم تُمنح درجة.")
    st.caption(ocr_status()[1])
    st.markdown("#### 2. اقتراح الدرجة ومراجعتها")
    consent = st.checkbox(
        "أوافق على إرسال النص والسؤال والمعايير للمزود الخارجي، وأعتمد تكلفة هذا الطلب",
        key=f"consent_{row.id}",
    )
    if st.button(
        "اقتراح درجات بالذكاء الاصطناعي",
        disabled=not (row.text.strip() and consent and all(provider_status().values())),
    ):
        try:
            with st.spinner("جارٍ اقتراح الدرجات…"):
                proposal = propose_grade(spec, row.text, consent=consent)
            store.save_proposal(row.id, proposal.model_dump(), row.revision)
            st.rerun()
        except ProviderError:
            store.record_processing_failure(row.id, "llm", row.revision)
            st.warning("فشل المزود أو أعاد مخرجات غير صالحة. راجع الإجابة يدويًا؛ لم تُمنح درجة.")
    if not all(provider_status().values()):
        st.caption("الاقتراح الآلي غير مفعّل. يمكنك إدخال الدرجات يدويًا وفق المعايير أدناه.")
    if row.proposal:
        st.info("الاقتراح الآلي متاح للمراجعة. الاقتباس الموجود في النص لا يثبت وحده صحة التقييم.")
    prior = row.approved or row.proposal or {"criteria": []}
    by_id = {g["criterion_id"]: g for g in prior["criteria"]}
    grades = []
    with st.form(f"essay_{row.id}_{row.revision}"):
        for c in spec.criteria:
            g = by_id.get(c.id, {})
            prefix = f"{row.id}_{row.revision}_{c.id}"
            with st.expander(f"{c.description} · {c.maximum:g} درجات", expanded=True):
                score = st.number_input(
                    "درجة المعيار",
                    min_value=0.0,
                    max_value=float(c.maximum),
                    value=float(g.get("score", 0)),
                    step=0.25,
                    key="score_" + prefix,
                )
                evidence = st.text_input(
                    "دليل حرفي من الإجابة (اختياري)",
                    value=g.get("evidence", ""),
                    max_chars=10000,
                    key="evidence_" + prefix,
                )
                rationale = st.text_input(
                    "تبرير الدرجة", value=g.get("rationale", ""), max_chars=5000, key="rationale_" + prefix
                )
                missing = st.text_input(
                    "العناصر الناقصة (إن وجدت)",
                    value=g.get("missing", ""),
                    max_chars=5000,
                    key="missing_" + prefix,
                )
                grades.append(
                    {
                        "criterion_id": c.id,
                        "score": score,
                        "evidence": evidence,
                        "rationale": rationale,
                        "missing": missing,
                    }
                )
        reason = st.text_input("ملاحظة المراجع / سبب التعديل", max_chars=1000)
        checked = st.checkbox("راجعت نص الإجابة وجميع المعايير وأوافق على الدرجات")
        approve = st.form_submit_button("اعتماد الدرجة المقالية", type="primary", width="stretch")
    if approve:
        proposal = GradeProposal(criteria=grades).model_dump()
        store.approve_essay(row.id, proposal, reviewer, reason, row.revision, checked)
        st.rerun()


def review_page():
    st.title("التصحيح والمراجعة")
    st.caption("راجع القراءة والدرجات قبل اعتماد النتائج. كل تعديل محفوظ في سجل المراجعة.")
    exam = choose_exam(key="review_exam")
    if not exam:
        return
    rows = store.submissions(exam.id)
    if not rows:
        st.info("لا توجد أوراق لهذا الاختبار. ابدأ برفع صورة من صفحة رفع الأوراق.")
        return
    mapping = {r.id: r for r in rows}
    sid = st.selectbox(
        "ورقة الطالب",
        list(mapping),
        key="review_submission",
        format_func=lambda x: f"{mapping[x].student_code} · {STATUS[mapping[x].status]} · {x[:8]}",
    )
    row = mapping[sid]
    spec = ExamSpec.model_validate(exam.spec)
    reviewer = st.text_input("اسم المراجع", value="المعلم", max_chars=100)
    if row.status == "approved":
        st.success(f"نتيجة معتمدة: {row.total:g} / {spec.maximum:g}")
    left, right = st.columns([1, 1.3], gap="large")
    with left:
        st.markdown("#### صورة الإجابة")
        image = decode_image(store.image_path(row.aligned_name or row.image_name).read_bytes())
        if spec.kind == "mcq":
            image = annotate(image, row.original["readings"])
        st.image(image, width="stretch")
        if row.original.get("ocr_text"):
            with st.expander("نص OCR الأصلي"):
                st.text(row.original["ocr_text"])
    with right:
        if spec.kind == "mcq":
            mcq_review(row, spec, reviewer)
        else:
            essay_review(row, spec, reviewer)
    with st.expander("سجل المراجعة"):
        for event in store.history(sid):
            st.write(f"{event.created_at[:19]} · {event.reviewer} · {event.action}")
            if event.reason:
                st.caption(event.reason)
            with st.expander(f"تفاصيل التغيير {event.id[:6]}"):
                st.json({"قبل": event.before, "بعد": event.after}, expanded=False)
    with st.expander("حذف بيانات هذه الورقة"):
        confirmed = st.checkbox("أريد حذف الصورة والنص والدرجات وسجل المراجعة نهائيًا")
        if st.button("حذف التسليم", disabled=not confirmed):
            store.delete_submission(sid, row.revision)
            st.rerun()


def results_page():
    st.title("النتائج والتصدير")
    st.caption("تتضمن ملفات التصدير النتائج التي اعتمدها المعلم فقط.")
    exam = choose_exam(key="result_exam")
    if not exam:
        return
    rows = store.submissions(exam.id)
    summary, details = frames(store, exam.id)
    a, b, c = st.columns(3)
    a.metric("نتائج معتمدة", len(summary))
    b.metric("تحتاج مراجعة", sum(r.status != "approved" for r in rows))
    c.metric("متوسط النسبة", f"{summary['النسبة %'].mean():.1f}%" if not summary.empty else "—")
    if summary.empty:
        st.info("لا توجد نتائج معتمدة بعد. أكمل المراجعة واعتمد أول ورقة.")
        return
    st.dataframe(summary, hide_index=True, width="stretch")
    a, b = st.columns(2)
    a.download_button(
        "تصدير Excel مع تفاصيل التصحيح",
        excel_bytes(summary, details),
        "smartgrade-results.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        width="stretch",
    )
    b.download_button("تصدير CSV", csv_bytes(summary), "smartgrade-results.csv", "text/csv", width="stretch")
    with st.expander("تفاصيل كل سؤال / معيار"):
        st.dataframe(details, hide_index=True, width="stretch")
    st.markdown("#### توزيع النتائج المعتمدة")
    bins = pd.cut(
        summary["النسبة %"], [-0.01, 25, 50, 75, 100], labels=["0–25%", "25–50%", "50–75%", "75–100%"]
    )
    st.bar_chart(bins.value_counts(sort=False))
    if exam.kind == "mcq":
        st.markdown("#### نسبة الإجابة الصحيحة لكل سؤال")
        grouped = details.groupby("السؤال / المعيار", sort=False).agg(
            {"الدرجة": "sum", "الدرجة الكاملة": "sum"}
        )
        st.bar_chart((100 * grouped["الدرجة"] / grouped["الدرجة الكاملة"]).rename("نسبة الصحة %"))


def settings_page():
    st.title("الإعدادات والخصوصية")
    st.markdown("#### محركات الذكاء الاصطناعي")
    available, message = ocr_status()
    st.info(message)
    st.write(
        "مزود OCR المحدد: "
        + ("Tesseract محلي" if os.getenv("OCR_PROVIDER", "tesseract") == "tesseract" else "إدخال يدوي")
    )
    for label, enabled in provider_status().items():
        st.write(("✓ " if enabled else "○ ") + label)
    st.caption(
        "الإعدادات من ملف .env وفق .env.example. المفاتيح لا تظهر في الواجهة. لا يتم إرسال طلب عند فتح التطبيق."
    )
    st.markdown("#### حدود اكتشاف التظليل")
    st.caption("تطبق على الصور الجديدة فقط. القيم الافتراضية تجريبية وتحتاج معايرة على أوراقك الحقيقية.")
    st.slider("أقصى شدة للفراغ / بداية المنطقة غير الواضحة", 0.01, 0.40, 0.12, 0.01, key="blank_threshold")
    st.slider("أدنى شدة لتأكيد التظليل", 0.45, 0.95, 0.55, 0.01, key="marked_threshold")
    st.markdown("#### حفظ البيانات وحذفها")
    st.write(
        "تُحفظ الصور والنصوص والدرجات محليًا حتى حذفها من صفحة المراجعة. لا يوجد حذف تلقائي في هذه النسخة. "
        "يمكن حذف التسليم كاملًا مع سجل مراجعته. النسخ الاحتياطية مسؤولية المشغّل ويجب حذفها بصورة مستقلة."
    )
    st.write(
        "التطبيق مخصص لمعلم موثوق على جهاز محلي. استخدامه لمؤسسة أو على الإنترنت يتطلب مصادقة وصلاحيات وعزل البيانات."
    )
    st.markdown("#### حدود النسخة")
    st.write(
        "قالب OMR واحد، صورة PNG/JPEG واحدة في كل عملية، سؤال مقالي واحد لكل اختبار مقالي. "
        "لم تُعتمد جودة التعرف على خط اليد أو اتفاق التصحيح الآلي على بيانات طلاب حقيقية. "
        "التصدير المتاح Excel وCSV؛ تصدير النتائج إلى PDF مؤجل."
    )


def sync_navigation(source, target):
    st.session_state[target] = st.session_state[source]


with st.sidebar:
    st.markdown(
        '<div class="brand">✦ SmartGrade</div><div class="subbrand">مساحة المعلم للتصحيح والمراجعة</div>',
        unsafe_allow_html=True,
    )
    page = st.radio(
        "مساحة العمل",
        PAGES,
        label_visibility="collapsed",
        key="navigation",
        on_change=sync_navigation,
        args=("navigation", "mobile_navigation"),
    )
    st.divider()
    st.caption("01 / رفع الورقة\n\n02 / مراجعة التصحيح\n\n03 / اعتماد النتيجة")
    st.divider()
    st.caption("نسخة أولية · تخزين محلي\n\nقرار الاعتماد دائمًا للمعلم")

with st.container(key="mobile_nav"):
    st.selectbox(
        "انتقل إلى",
        PAGES,
        index=PAGES.index(page),
        key="mobile_navigation",
        on_change=sync_navigation,
        args=("mobile_navigation", "navigation"),
    )

try:
    {
        PAGES[0]: exams_page,
        PAGES[1]: upload_page,
        PAGES[2]: review_page,
        PAGES[3]: results_page,
        PAGES[4]: settings_page,
    }[page]()
    if "_flash" in st.session_state:
        st.success(st.session_state.pop("_flash"))
except (ValueError, OSError, SQLAlchemyError) as exc:
    notify_error(exc)
