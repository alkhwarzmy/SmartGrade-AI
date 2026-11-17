"""Inline artifacts for authenticated Streamlit sessions.

Streamlit's default media/download URLs are content-addressed capabilities, not
protected by our account service. Commercial student images and reports therefore
travel inline through the authenticated view instead of that global media route.
"""

import base64
from html import escape
import io
from PIL import Image

MAX_INLINE_DOWNLOAD_BYTES = 10 * 1024 * 1024
MIMES = {"application/pdf", "text/csv", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


def download_html(label, data, filename, mime):
    if mime not in MIMES or not isinstance(data, bytes) or len(data) > MAX_INLINE_DOWNLOAD_BYTES:
        raise ValueError("الملف أكبر من حد التنزيل داخل الواجهة؛ استخدم API الموثق للتقارير الكبيرة")
    encoded = base64.b64encode(data).decode("ascii")
    return (
        f'<a role="button" download="{escape(filename, quote=True)}" '
        f'href="data:{mime};base64,{encoded}" '
        'style="display:block;text-align:center;padding:11px 14px;margin:0 0 12px;'
        'border:1px solid #d4e1df;border-radius:9px;color:#126f65;background:white;text-decoration:none">'
        f"{escape(label)}</a>"
    )


def image_html(image):
    rendered = Image.fromarray(image)
    rendered.thumbnail((1400, 1800))
    out = io.BytesIO()
    rendered.save(out, format="PNG")
    encoded = base64.b64encode(out.getvalue()).decode("ascii")
    return (
        f'<img alt="صورة الإجابة للمراجعة" src="data:image/png;base64,{encoded}" '
        'style="width:100%;height:auto;border-radius:12px;border:1px solid #e1e8eb" />'
    )
