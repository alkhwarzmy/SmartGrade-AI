"""Arabic account and organization administration screens for the commercial UI."""

from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import streamlit as st
from modules.auth import AuthError, PermissionDenied

ROLES = {"admin": "مدير المؤسسة", "teacher": "معلم / مصحح", "viewer": "مشاهد النتائج"}


def clear_session(notice=""):
    for key in list(st.session_state):
        del st.session_state[key]
    if notice:
        st.session_state["login_notice"] = notice


def sign_in(auth):
    token = st.session_state.get("auth_token")
    actor = None
    if token:
        try:
            actor = auth.actor(token, allow_password_change=True)
        except AuthError:
            clear_session("انتهت الجلسة أو أُلغيت. سجّل الدخول مجددًا.")
    if actor and actor.must_change_password:
        st.title("تأمين حسابك")
        st.info("غيّر كلمة المرور المؤقتة قبل الوصول إلى بيانات المؤسسة.")
        password_form(auth, mandatory=True)
        if st.button("العودة لتسجيل الدخول"):
            auth.logout(token)
            clear_session()
            st.rerun()
        st.stop()
    if actor:
        return actor
    st.markdown(
        '<div class="hero"><div class="eyebrow">SMARTGRADE / INSTITUTIONS</div>'
        "<h2>مساحة مؤسستك لتصحيح الاختبارات</h2>"
        "<p>دخول آمن، مراجعة موثقة، ونتائج يعتمدها المعلم.</p></div>",
        unsafe_allow_html=True,
    )
    if "login_notice" in st.session_state:
        st.info(st.session_state.pop("login_notice"))
    if not auth.has_organizations():
        st.info("لم تُجهز مؤسسة بعد. ينشئ مشغل النظام حساب المدير من أداة الإعداد الآمنة.")
        st.code(
            'python -m scripts.manage init --slug my-school --name "اسم المؤسسة" --email admin@example.com'
        )
        st.caption("لن يُنشأ أي حساب أو كلمة مرور افتراضية من الواجهة.")
        st.stop()
    left, center, right = st.columns([1, 2, 1])
    with center:
        with st.form("commercial_login", clear_on_submit=True):
            st.subheader("تسجيل الدخول")
            slug = st.text_input("رمز المؤسسة", placeholder="my-school", max_chars=48)
            email = st.text_input("البريد الإلكتروني", max_chars=254)
            password = st.text_input("كلمة المرور", type="password", max_chars=128)
            submitted = st.form_submit_button("دخول إلى مساحة العمل", type="primary", width="stretch")
        if submitted:
            try:
                client = getattr(st.context, "ip_address", None) or "streamlit-local"
                token = auth.login(slug, email, password, client_id=client)
                clear_session()
                st.session_state["auth_token"] = token
                st.rerun()
            except AuthError as exc:
                st.error(str(exc))
        st.caption("اطلب رمز المؤسسة أو إعادة تعيين كلمة المرور من مدير مؤسستك.")
    st.stop()


def password_form(auth, mandatory=False):
    with st.form("password_change", clear_on_submit=True):
        current = st.text_input("كلمة المرور الحالية", type="password", max_chars=128)
        new = st.text_input(
            "كلمة المرور الجديدة",
            type="password",
            max_chars=128,
            help="12 إلى 128 حرفًا؛ استخدم كلمة مرور فريدة",
        )
        confirm = st.text_input("تأكيد كلمة المرور الجديدة", type="password", max_chars=128)
        submit = st.form_submit_button("تغيير كلمة المرور وتسجيل الخروج", type="primary")
    if submit:
        try:
            if new != confirm:
                raise ValueError("كلمتا المرور غير متطابقتين")
            auth.change_password(st.session_state["auth_token"], current, new)
            clear_session("تم تغيير كلمة المرور وإلغاء الجلسات السابقة. سجّل الدخول بكلمتك الجديدة.")
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))


def account_page(auth, actor):
    st.title("حسابي وأمان الجلسة")
    st.write(actor.organization_name)
    st.caption(f"{actor.name} · {actor.email} · {ROLES[actor.role]}")
    st.markdown("#### تغيير كلمة المرور")
    st.caption("تغيير كلمة المرور يلغي جميع جلسات حسابك. يبقى اعتماد المعلم مرتبطًا بهويتك المسجلة.")
    password_form(auth)


