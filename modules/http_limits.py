"""Bound request bodies on disk and bound simultaneous requests before parsing multipart data."""

import asyncio
import tempfile
from starlette.responses import JSONResponse


class RequestSizeLimit:
    def __init__(self, app, maximum=11 * 1024 * 1024, concurrency=8, body_seconds=30):
        self.app, self.maximum = app, maximum
        self.body_seconds = body_seconds
        self.gate = asyncio.Semaphore(concurrency)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            length = -1
        if length < 0 or length > self.maximum:
            return await JSONResponse({"detail": "حجم الطلب غير مقبول"}, status_code=413)(
                scope, receive, send
            )
        try:
            await asyncio.wait_for(self.gate.acquire(), timeout=5)
        except TimeoutError:
            return await JSONResponse(
                {"detail": "الخدمة مشغولة؛ أعد المحاولة"}, status_code=503, headers={"Retry-After": "5"}
            )(scope, receive, send)
        try:
            with tempfile.SpooledTemporaryFile(max_size=256 * 1024) as body:
                size = 0
                deadline = asyncio.get_running_loop().time() + self.body_seconds
                while True:
                    try:
                        remaining_time = max(0, deadline - asyncio.get_running_loop().time())
                        message = await asyncio.wait_for(receive(), timeout=remaining_time)
                    except TimeoutError:
                        return await JSONResponse({"detail": "انتهت مهلة استقبال الملف"}, status_code=408)(
                            scope, receive, send
                        )
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    size += len(chunk)
                    if size > self.maximum:
                        return await JSONResponse(
                            {"detail": "حجم الطلب أكبر من الحد المسموح"}, status_code=413
                        )(scope, receive, send)
                    body.write(chunk)
                    if not message.get("more_body", False):
                        break
                body.seek(0)
                remaining = size
                delivered = False

                async def replay():
                    nonlocal remaining, delivered
                    if not delivered:
                        chunk = body.read(64 * 1024)
                        remaining -= len(chunk)
                        delivered = remaining == 0
                        return {"type": "http.request", "body": chunk, "more_body": not delivered}
                    return await receive()

                await self.app(scope, replay, send)
        finally:
            self.gate.release()
