# @title تشغيل SmartGrade دون تنزيل ملفات المشروع
"""Open the published institutional SmartGrade notebook directly in Google Colab."""

import hashlib
import json
import sys
from urllib.request import urlopen

# اترك الاستعادة معطلة في أول جلسة. لاستعادة بياناتك، ارفع ملفاتك من لوحة Colab أولًا.
OPEN_IN_NEW_TAB = True  # @param {type:"boolean"}
RESTORE_PREVIOUS = False  # @param {type:"boolean"}
RESTORE_FILE = ""  # @param {type:"string"}
RESTORE_KEY = ""  # @param {type:"string"}

SOURCE_COMMIT = "73b9f73dc6d247f04f3b4da67fec32a0f5e20b38"
NOTEBOOK_BLOB = "3822328058e3115723a1a26921b403e969428a78"
NOTEBOOK_URL = (
    "https://raw.githubusercontent.com/alkhwarzmy/SmartGrade-AI/"
    + SOURCE_COMMIT
    + "/notebooks/SmartGrade_Institution_Colab.ipynb"
)
MAX_NOTEBOOK_BYTES = 8 * 1024 * 1024


def _source(cell):
    value = cell["source"]
    return "".join(value) if isinstance(value, list) else value


def _display_source(source):
    if not OPEN_IN_NEW_TAB:
        return source
    before = "output.serve_kernel_port_as_iframe(PORT, height=1000, cache_in_notebook=False)"
    if "output.serve_kernel_port_as_iframe(" in source:
        if source.count(before) != 1:
            raise RuntimeError("تعذر تحديد خطوة عرض الواجهة")
        return source.replace(before, "output.serve_kernel_port_as_window(PORT)", 1)
    return source


def _fetch_notebook():
    with urlopen(NOTEBOOK_URL, timeout=60) as response:
        payload = response.read(MAX_NOTEBOOK_BYTES + 1)
    if len(payload) > MAX_NOTEBOOK_BYTES:
        raise ValueError("حجم دفتر المصدر غير متوقع")
    header = b"blob " + str(len(payload)).encode("ascii") + b"\0"
    if hashlib.sha1(header + payload).hexdigest() != NOTEBOOK_BLOB:
        raise ValueError("محتوى الدفتر لا يطابق النسخة المثبتة")
    notebook = json.loads(payload)
    if notebook.get("nbformat") != 4:
        raise ValueError("صيغة دفتر المصدر غير متوقعة")
    cells = [cell for cell in notebook["cells"] if cell["cell_type"] == "code"]
    if len(cells) != 10:
        raise ValueError("عدد خلايا دفتر المصدر غير متوقع")
    for index, cell in enumerate(cells, 1):
        compile(_source(cell), f"<institution-source-{index}>", "exec")
    return cells


def start_project():
    if sys.version_info < (3, 11):
        raise RuntimeError("اختر جلسة Colab حديثة تستخدم Python 3.11 أو أحدث")
    try:
        __import__("google.colab")
    except ImportError as exc:
        raise RuntimeError("شغّل هذا الدفتر داخل Google Colab") from exc
    if RESTORE_PREVIOUS and (not RESTORE_FILE.strip() or not RESTORE_KEY.strip()):
        raise ValueError("حدد مسار النسخة المشفرة ومفتاحها قبل تفعيل الاستعادة")
    scope = globals().setdefault("_smartgrade_direct_scope", {})
    previous = scope.get("_smartgrade_institution_runtime")
    if previous is not None:
        previous.stop()
    cells = _fetch_notebook()
    globals()["_smartgrade_direct_cells"] = cells
    scope.setdefault("__name__", "__main__")
    for index, cell in enumerate(cells, 1):
        source = _source(cell)
        if "RESTORE_INSTITUTION = False" in source:
            source = source.replace(
                "RESTORE_INSTITUTION = False",
                "RESTORE_INSTITUTION = " + repr(bool(RESTORE_PREVIOUS)),
                1,
            )
            source = source.replace('RESTORE_ARCHIVE = ""', "RESTORE_ARCHIVE = " + repr(RESTORE_FILE), 1)
            source = source.replace('RESTORE_KEY_FILE = ""', "RESTORE_KEY_FILE = " + repr(RESTORE_KEY), 1)
        source = _display_source(source)
        print(f"تشغيل خطوة {index} من {len(cells)}…", flush=True)
        exec(compile(source, f"<institution-step-{index}>", "exec"), scope)
    print("افتح رابط الواجهة وسجّل الدخول. احفظ نسخة مشفرة قبل انتهاء جلسة Colab.")


def _run_original(marker, replacements=None):
    cells = globals().get("_smartgrade_direct_cells")
    scope = globals().get("_smartgrade_direct_scope")
    if not cells or scope is None:
        raise RuntimeError("شغّل خلية بدء المشروع أولًا")
    matching = [_source(cell) for cell in cells if marker in _source(cell)]
    if len(matching) != 1:
        raise RuntimeError("تعذر تحديد خطوة المصدر المطلوبة")
    source = matching[0]
    for before, after in (replacements or {}).items():
        if source.count(before) != 1:
            raise RuntimeError("شكل خطوة المصدر غير متوقع")
        source = source.replace(before, after, 1)
    source = _display_source(source)
    exec(compile(source, "<institution-command>", "exec"), scope)


def backup_project():
    _run_original(
        'BACKUP_ACTION = "none"',
        {
            'BACKUP_ACTION = "none"': 'BACKUP_ACTION = "download"',
            "DOWNLOAD_BACKUP_KEY = False": "DOWNLOAD_BACKUP_KEY = True",
        },
    )


def reopen_project():
    _run_original("output.serve_kernel_port_as_iframe(")


def open_project_window():
    scope = globals().get("_smartgrade_direct_scope")
    if not scope or "PORT" not in scope:
        raise RuntimeError("شغّل خلية بدء المشروع أولًا")
    from google.colab import output

    output.serve_kernel_port_as_window(int(scope["PORT"]))


def stop_project():
    _run_original("STOP_APP = False", {"STOP_APP = False": "STOP_APP = True"})


if __name__ == "__main__":
    start_project()