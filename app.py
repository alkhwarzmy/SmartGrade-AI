"""SmartGrade AI: a local-first, Arabic teacher review workspace."""

import os
import re
from pathlib import Path
from uuid import uuid4
import pandas as pd
import streamlit as st
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from config import DATA_DIR
from modules.schemas import ExamSpec, Criterion, EssayQuestion, GradeProposal, EssayGradeProposal, answer_texts
from modules.review import Store
from modules.mcq_grader import template, template_pdf, annotate, score_answers
from modules.preprocessing import png_bytes, decode_image, prepare_image
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


MODE = os.getenv("SMARTGRADE_MODE", "commercial")
if MODE not in {"commercial", "demo"}:
    st.error("SMARTGRADE_MODE يجب أن يكون commercial أو demo")
    st.stop()
COMMERCIAL = MODE == "commercial"
PRINCIPAL = None
if COMMERCIAL:
    from modules.auth import AuthService
    from modules.workspace import Workspace
    from modules.accounts_ui import sign_in, clear_session, account_page, organization_page, ROLES

    @st.cache_resource
    def get_identity(path):
        return AuthService(Path(path))

    identity = get_identity(str(DATA_DIR))
    PRINCIPAL = sign_in(identity)
    store = Workspace(identity, st.session_state["auth_token"])
else:
    store = get_store(str(DATA_DIR))
    st.warning("نسخة تجريبية بدون حسابات؛ استخدم بيانات اصطناعية فقط.")
STATUS = {"review": "بانتظار المراجعة", "approved": "معتمد", "failed": "فشلت المعالجة"}
STATE = {"single": "اختيار واحد", "blank": "فارغ", "multiple": "تظليل متعدد", "unclear": "غير واضح"}
PAGES = ["الاختبارات", "رفع الأوراق", "التصحيح والمراجعة", "النتائج والتصدير", "الإعدادات"]
if COMMERCIAL:
    if PRINCIPAL.role == "viewer":
        PAGES = ["النتائج والتصدير"]
    if PRINCIPAL.role == "admin":
        PAGES.append("إدارة المؤسسة")
    PAGES.append("حسابي")


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
    page = 1
    if COMMERCIAL and store.counts()["exams"] > 50:
        page = st.number_input(
            "صفحة الاختبارات",
            min_value=1,
            max_value=(store.counts()["exams"] + 49) // 50,
            step=1,
            key=key + "_page",
        )
    exams = store.exams(limit=50, offset=(page - 1) * 50) if COMMERCIAL else store.exams()
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


def new_essay_question():
    return {
        "id": uuid4().hex, "question": "", "model_answer": "", "instructions": "", "maximum": 10.0,
        "rubric": [
            {"المعيار": "صحة المحتوى", "الدرجة القصوى": 4.0},
            {"المعيار": "التفسير والأدلة", "الدرجة القصوى": 4.0},
            {"المعيار": "الوضوح والترابط", "الدرجة القصوى": 2.0},
        ],
    }


