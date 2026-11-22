"""Consistent Arabic workspace presentation; never interpolate unescaped user content."""
from html import escape
import streamlit as st

STATUS_LABELS = {"review": "بانتظار المراجعة", "approved": "معتمد", "failed": "تحتاج معالجة"}

CSS = """
<style>
:root {--sg-ink:#153b42;--sg-teal:#0f766e;--sg-line:#dce6e9;--sg-muted:#526b75;}
html,body,.stApp {font-family:'Noto Sans Arabic','Segoe UI',Tahoma,sans-serif;color:var(--sg-ink);}
[data-testid="stMain"] {direction:rtl;background:#f4f7f8;}
.block-container {max-width:1500px;padding:2rem 2.5rem 4rem;}
h1,h2,h3,h4,p,label {text-align:right;}
h1 {font-size:1.9rem!important;font-weight:750!important;letter-spacing:-.5px;}
h2 {font-size:1.45rem!important;}
[data-testid="stCaptionContainer"] {color:var(--sg-muted);}
[data-testid="stSidebar"] {direction:rtl;background:#12373e;border-left:0;color:#e8f3f2;}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"],
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] {color:#d2e6e5;}
[data-testid="stSidebar"] [role="radiogroup"] {gap:6px;}
[data-testid="stSidebar"] [role="radiogroup"] label {width:100%;border-radius:10px;padding:10px 12px;color:#eef7f7;}
[data-testid="stSidebar"] [role="radiogroup"] label:hover {background:#244b51;}
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {background:#246159;color:white;}
[data-testid="stSidebar"] hr {border-color:#ffffff24;}
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {color:#bfd8d7!important;}
[data-testid="stSidebar"] .stButton button [data-testid="stMarkdownContainer"] {color:#153b42;}
.brand {font-size:26px;font-weight:750;color:white;margin:8px 0 4px;direction:ltr;text-align:right;}
.subbrand {font-size:12px;color:#bfd8d7;margin-bottom:28px;}
[data-testid="stMetric"] {background:white;border:1px solid var(--sg-line);border-radius:14px;padding:18px 20px;box-shadow:0 3px 10px #153b4205;}
[data-testid="stMetricLabel"] {color:var(--sg-muted);}
[data-testid="stMetricValue"] {color:var(--sg-ink);font-size:1.9rem;}
[data-testid="stForm"],[data-testid="stExpander"] {background:white;border:1px solid var(--sg-line);border-radius:14px;}
[data-testid="stForm"] {padding:24px;}
[data-baseweb="tab-list"] {gap:8px;border-bottom:1px solid var(--sg-line);margin:12px 0 18px;}
[data-baseweb="tab"] {padding:12px 20px;border-radius:10px 10px 0 0;}
[data-baseweb="tab"][aria-selected="true"] {background:#e4f1ed;color:#0e6058;font-weight:700;}
.stButton>button,.stDownloadButton>button {min-height:44px;border-radius:10px;font-weight:600;}
button:focus-visible,input:focus-visible,textarea:focus-visible {outline:3px solid #53b6aa!important;outline-offset:3px;}
[data-testid="stTextInput"] input,[data-testid="stTextArea"] textarea {line-height:1.7;}
[data-testid="stFileUploader"] {background:white;border:1px dashed #85b3ad;border-radius:14px;padding:12px;}
[data-testid="stFileUploaderDropzone"] {background:#edf6f3;}
[data-testid="stAlert"] {border-radius:12px;}
[data-testid="stExpander"] img[alt="صورة الإجابة للمراجعة"] {max-height:480px;object-fit:contain;background:#f7f9fa;}
.sg-topbar {display:flex;justify-content:space-between;align-items:center;gap:16px;padding:0 0 18px;margin-bottom:16px;border-bottom:1px solid var(--sg-line);color:var(--sg-muted);font-size:13px;}
.sg-product {font-weight:800;color:var(--sg-ink);direction:ltr;}
.sg-tag {display:inline-block;padding:5px 11px;border:1px solid #d5e6e1;border-radius:99px;background:#eaf5f0;color:#1b6056;font-size:12px;line-height:1.6;}
.hero {position:relative;overflow:hidden;background:linear-gradient(115deg,#123b43,#145950);padding:30px 34px;border-radius:18px;margin:8px 0 24px;color:white;}
.hero h2 {color:white;font-size:1.8rem!important;line-height:1.5;margin:6px 0 10px;}
.hero p {color:#d5e9e5;margin:0;line-height:1.9;max-width:800px;}
.eyebrow {font-size:11px;font-weight:700;letter-spacing:1.3px;color:#9de0c8;direction:ltr;text-align:right;}
.sg-workflow {display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;padding:0;margin:18px 0 24px;list-style:none;}
.sg-workflow li {display:flex;gap:10px;align-items:center;padding:12px;background:white;border:1px solid var(--sg-line);border-radius:12px;font-size:13px;color:var(--sg-muted);}
.sg-workflow li.active {border-color:#2c8d7f;background:#eaf5f0;color:#155b52;font-weight:700;}
.sg-workflow span {display:grid;place-items:center;flex-shrink:0;width:28px;height:28px;border-radius:8px;background:#eef3f4;font-variant-numeric:tabular-nums;}
.sg-workflow li.active span {background:#15756a;color:white;}
.sg-summary {display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:16px;background:white;border:1px solid var(--sg-line);border-right:4px solid #278779;border-radius:12px;padding:16px 20px;margin:14px 0;}
.sg-summary strong {display:block;margin-bottom:4px;font-size:17px;}
.sg-summary small {color:var(--sg-muted);line-height:1.8;}
.sg-empty {background:white;border:1px dashed #b7cecd;border-radius:16px;padding:32px;text-align:center;margin:16px 0;}
.sg-empty h3,.sg-empty p {text-align:center;}
.sg-empty p {color:var(--sg-muted);line-height:1.9;margin-bottom:0;}
.sg-empty-mark {color:#14766b;font-size:26px;}
.sg-saving {padding:10px 14px;border-radius:10px;background:#eef4f6;color:#3b5e6a;font-size:13px;margin-bottom:12px;}
footer {visibility:hidden;}
.st-key-mobile_nav {display:none;}
@media(max-width:900px) {.block-container {padding:1.5rem 1.25rem 3rem;}.sg-workflow {grid-template-columns:repeat(2,minmax(0,1fr));}}
@media(max-width:768px) {
[data-testid="stSidebar"],[data-testid="stSidebarCollapsedControl"] {display:none!important;}
.st-key-mobile_nav {display:block!important;}
.block-container {padding:1rem .85rem 3rem;}
.hero {padding:22px 20px;}.hero h2 {font-size:1.5rem!important;}
h1 {font-size:1.6rem!important;}
.sg-topbar {align-items:flex-start;flex-direction:column;gap:6px;}
.sg-workflow {gap:6px;}.sg-workflow li {padding:10px 8px;font-size:12px;}
[data-testid="stForm"] {padding:16px;}
}
</style>
"""