def organization_page(auth, workspace):
    actor = auth.actor(workspace.token, permission="users")
    st.title("إدارة المؤسسة")
    st.caption(actor.organization_name)
    usage = workspace.usage()
    labels = {
        "users": "المستخدمون النشطون",
        "exams": "الاختبارات",
        "submissions": "الأوراق",
        "storage_mb": "التخزين MB",
    }
    for column, (key, label) in zip(st.columns(4), labels.items()):
        with column:
            st.metric(label, f"{usage[key]:g} / {usage['limits'][key]:g}")
            st.progress(min(1.0, usage[key] / usage["limits"][key]))
    st.caption("الترخيص التشغيلي: " + usage["plan"] + " · إدارة العقود والسعة عبر مشغل الخدمة.")
    if usage["renewal_until"]:
        expiry = datetime.fromtimestamp(usage["renewal_until"], ZoneInfo("Asia/Riyadh")).strftime("%Y-%m-%d")
        st.caption("سريان الترخيص حتى: " + expiry + " بتوقيت الرياض")
    members, settings, audit = st.tabs(["فريق العمل", "سياسة الذكاء الاصطناعي", "سجل المؤسسة"])
    with members:
        users = auth.list_users(workspace.token)
        display = [
            {
                "الاسم": u["name"],
                "البريد": u["email"],
                "الدور": ROLES[u["role"]],
                "الحالة": "نشط" if u["active"] else "معطل",
                "تغيير كلمة المرور مطلوب": bool(u["must_change_password"]),
            }
            for u in users
        ]
        st.dataframe(pd.DataFrame(display), hide_index=True, width="stretch")
        with st.expander("إضافة عضو للفريق"):
            with st.form("create_member", clear_on_submit=True):
                name = st.text_input("اسم العضو", max_chars=100)
                email = st.text_input("بريد العضو", max_chars=254)
                role = st.selectbox("صلاحية العضو", list(ROLES), format_func=ROLES.get, index=1)
                password = st.text_input("كلمة مرور مؤقتة", type="password", max_chars=128)
                create = st.form_submit_button("إضافة المستخدم", type="primary")
            if create:
                auth.create_user(workspace.token, email, name, password, role)
                st.session_state["_flash"] = (
                    "أُضيف المستخدم. سلّمه كلمة المرور بقناة آمنة؛ سيُلزم بتغييرها عند دخوله الأول."
                )
                st.rerun()
        with st.expander("تعديل صلاحيات عضو أو تعطيله"):
            mapping = {u["id"]: u for u in users}
            uid = st.selectbox(
                "اختر العضو",
                list(mapping),
                format_func=lambda x: mapping[x]["name"] + " · " + mapping[x]["email"],
            )
            user = mapping[uid]
            with st.form(f"update_member_{uid}"):
                role = st.selectbox(
                    "الدور الجديد", list(ROLES), format_func=ROLES.get, index=list(ROLES).index(user["role"])
                )
                active = st.checkbox("حساب نشط", value=bool(user["active"]))
                update = st.form_submit_button("حفظ التغيير وإلغاء جلسات العضو")
            if update:
                auth.update_user(workspace.token, uid, role=role, active=active)
                st.session_state["_flash"] = "تم تحديث العضو وإلغاء جلساته السابقة."
                st.rerun()
            with st.form(f"reset_member_{uid}", clear_on_submit=True):
                temporary = st.text_input("كلمة مرور بديلة مؤقتة", type="password", max_chars=128)
                reset = st.form_submit_button("إعادة تعيين كلمة المرور وإلغاء الجلسات")
            if reset:
                auth.reset_password(workspace.token, uid, temporary)
                st.session_state["_flash"] = "أُعيد تعيين كلمة المرور. يغيّرها العضو عند الدخول التالي."
                st.rerun()
    with settings:
        st.write(
            "لا تُرسل إجابات المؤسسة إلى مزود خارجي إلا إذا سمحت المؤسسة والمشغل بذلك، ثم وافق المراجع على الطلب والتكلفة."
        )
        with st.form("organization_ai"):
            enabled = st.checkbox(
                "السماح باستخدام مزود تصحيح خارجي لإجابات هذه المؤسسة", value=usage["external_ai_allowed"]
            )
            confirm = st.checkbox("أؤكد أن لدي صلاحية اعتماد معالجة بيانات المؤسسة خارجيًا")
            save = st.form_submit_button("حفظ سياسة المؤسسة")
        if save:
            if enabled and not confirm:
                raise PermissionDenied("يلزم اعتماد سياسة معالجة البيانات قبل التفعيل")
            auth.set_ai_policy(workspace.token, enabled)
            st.session_state["_flash"] = "تم حفظ سياسة المؤسسة. تظل إعدادات المشغل وموافقة المراجع مطلوبة."
            st.rerun()
    with audit:
        valid = auth.verify_audit(workspace.token)
        if valid:
            st.success("سلسلة السجل سليمة وفق فحص البصمات الحالي.")
        else:
            st.error("فشل فحص سلسلة السجل؛ راجع المشغل قبل متابعة الاعتماد.")
        events = auth.audit_events(workspace.token)
        names = {u["id"]: u["name"] for u in auth.list_users(workspace.token)}
        rows = [
            {
                "الوقت بتوقيت الرياض": datetime.fromtimestamp(
                    e["created_at"], ZoneInfo("Asia/Riyadh")
                ).strftime("%Y-%m-%d %H:%M:%S"),
                "المستخدم": names.get(e["actor_id"], "مشغل النظام"),
                "الإجراء": e["action"],
                "مرجع العملية": e["object_id"],
            }
            for e in events
        ]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption(
            "آخر 200 حدث. السجل لا يحفظ كلمات مرور أو نصوص إجابات؛ فحص البصمات ليس شهادة تدقيق قانونية."
        )