def essay_exam_fields():
    drafts = st.session_state.setdefault("new_essay_questions", [new_essay_question()])
    generation = st.session_state.get("essay_editor_generation", 0)
    st.caption(f"عدد الأسئلة: {len(drafts)} · أضف أو احذف الأسئلة حسب حاجتك.")
    remove = None
    values = []
    for number, draft in enumerate(drafts, 1):
        prefix = f"newq_{draft['id']}_{generation}"
        with st.expander(f"السؤال {number}", expanded=True):
            question = st.text_area("نص السؤال", value=draft["question"], max_chars=10000, key=prefix + "_text")
            model = st.text_area("الإجابة النموذجية", value=draft["model_answer"], max_chars=20000, key=prefix + "_model")
            instructions = st.text_area("تعليمات المعلم", value=draft["instructions"], max_chars=5000, key=prefix + "_instructions")
            maximum = st.number_input("درجة السؤال الكاملة", min_value=0.5, max_value=20000.0,
                                      value=float(draft["maximum"]), step=0.5, key=prefix + "_maximum")
            seed = st.session_state.setdefault(prefix + "_seed", draft["rubric"])
            rubric = st.data_editor(pd.DataFrame(seed), num_rows="dynamic", hide_index=True, width="stretch",
                key=prefix + "_rubric", column_config={"الدرجة القصوى": st.column_config.NumberColumn(
                    min_value=0.25, max_value=1000.0, step=0.25)})
            if st.form_submit_button(f"حذف السؤال {number}", disabled=len(drafts) == 1):
                remove = draft["id"]
            values.append({"id": draft["id"], "question": question, "model_answer": model,
                           "instructions": instructions, "maximum": maximum, "rubric": rubric.to_dict("records")})
    add = st.form_submit_button("إضافة سؤال آخر")
    st.session_state["new_essay_questions"] = values
    if add or remove:
        st.session_state["new_essay_questions"] = (
            [q for q in values if q["id"] != remove] if remove else values + [new_essay_question()]
        )
        st.session_state["essay_editor_generation"] = generation + 1
        for key in list(st.session_state):
            if key.startswith("newq_") and key.endswith("_seed"):
                del st.session_state[key]
        st.rerun()
    return values