def apply_theme():
    st.markdown(CSS, unsafe_allow_html=True)


def workspace_header(page, organization="مساحة المعلم"):
    st.markdown(
        '<div class="sg-topbar"><span class="sg-product">✦ SmartGrade</span>'
        f'<span>{escape(str(organization))} / {escape(str(page))}</span>'
        '<span class="sg-tag">الاعتماد النهائي للمعلم</span></div>',
        unsafe_allow_html=True,
    )


def workflow(active):
    labels = ["إعداد الاختبار", "رفع الأوراق", "مراجعة التصحيح", "اعتماد وتصدير"]
    items = "".join(
        f'<li class="{"active" if n == active else ""}"'
        + (' aria-current="step"' if n == active else "")
        + f'><span>{n + 1:02}</span>{label}</li>'
        for n, label in enumerate(labels)
    )
    st.markdown('<ol class="sg-workflow" aria-label="مراحل التصحيح">' + items + '</ol>', unsafe_allow_html=True)


def empty_state(title, message):
    st.markdown(
        '<div class="sg-empty"><div class="sg-empty-mark" aria-hidden="true">◇</div>'
        f'<h3>{escape(str(title))}</h3><p>{escape(str(message))}</p></div>',
        unsafe_allow_html=True,
    )


def exam_summary(exam, spec):
    count = len(spec.essay_questions()) if spec.kind == "essay" else 20
    kind = "اختبار مقالي" if spec.kind == "essay" else "اختيار من متعدد"
    st.markdown(
        '<div class="sg-summary"><div>'
        f'<strong>{escape(str(exam.title))}</strong><small>{kind} · {count} سؤال · '
        f'{spec.maximum:g} درجة</small></div>'
        '<span class="sg-tag">مساحة الاختبار</span></div>',
        unsafe_allow_html=True,
    )


def filter_papers(rows, query="", status="all"):
    """Filter the loaded page only; callers must show the page scope to users."""
    query = query.strip().casefold()
    return [row for row in rows if (status == "all" or row.status == status)
            and (not query or query in row.student_code.casefold())]
