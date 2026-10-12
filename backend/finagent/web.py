"""Deployment wiring: serve the built frontend, and put a shared password in front of everything.

Local development sets none of these, so nothing changes there. A deployment sets REQUIRE_PASSWORD=1,
and then the app refuses to start unless APP_PASSWORD is also set, so it can never go live open to everyone.
"""

import base64
import os
import secrets
from pathlib import Path

from starlette.staticfiles import StaticFiles


class PasswordGate:
    """Pure ASGI middleware (it does not buffer, so streamed answers still stream). HTTP Basic, any user name."""

    def __init__(self, app, password: str):
        self.app = app
        self.password = password.encode()

    def _allowed(self, scope) -> bool:
        for key, value in scope["headers"]:
            if key == b"authorization" and value[:6].lower() == b"basic ":
                try:
                    _, _, given = base64.b64decode(value[6:]).partition(b":")
                except ValueError:
                    return False
                return secrets.compare_digest(given, self.password)
        return False

    async def __call__(self, scope, receive, send):
        kind = scope["type"]
        if kind == "lifespan" or (kind in ("http", "websocket") and self._allowed(scope)):
            await self.app(scope, receive, send)
        elif kind == "websocket":
            await receive()  # the client's connect message, then refuse it
            await send({"type": "websocket.close", "code": 1008})
        else:
            body = b"Password required"
            await send({"type": "http.response.start", "status": 401, "headers": [
                (b"www-authenticate", b'Basic realm="AI Financial Agent"'), (b"content-type", b"text/plain"),
                (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})


def install(app, *, static_dir: Path | str | None = None, password: str | None = None,
            require_password: bool = False) -> None:
    """Call after every API route is defined: the static mount at "/" must come last."""
    if require_password and not password:
        raise RuntimeError("REQUIRE_PASSWORD is set but APP_PASSWORD is empty: refusing to start without a password.")
    if static_dir and Path(static_dir).is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="site")
    if password:
        app.add_middleware(PasswordGate, password=password)


def install_from_env(app) -> None:
    install(app, static_dir=os.getenv("STATIC_DIR") or None, password=os.getenv("APP_PASSWORD") or None,
            require_password=os.getenv("REQUIRE_PASSWORD", "").lower() in ("1", "true", "yes"))