def exams_page():
    st.markdown(
        '<div class="hero"><div class="eyebrow">SMARTGRADE / TEACHER WORKSPACE</div>'
        "<h2>وقت أقل للتصحيح. مساحة أكبر للتعليم.</h2>"
        "<p>اجمع أوراق الاختبار، راجع الإجابات، واعتمد النتائج من مكان واحد.</p></div>",
        unsafe_allow_html=True,
    )
    counts = store.counts()
    page = 1
    if counts["exams"] > 50:
        page = st.number_input(
            "صفحة قائمة الاختبارات", min_value=1, max_value=(counts["exams"] + 49) // 50, step=1
        )
    exams = store.exams(limit=50, offset=(page - 1) * 50)
    a, b, c, d = st.columns(4)
    a.metric("الاختبارات", counts["exams"])
    b.metric("الأوراق المستلمة", counts["submissions"])
    c.metric("بانتظار المراجعة", counts["review"])
    d.metric("النتائج المعتمدة", counts["approved"])
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
                questions = essay_exam_fields()
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
                parsed_questions = []
                for q in questions:
                    criteria = [
                        Criterion(id=f"c{i + 1}", description=str(r["المعيار"]), maximum=r["الدرجة القصوى"])
                        for i, r in enumerate(q["rubric"])
                        if pd.notna(r["المعيار"]) and str(r["المعيار"]).strip()
                    ]
                    parsed_questions.append(EssayQuestion(
                        **{k: v for k, v in q.items() if k != "rubric"}, criteria=criteria))
                spec = ExamSpec(title=title, kind="essay", questions=parsed_questions)
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
                    st.caption(f"عدد الأسئلة: {len(spec.essay_questions())}")
                    for number, q in enumerate(spec.essay_questions(), 1):
                        st.markdown(f"**السؤال {number} · {q.maximum:g} درجة**")
                        st.write(q.question)
                        st.write("الإجابة النموذجية: " + q.model_answer)
                        st.dataframe(pd.DataFrame([
                            {"المعيار": c.description, "الحد الأعلى": c.maximum} for c in q.criteria
                        ]), hide_index=True)
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
    prepared = None
    if file:
        prepared = file.getvalue()
        if st.checkbox("تحسين الصورة قبل الرفع"):
            rotation = st.selectbox(
                "تدوير الصورة عكس عقارب الساعة", [0, 90, 180, 270], format_func=lambda x: f"{x}°"
            )
            horizontal = st.slider("القص الأفقي %", 0, 100, (0, 100))
            vertical = st.slider("القص الرأسي %", 0, 100, (0, 100))
            correction = st.slider("تصحيح الميل الدقيق بالدرجات", -15, 15, 0)
            prepared = prepare_image(prepared, rotation + correction, horizontal, vertical)
            if COMMERCIAL:
                from modules.private_artifacts import image_html

                st.markdown(image_html(decode_image(prepared)), unsafe_allow_html=True)
            else:
                st.image(decode_image(prepared), width="stretch")
            st.caption("أبقِ علامات OMR الأربع ظاهرة؛ المعاينة لا تعتمد نتيجة تلقائيًا.")
        if COMMERCIAL:
            from uuid import uuid4

            st.session_state.setdefault("upload_attempt", uuid4().hex)
            if st.button("بدء محاولة جديدة للورقة نفسها"):
                st.session_state["upload_attempt"] = uuid4().hex
                st.info("سيُحفظ الرفع التالي كمحاولة جديدة مقصودة.")
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
                options = {}
                if COMMERCIAL:
                    import hashlib

                    key = hashlib.sha256(
                        (
                            exam.id
                            + code
                            + hashlib.sha256(prepared).hexdigest()
                            + st.session_state["upload_attempt"]
                        ).encode()
                    ).hexdigest()
                    options["idempotency_key"] = key
                sid = store.upload(
                    exam.id,
                    code,
                    prepared,
                    st.session_state.get("blank_threshold", 0.12),
                    st.session_state.get("marked_threshold", 0.55),
                    **options,
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


def essay_answer_fields(row, spec, reviewer):
    saved = answer_texts(spec, row.text, row.current)
    key = "question_answers_draft_" + row.id
    draft = st.session_state.get(key)
    if draft is None or (draft["revision"] != row.revision and draft["answers"] == draft["base"]):
        draft = {"revision": row.revision, "answers": dict(saved), "base": dict(saved)}
        st.session_state[key] = draft
    st.markdown("#### 2. إجابة كل سؤال")
    st.caption("وزّع النص المدقق على الأسئلة أو أدخل الإجابات يدويًا. السؤال دون إجابة يُراجع بصفر. تغيير النص المصدر يلغي التوزيع السابق.")
    for number, q in enumerate(spec.questions, 1):
        widget = f"answer_{row.id}_{row.revision}_{q.id}"
        def keep_answer(question_id=q.id, widget_key=widget):
            st.session_state[key]["answers"][question_id] = st.session_state[widget_key]
        st.write(f"السؤال {number}: {q.question}")
        draft["answers"][q.id] = st.text_area(f"إجابة السؤال {number}", value=draft["answers"][q.id],
                                            max_chars=30000, key=widget, on_change=keep_answer)
    if draft["revision"] != row.revision:
        st.warning("تغيرت الورقة في جلسة أخرى. مسودة إجاباتك محفوظة؛ حمّل الإجابات الأحدث قبل الحفظ.")
        if st.button("تحميل إجابات الأسئلة المحفوظة الأحدث"):
            del st.session_state[key]
            for widget_key in list(st.session_state):
                if widget_key.startswith(f"answer_{row.id}_"):
                    del st.session_state[widget_key]
            st.rerun()
    if st.button("حفظ إجابات الأسئلة"):
        changed = store.save_answers(row.id, draft["answers"], reviewer, draft["revision"])
        fresh = store.submission(row.id)
        st.session_state[key] = {"revision": fresh.revision, "answers": dict(draft["answers"]), "base": dict(draft["answers"])}
        st.session_state["_flash"] = "حُفظت إجابات الأسئلة. راجع الدرجات ثم اعتمد مجموع الاختبار." if changed else "الإجابات لم تتغير؛ بقيت الدرجات كما هي."
        st.rerun()
    mapping_saved = len(spec.questions) == 1 or "essay_answers" in row.current
    ready = mapping_saved and draft["answers"] == saved and draft["revision"] == row.revision
    if not ready:
        st.info("احفظ إجابات الأسئلة قبل اقتراح الدرجات أو اعتمادها.")
    return ready


def criterion_grade_fields(row, question, prior, *, multiple):
    by_id = {g["criterion_id"]: g for g in prior.get("criteria", [])}
    grades = []
    for c in question.criteria:
        g = by_id.get(c.id, {})
        suffix = f"{question.id}_{c.id}" if multiple else c.id
        prefix = f"{row.id}_{row.revision}_{suffix}"
        with st.expander(f"{c.description} · {c.maximum:g} درجات", expanded=True):
            score = st.number_input("درجة المعيار", min_value=0.0, max_value=float(c.maximum),
                                    value=float(g.get("score", 0)), step=0.25, key="score_" + prefix)
            evidence = st.text_input("دليل حرفي من الإجابة (اختياري)", value=g.get("evidence", ""),
                                     max_chars=10000, key="evidence_" + prefix)
            rationale = st.text_input("تبرير الدرجة", value=g.get("rationale", ""), max_chars=5000, key="rationale_" + prefix)
            missing = st.text_input("العناصر الناقصة (إن وجدت)", value=g.get("missing", ""), max_chars=5000, key="missing_" + prefix)
            grades.append({"criterion_id": c.id, "score": score, "evidence": evidence, "rationale": rationale, "missing": missing})
    return grades


def essay_review(row, spec, reviewer):
    notice = st.session_state.pop("_processing_notice_" + row.id, None)
    if notice:
        st.warning(notice)
    st.markdown("#### 1. تدقيق نص الإجابة")
    if spec.questions:
        st.caption(f"اختبار مقالي من {len(spec.questions)} سؤال · الدرجة الكاملة {spec.maximum:g}")
    else:
        st.write(spec.question)
    draft = "essay_draft_" + row.id
    base_revision = draft + "_revision"
    base_text = draft + "_base_text"
    widget = f"text_input_{row.id}_{row.revision}"
    if draft not in st.session_state:
        st.session_state[draft] = row.text
        st.session_state[base_revision] = row.revision
        st.session_state[base_text] = row.text
    elif st.session_state[base_revision] != row.revision and st.session_state[draft] == st.session_state.get(
        base_text, row.text
    ):
        st.session_state[draft] = row.text
        st.session_state[base_revision] = row.revision
        st.session_state[base_text] = row.text

    def keep_draft():
        st.session_state[draft] = st.session_state[widget]

    text = st.text_area(
        "النص المستخدم للتصحيح",
        value=st.session_state[draft],
        height=190,
        max_chars=30000,
        key=widget,
        on_change=keep_draft,
    )
    st.session_state[draft] = text
    if st.session_state[base_revision] != row.revision:
        st.warning("تغير النص المحفوظ في جلسة أو مهمة أخرى. مسودتك محفوظة؛ راجع النص الأحدث قبل الحفظ.")
        if st.button("تحميل النص المحفوظ الأحدث"):
            st.session_state[draft] = row.text
            st.session_state[base_revision] = row.revision
            st.session_state[base_text] = row.text
            del st.session_state[widget]
            st.rerun()
    save = st.button("حفظ النص")
    if save:
        changed = store.save_text(row.id, text, reviewer, st.session_state[base_revision])
        current = store.submission(row.id)
        st.session_state[base_revision] = current.revision
        st.session_state[base_text] = current.text
        st.session_state["_flash"] = (
            "تم حفظ النص. الورقة بانتظار مراجعة الدرجات."
            if changed
            else "النص لم يتغير؛ بقي الاعتماد والدرجة كما هما."
        )
        st.rerun()
    if st.button(
        "استخراج النص بمحرك OCR المحلي", disabled=os.getenv("OCR_PROVIDER", "tesseract") != "tesseract"
    ):
        if COMMERCIAL:
            from modules.jobs import Jobs

            st.session_state["ocr_job_" + row.id] = Jobs(store).create(
                "ocr", {"sid": row.id, "revision": row.revision}
            )
            st.rerun()
        try:
            with st.spinner("جارٍ استخراج النص محليًا…"):
                extracted = recognize(store.image_path(row.image_name).read_bytes())
            store.save_text(row.id, extracted, reviewer, row.revision, source="ocr")
            st.session_state["_flash"] = "تم استخراج النص محليًا. راجعه وصحح الأخطاء قبل التصحيح."
            st.rerun()
        except ValueError as exc:
            from modules.processing import ProcessingError
            code = exc.code if isinstance(exc, ProcessingError) else "ocr_failed"
            store.record_processing_failure(row.id, "ocr", row.revision, code=code)
            st.session_state["_processing_notice_" + row.id] = str(exc) if isinstance(exc, ProcessingError) else "فشل OCR؛ احتُفظ بالنص والدرجات المحفوظة."
            st.rerun()
    if COMMERCIAL and st.session_state.get("ocr_job_" + row.id):
        from modules.jobs import Jobs

        jobs = Jobs(store)
        job_id = st.session_state["ocr_job_" + row.id]
        task = jobs.get(job_id)
        labels = {
            "queued": "استخراج النص بانتظار العامل",
            "running": "جارٍ استخراج النص",
            "succeeded": "اكتمل استخراج النص؛ راجعه قبل اعتماد الدرجة",
            "failed": "تعذر الاستخراج؛ يمكنك إدخال النص يدويًا أو إعادة المحاولة",
            "cancelled": "أُلغيت مهمة استخراج النص",
        }
        st.info(labels[task.state])
        if task.state == "failed" and task.error:
            from modules.processing import message_for
            st.warning(message_for(task.error) + " [" + task.error + "]")
        if st.button("تحديث حالة استخراج النص"):
            st.rerun()
        if task.state in {"queued", "running"} and st.button("إلغاء استخراج النص"):
            jobs.change(job_id, "cancel")
            st.rerun()
        if task.state in {"failed", "cancelled"} and st.button("إعادة محاولة استخراج النص"):
            # Queue a fresh revision; replaying an obsolete OCR task must never overwrite a teacher edit.
            st.session_state["ocr_job_" + row.id] = jobs.create(
                "ocr", {"sid": row.id, "revision": row.revision}
            )
            st.rerun()
    st.caption(ocr_status()[1])
    source_ready = text == row.text and st.session_state[base_revision] == row.revision
    question_answers_ready = essay_answer_fields(row, spec, reviewer) if spec.questions else True
    answers_ready = question_answers_ready and source_ready
    if not source_ready:
        st.warning("النص الظاهر غير محفوظ؛ احفظه قبل طلب التصحيح أو اعتماد الدرجة.")
    saved_answers = answer_texts(spec, row.text, row.current)
    if st.button("فحص سبب تعثر التصحيح"):
        from modules.grading_diagnostics import essay_diagnostics
        report = essay_diagnostics(spec, row,
            organization_allowed=not COMMERCIAL or bool(identity.organization(store.token)["external_ai_allowed"]),
            visible_text=text, unsaved_answers=not question_answers_ready, ocr=ocr_status())
        st.session_state["diagnostic_" + row.id] = report
    if st.session_state.get("diagnostic_" + row.id):
        report = st.session_state["diagnostic_" + row.id]
        with st.expander("نتيجة فحص التصحيح", expanded=True):
            if report["revision"] != row.revision:
                st.info("هذا الفحص لمراجعة سابقة؛ اضغط فحص سبب تعثر التصحيح لتحديثه.")
            if report["blockers"]:
                for blocker in report["blockers"]:
                    st.warning(blocker["message"] + " [" + blocker["code"] + "]")
            else:
                st.success("الإجابات وإعدادات التصحيح جاهزة. يلزم تأكيد الموافقة لإرسال الطلب.")
            st.caption(report["ocr"]["message"])
            labels = {"answered": "إجابة محفوظة", "blank": "فارغ بعد المراجعة", "unassigned": "لم يُحفظ التوزيع"}
            st.dataframe(pd.DataFrame([{"السؤال": q["number"], "حالة الإجابة": labels[q["state"]],
                "عدد الأحرف": q["answer_characters"], "الدرجة الكاملة": q["maximum"]}
                for q in report["questions"]]), hide_index=True, width="stretch")
            sources = {"teacher_approved": "درجة اعتمدها المعلم", "model_proposal": "اقتراح من النموذج",
                       "none": "لا توجد درجة محفوظة"}
            st.write("مصدر الدرجة الظاهرة: " + sources[report["grade"]["source"]])
            for stage, attempt in report["last_attempts"].items():
                st.caption(("آخر طلب OCR: " if stage == "ocr" else "آخر طلب نموذج: ") + attempt["message"])
            import json
            st.download_button("تنزيل تقرير الفحص دون نصوص الطلاب", json.dumps(report, ensure_ascii=False, indent=2),
                               file_name="smartgrade-diagnostic.json", mime="application/json")
    st.markdown("#### اقتراح الدرجة ومراجعتها")
    consent = st.checkbox(
        "أوافق على إرسال النص والسؤال والمعايير للمزود الخارجي، وأعتمد تكلفة هذا الطلب",
        key=f"consent_{row.id}",
    )
    if st.button(
        "اقتراح درجات بالذكاء الاصطناعي",
        disabled=not (
            any(t.strip() for t in saved_answers.values())
            and answers_ready
            and consent
            and all(provider_status().values())
            and (not COMMERCIAL or identity.organization(store.token)["external_ai_allowed"])
        ),
    ):
        try:
            with st.spinner("جارٍ اقتراح الدرجات…"):
                if COMMERCIAL:
                    identity.actor(store.token, permission="write")
                    if not identity.organization(store.token)["external_ai_allowed"]:
                        raise ValueError("المؤسسة لم تسمح بإرسال الإجابات إلى مزود خارجي")
                proposal = propose_grade(spec, row.text, current=row.current, consent=consent)
            store.save_proposal(row.id, proposal.model_dump(), row.revision)
            st.rerun()
        except ProviderError as exc:
            if exc.attempted:
                store.record_processing_failure(row.id, "llm", row.revision, code=exc.code)
            st.session_state["_processing_notice_" + row.id] = f"{exc} (رمز: {exc.code})"
            st.rerun()
    if not all(provider_status().values()):
        st.caption("الاقتراح الآلي غير مفعّل. يمكنك إدخال الدرجات يدويًا وفق المعايير أدناه.")
    if row.proposal:
        st.info("الاقتراح الآلي متاح للمراجعة. الاقتباس الموجود في النص لا يثبت وحده صحة التقييم.")
    prior = row.approved or row.proposal or {}
    by_question = {g["question_id"]: g for g in prior.get("questions", [])}
    question_grades = []
    with st.form(f"essay_{row.id}_{row.revision}"):
        for number, question in enumerate(spec.essay_questions(), 1):
            if spec.questions:
                st.markdown(f"**السؤال {number} · {question.maximum:g} درجة**")
                st.write(question.question)
                st.write("الإجابة النموذجية: " + question.model_answer)
                if question.instructions:
                    st.caption(question.instructions)
            grades = criterion_grade_fields(row, question,
                by_question.get(question.id, {}) if spec.questions else prior,
                multiple=bool(spec.questions))
            question_grades.append({"question_id": question.id, "criteria": grades})
        reason = st.text_input("ملاحظة المراجع / سبب التعديل", max_chars=1000)
        checked = st.checkbox("راجعت نص الإجابة وجميع المعايير وأوافق على الدرجات")
        approve = st.form_submit_button("اعتماد الدرجة المقالية", type="primary", width="stretch", disabled=not answers_ready)
    if approve:
        for q in question_grades:
            for grade in q["criteria"]:
                if not grade["rationale"].strip():
                    raise ValueError("تبرير الدرجة مطلوب لكل معيار")
        proposal = (EssayGradeProposal(questions=question_grades).model_dump() if spec.questions
                    else GradeProposal(criteria=question_grades[0]["criteria"]).model_dump())
        store.approve_essay(row.id, proposal, reviewer, reason, row.revision, checked)
        st.rerun()


def review_page():
    st.title("التصحيح والمراجعة")
    st.caption("راجع القراءة والدرجات قبل اعتماد النتائج. كل تعديل محفوظ في سجل المراجعة.")
    exam = choose_exam(key="review_exam")
    if not exam:
        return
    page = 1
    count = store.counts(exam.id)["submissions"]
    if count > 50:
        page = st.number_input(
            "صفحة الأوراق", min_value=1, max_value=(count + 49) // 50, step=1, key="review_page"
        )
    rows = store.submissions(exam.id, limit=50, offset=(page - 1) * 50)
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
    reviewer = st.text_input(
        "اسم المراجع", value=PRINCIPAL.name if COMMERCIAL else "المعلم", max_chars=100, disabled=COMMERCIAL
    )
    if row.status == "approved":
        st.success(f"نتيجة معتمدة: {row.total:g} / {spec.maximum:g}")
    left, right = st.columns([1, 1.3], gap="large")
    with left:
        st.markdown("#### صورة الإجابة")
        image = decode_image(store.image_path(row.aligned_name or row.image_name).read_bytes())
        if spec.kind == "mcq":
            image = annotate(image, row.original["readings"])
        if COMMERCIAL:
            from modules.private_artifacts import image_html

            st.markdown(image_html(image), unsafe_allow_html=True)
        else:
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
    if COMMERCIAL and not PRINCIPAL.can("delete"):
        return
    with st.expander("حذف بيانات هذه الورقة"):
        confirmed = st.checkbox("أريد حذف الصورة والنص والدرجات وسجل المراجعة نهائيًا")
        if st.button("حذف التسليم", disabled=not confirmed):
            complete = store.delete_submission(sid, row.revision)
            st.session_state["_flash"] = (
                "اكتمل الحذف." if complete else "الورقة قيد الحذف؛ سيستكمل العامل مسح الملفات المتعثرة."
            )
            st.rerun()


def report_jobs(exam_id):
    from modules.jobs import Jobs
    from modules.private_artifacts import download_html

    jobs = Jobs(store)
    formats = {"xlsx": "Excel", "csv": "CSV", "pdf": "PDF"}
    kind = st.selectbox("صيغة التقرير الكامل", list(formats), format_func=formats.get)
    if st.button("إعداد التقرير في الخلفية", type="primary"):
        st.session_state["report_job_" + exam_id] = jobs.create(
            "report", {"exam_id": exam_id, "format": kind}
        )
    job_id = st.session_state.get("report_job_" + exam_id)
    if not job_id:
        return
    row = jobs.get(job_id)
    labels = {
        "queued": "بانتظار المعالجة",
        "running": "جارٍ إعداد التقرير",
        "succeeded": "جاهز للتنزيل",
        "failed": "تعذرت المعالجة؛ راجع البيانات وأعد المحاولة",
        "cancelled": "أُلغيت المهمة",
        "expired": "انتهت صلاحية الملف؛ أنشئ تقريرًا جديدًا",
    }
    st.write(labels[row.state])
    st.progress(row.progress / 100)
    if st.button("تحديث حالة التقرير"):
        st.rerun()
    if row.state in {"queued", "running"} and st.button("إلغاء إعداد التقرير"):
        jobs.change(job_id, "cancel")
        st.rerun()
    if row.state in {"failed", "cancelled"} and st.button("إعادة محاولة التقرير"):
        jobs.change(job_id, "retry")
        st.rerun()
    if row.state == "succeeded":
        path = jobs.result_path(job_id)
        mime = {
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "csv": "text/csv",
            "pdf": "application/pdf",
        }[path.suffix[1:]]
        st.markdown(
            download_html("تنزيل التقرير الكامل", path.read_bytes(), path.name, mime), unsafe_allow_html=True
        )
    st.caption(
        "ملف التقرير متاح لساعة واحدة. إلغاء المهمة يمنع نشر النتيجة؛ تتوقف المعالجة خلال المهلة القصوى."
    )


def results_page():
    st.title("النتائج والتصدير")
    st.caption("تتضمن ملفات التصدير النتائج التي اعتمدها المعلم فقط.")
    exam = choose_exam(key="result_exam")
    if not exam:
        return
    count = store.counts(exam.id)["approved"]
    if COMMERCIAL and count > 200:
        page = st.number_input("صفحة النتائج المعتمدة", min_value=1, max_value=(count + 49) // 50, step=1)
        summary, details = frames(store, exam.id, limit=50, offset=(page - 1) * 50)
        st.metric("إجمالي النتائج المعتمدة", count)
        st.caption("عرض 50 نتيجة في الصفحة. التقرير الكامل يُعالج في الخلفية.")
        st.dataframe(summary, hide_index=True, width="stretch")
        report_jobs(exam.id)
        return
    rows = store.submissions(exam.id, limit=200)
    summary, details = frames(store, exam.id)
    metrics = st.columns(3)
    metrics[0].metric("نتائج معتمدة", len(summary))
    if COMMERCIAL and PRINCIPAL.role == "viewer":
        metrics[1].metric("حالة العرض", "المعتمد فقط")
    else:
        metrics[1].metric("تحتاج مراجعة", sum(r.status != "approved" for r in rows))
    metrics[2].metric("متوسط النسبة", f"{summary['النسبة %'].mean():.1f}%" if not summary.empty else "—")
    if summary.empty:
        st.info("لا توجد نتائج معتمدة بعد. أكمل المراجعة واعتمد أول ورقة.")
        return
    st.dataframe(summary, hide_index=True, width="stretch")
    a, b, c = st.columns(3)
    from modules.pdf_reports import pdf_bytes

    exports = [
        (
            a,
            "تصدير Excel مع تفاصيل التصحيح",
            excel_bytes(summary, details),
            "smartgrade-results.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
        (b, "تصدير CSV", csv_bytes(summary), "smartgrade-results.csv", "text/csv"),
    ]
    if len(summary) <= 2000:
        exports.append(
            (
                c,
                "تصدير PDF بالعربية",
                pdf_bytes(summary, PRINCIPAL.organization_name if COMMERCIAL else "SmartGrade AI"),
                "smartgrade-results.pdf",
                "application/pdf",
            )
        )
    else:
        c.caption("PDF متاح حتى 2000 نتيجة في التقرير الواحد. استخدم Excel أو CSV لهذا الاختبار.")
    for column, label, content, filename, mime in exports:
        if COMMERCIAL:
            from modules.private_artifacts import download_html

            with column:
                st.markdown(download_html(label, content, filename, mime), unsafe_allow_html=True)
        else:
            column.download_button(label, content, filename, mime, width="stretch")
    if COMMERCIAL:
        store.record_event("report.generated", exam.id)
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
        "الوضع التجاري يعزل بيانات المؤسسات ويفرض الصلاحيات. استخدم HTTPS وإعدادات التشغيل الموثقة قبل فتح الخدمة للمستخدمين."
        if COMMERCIAL
        else "هذه نسخة تجربة دون حسابات. استخدم الوضع التجاري عند تشغيل الخدمة لمؤسسة."
    )
    st.markdown("#### حدود النسخة")
    st.write(
        "قالب OMR واحد، صورة PNG/JPEG واحدة في كل عملية، سؤال مقالي واحد لكل اختبار مقالي. "
        "لم تُعتمد جودة التعرف على خط اليد أو اتفاق التصحيح الآلي على بيانات طلاب حقيقية. "
        "التصدير متاح بصيغ Excel وCSV وPDF عربي. راجع تقرير حدود الجودة قبل استخدام الدرجات المقترحة."
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
    if COMMERCIAL:
        st.caption(PRINCIPAL.organization_name + " · " + ROLES[PRINCIPAL.role])
        st.caption(PRINCIPAL.name)
        if st.button("تسجيل الخروج", width="stretch"):
            identity.logout(st.session_state["auth_token"])
            clear_session("تم تسجيل الخروج بأمان.")
            st.rerun()
    else:
        st.caption("نسخة تجريبية · الاعتماد للمعلم")

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
    routes = {
        "الاختبارات": exams_page,
        "رفع الأوراق": upload_page,
        "التصحيح والمراجعة": review_page,
        "النتائج والتصدير": results_page,
        "الإعدادات": settings_page,
    }
    if COMMERCIAL:
        routes["حسابي"] = lambda: account_page(identity, PRINCIPAL)
        routes["إدارة المؤسسة"] = lambda: organization_page(identity, store)
    routes[page]()
    if "_flash" in st.session_state:
        st.success(st.session_state.pop("_flash"))
except (ValueError, OSError, SQLAlchemyError) as exc:
    notify_error(exc)
