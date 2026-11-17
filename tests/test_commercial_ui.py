from pathlib import Path
from streamlit.testing.v1 import AppTest
import config
from modules.auth import AuthService
from modules.workspace import Workspace

PASSWORD = "Commercial-synthetic-Pass!2026"


def app_for(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTGRADE_MODE", "commercial")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    auth = AuthService(tmp_path)
    auth.provision_organization("school-one", "مدرسة تجريبية", "admin@one.test", "المدير", PASSWORD)
    return auth, AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=30).run()


def ui_login(app, email="admin@one.test", password=PASSWORD):
    next(t for t in app.text_input if t.label == "رمز المؤسسة").set_value("school-one")
    next(t for t in app.text_input if t.label == "البريد الإلكتروني").set_value(email)
    next(t for t in app.text_input if t.label == "كلمة المرور").set_value(password)
    next(b for b in app.button if b.label == "دخول إلى مساحة العمل").click().run()
    assert not app.exception


def test_commercial_no_account_bypass(tmp_path, monkeypatch):
    auth, app = app_for(tmp_path, monkeypatch)
    assert not app.exception and not app.radio
    assert not any(t.label == "عنوان الاختبار" for t in app.text_input)
    ui_login(app, password="wrong")
    assert not app.radio and app.error
    ui_login(app)
    assert "إدارة المؤسسة" in app.radio(key="navigation").options
    next(t for t in app.text_input if t.label == "عنوان الاختبار").set_value("اختبار آمن")
    next(b for b in app.button if b.label == "إنشاء الاختبار").click().run()
    assert not app.exception
    token = auth.login("school-one", "admin@one.test", PASSWORD)
    assert len(Workspace(auth, token).exams()) == 1
    app.radio(key="navigation").set_value("إدارة المؤسسة").run()
    assert not app.exception and app.dataframe
    next(b for b in app.button if b.label == "تسجيل الخروج").click().run()
    assert not app.exception and not app.radio
    assert "auth_token" not in app.session_state


def test_viewer_ui_and_forced_password_change(tmp_path, monkeypatch):
    auth, app = app_for(tmp_path, monkeypatch)
    token = auth.login("school-one", "admin@one.test", PASSWORD)
    auth.create_user(token, "viewer@one.test", "مشاهد", PASSWORD, "viewer")
    ui_login(app, email="viewer@one.test")
    assert not app.radio
    new = "Changed-synthetic-Password!2027"
    next(t for t in app.text_input if t.label == "كلمة المرور الحالية").set_value(PASSWORD)
    next(t for t in app.text_input if t.label == "كلمة المرور الجديدة").set_value(new)
    next(t for t in app.text_input if t.label == "تأكيد كلمة المرور الجديدة").set_value(new)
    next(b for b in app.button if b.label == "تغيير كلمة المرور وتسجيل الخروج").click().run()
    assert not app.exception
    ui_login(app, email="viewer@one.test", password=new)
    assert app.radio(key="navigation").options == ["النتائج والتصدير", "حسابي"]
    app.radio(key="navigation").set_value("حسابي").run()
    assert not app.exception
