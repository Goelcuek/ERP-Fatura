"""Bundled-Ollama lifecycle against a fake `ollama` executable that speaks Ollama's native API."""

import os
import stat
import sys
import textwrap
import time

import pytest

from app.services.assistant import ollama as ollama_mod
from app.services.assistant.ollama import BUNDLED_PORT, OllamaManager, api_root

FAKE_OLLAMA = textwrap.dedent('''\
    #!{python}
    import json, os, sys, time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    assert sys.argv[1:] == ["serve"], sys.argv
    host, port = os.environ["OLLAMA_HOST"].split(":")
    models = os.environ["OLLAMA_MODELS"]
    marker = os.path.join(models, "installed.json")

    def installed():
        return json.load(open(marker)) if os.path.exists(marker) else []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def reply(self, obj, code=200):
            b = json.dumps(obj).encode()
            self.send_response(code); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
        def do_GET(self):
            if self.path == "/api/version": return self.reply({{"version": "0.0.0-fake"}})
            if self.path == "/api/tags": return self.reply({{"models": [{{"name": n}} for n in installed()]}})
            self.reply({{"error": "not found"}}, 404)
        def do_POST(self):
            req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path != "/api/pull": return self.reply({{"error": "not found"}}, 404)
            if req["model"] == "does-not-exist":
                return self.reply({{"error": "pull model manifest: file does not exist"}}, 500)
            self.send_response(200); self.send_header("Content-Type", "application/x-ndjson"); self.end_headers()
            events = [{{"status": "pulling manifest"}}]
            for done in (0, 500, 1000):
                events.append({{"status": "pulling abc", "digest": "sha256:abc", "total": 1000, "completed": done}})
            events += [{{"status": "verifying sha256 digest"}}, {{"status": "success"}}]
            for e in events:
                self.wfile.write((json.dumps(e) + "\\n").encode()); self.wfile.flush(); time.sleep(0.05)
            json.dump(installed() + [req["model"]], open(marker, "w"))

    ThreadingHTTPServer((host, int(port)), H).serve_forever()
''')


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    base = tmp_path / "app"
    vendor = base / "vendor" / "ollama"
    vendor.mkdir(parents=True)
    exe = vendor / ("ollama.exe" if sys.platform == "win32" else "ollama")
    exe.write_text(FAKE_OLLAMA.format(python=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.delenv("ERP_OLLAMA_BIN", raising=False)
    m = OllamaManager(str(base), str(data))
    yield m
    m.stop()


@pytest.mark.skipif(sys.platform == "win32", reason="fake executable uses a shebang")
def test_bundled_server_start_download_stop(bundle):
    m = bundle
    assert m.bundled and m.bundled_url() == f"http://127.0.0.1:{BUNDLED_PORT}/v1"
    root = api_root(m.bundled_url())
    assert m.start(wait=15)
    assert os.path.isdir(m.models_dir())
    st = m.status(root, "qwen3.5:2b")
    assert st["running"] and not st["installed"]

    m.ensure_model_async(root, "qwen3.5:2b")
    seen = set()
    for _ in range(100):
        snap = m.download.snapshot()
        seen.add(snap["state"])
        if snap["state"] == "done":
            break
        time.sleep(0.05)
    assert snap["state"] == "done" and snap["percent"] == 100 and snap["total"] == 1000
    assert m.status(root, "qwen3.5:2b")["installed"]

    assert m.pull(root, "does-not-exist") is False
    assert "file does not exist" in m.download.snapshot()["message"]

    proc = m.proc
    m.stop()
    assert proc.poll() is not None
    assert not m.running(root)
    assert os.path.exists(os.path.join(m.data_dir, "logs", "ollama.log"))


def test_no_binary_means_not_bundled(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_OLLAMA_BIN", raising=False)
    m = OllamaManager(str(tmp_path), str(tmp_path))
    assert not m.bundled and m.start() is False


def test_api_root():
    assert api_root("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434"
    assert api_root("http://ollama:11434/v1/") == "http://ollama:11434"
    assert api_root("") == ""


def test_effective_url_and_downloading_reply(app, monkeypatch, tmp_path):
    from app.extensions import db
    from app.models import AssistantConversation, Setting, User
    from app.services.assistant import agent

    fake = OllamaManager(str(tmp_path), str(tmp_path))
    fake.binary = "/bin/true"  # pretend a bundled binary exists
    monkeypatch.setattr(ollama_mod, "_manager", fake)
    with app.test_request_context():
        assert agent.settings()["effective_base_url"] == f"http://127.0.0.1:{BUNDLED_PORT}/v1"
        Setting.set("assistant.server", "custom")
        assert agent.settings()["effective_base_url"] == "http://127.0.0.1:11434/v1"
        fake.download.reset("downloading")
        fake.download.parts = {"a": [300, 1000]}
        u = User.query.filter_by(username="admin").one()
        conv = AssistantConversation(user_id=u.id)
        db.session.add(conv)
        db.session.flush()
        res = agent.Session(conv, u).message("bu ay satışlar ne durumda")
        assert "%30" in res["items"][-1]["text"]


def test_model_status_endpoint(client, monkeypatch, tmp_path):
    fake = OllamaManager(str(tmp_path), str(tmp_path))
    monkeypatch.setattr(ollama_mod, "_manager", fake)
    st = client.get("/settings/assistant/model").json
    assert st["running"] is False and st["model"] == "qwen3.5:2b" and st["download"]["state"] == "idle"
    assert client.get("/settings/assistant").status_code == 200
