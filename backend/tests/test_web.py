import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from finagent import web


def make_app(tmp_path=None, **options):
    app = FastAPI()

    @app.get("/api/ping")
    def ping():
        return {"pong": True}

    @app.get("/api/stream")
    def stream():
        return StreamingResponse((f"data: {i}\n\n" for i in range(3)), media_type="text/event-stream")

    static_dir = None
    if tmp_path is not None:
        (tmp_path / "index.html").write_text("<html>FinAgent UI</html>")
        (tmp_path / "app.js").write_text("console.log('ok')")
        static_dir = tmp_path
    web.install(app, static_dir=static_dir, **options)
    return TestClient(app)


def test_serves_the_built_frontend_next_to_the_api(tmp_path):
    c = make_app(tmp_path)
    assert "FinAgent UI" in c.get("/").text
    assert c.get("/app.js").status_code == 200
    assert c.get("/api/ping").json() == {"pong": True}   # API routes are not shadowed by the static mount


def test_without_a_build_the_api_still_works(tmp_path):
    c = make_app(None)
    assert c.get("/api/ping").status_code == 200
    assert c.get("/").status_code == 404


def test_password_gate_refuses_requests_without_the_right_password(tmp_path):
    c = make_app(tmp_path, password="s3cret")
    for path in ("/", "/api/ping"):
        r = c.get(path)
        assert r.status_code == 401
        assert r.headers["www-authenticate"].startswith("Basic")
    assert c.get("/api/ping", auth=("anyone", "wrong")).status_code == 401
    assert c.get("/api/ping", auth=("anyone", "")).status_code == 401


def test_password_gate_accepts_the_password_with_any_user_name(tmp_path):
    c = make_app(tmp_path, password="s3cret")
    assert c.get("/api/ping", auth=("team", "s3cret")).json() == {"pong": True}
    assert "FinAgent UI" in c.get("/", auth=("ravi", "s3cret")).text


def test_password_gate_lets_streamed_answers_through(tmp_path):
    c = make_app(tmp_path, password="s3cret")
    r = c.get("/api/stream", auth=("team", "s3cret"))
    assert r.text == "data: 0\n\ndata: 1\n\ndata: 2\n\n"


def test_a_deployment_without_a_password_refuses_to_start():
    with pytest.raises(RuntimeError, match="APP_PASSWORD"):
        web.install(FastAPI(), password=None, require_password=True)
    with pytest.raises(RuntimeError, match="APP_PASSWORD"):
        web.install(FastAPI(), password="", require_password=True)


def test_install_from_env_reads_the_three_settings(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<html>FinAgent UI</html>")
    monkeypatch.setenv("APP_PASSWORD", "pw")
    monkeypatch.setenv("REQUIRE_PASSWORD", "1")
    monkeypatch.setenv("STATIC_DIR", str(tmp_path))
    app = FastAPI()
    web.install_from_env(app)
    c = TestClient(app)
    assert c.get("/").status_code == 401
    assert "FinAgent UI" in c.get("/", auth=("u", "pw")).text

    monkeypatch.delenv("APP_PASSWORD")
    with pytest.raises(RuntimeError):
        web.install_from_env(FastAPI())


def test_local_development_has_no_gate_and_no_static_mount(monkeypatch):
    for name in ("APP_PASSWORD", "REQUIRE_PASSWORD", "STATIC_DIR"):
        monkeypatch.delenv(name, raising=False)
    app = FastAPI()

    @app.get("/api/ping")
    def ping():
        return {"pong": True}

    web.install_from_env(app)
    assert TestClient(app).get("/api/ping").status_code == 200


def _import_real_app(**env):
    import os
    import subprocess
    import sys
    from pathlib import Path

    clean = {k: v for k, v in os.environ.items() if k not in ("APP_PASSWORD", "REQUIRE_PASSWORD", "STATIC_DIR")}
    return subprocess.run([sys.executable, "-c", "import finagent.api"], capture_output=True, text=True,
                          cwd=Path(__file__).resolve().parent.parent, env={**clean, **env})


def test_the_real_app_refuses_to_start_in_a_deployment_without_a_password():
    r = _import_real_app(REQUIRE_PASSWORD="1")
    assert r.returncode != 0 and "APP_PASSWORD" in r.stderr


def test_the_real_app_starts_with_a_password():
    assert _import_real_app(REQUIRE_PASSWORD="1", APP_PASSWORD="pw").returncode == 0


def test_websocket_connections_need_the_password_too():
    import base64

    from fastapi import WebSocket
    from starlette.websockets import WebSocketDisconnect

    app = FastAPI()

    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        await socket.accept()
        await socket.send_text("hi")
        await socket.close()

    web.install(app, password="s3cret")
    with TestClient(app) as c:                 # entering the context also runs the app's lifespan through the gate
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/ws"):
                pass
        token = base64.b64encode(b"user:s3cret").decode()
        with c.websocket_connect("/ws", headers={"Authorization": f"Basic {token}"}) as socket:
            assert socket.receive_text() == "hi"
