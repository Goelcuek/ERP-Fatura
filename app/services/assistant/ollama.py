"""Bundled Ollama: start/stop the model server and download the model on first run.

When the app ships with Ollama (vendor/ollama, see scripts/build_bundle.py) nothing has to be
installed: the app starts `ollama serve` in the background on its own port, keeps models in
data/models/ollama and pulls the configured model automatically the first time.
With an Ollama the shop installed itself (or Docker), only the model download is handled.
"""

import atexit
import json
import os
import platform
import subprocess
import threading
import time
from urllib.parse import urlparse

import requests

BUNDLED_PORT = 11435  # not Ollama's default 11434, so a separately installed Ollama never clashes


def _linux_die_with_parent():
    """preexec_fn: ask the kernel to terminate the child when the app process dies (even on a crash)."""
    try:
        import ctypes
        import signal

        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
    except (OSError, AttributeError):
        pass


def _windows_kill_on_exit(proc):
    """Put the child in a Job Object that Windows kills when the app's last handle closes (app exit or crash)."""
    try:
        import ctypes
        from ctypes import wintypes

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOperationCount", "WriteOperationCount",
                                                         "OtherOperationCount", "ReadTransferCount",
                                                         "WriteTransferCount", "OtherTransferCount")]

        class EXTENDED(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        job = k32.CreateJobObjectW(None, None)
        info = EXTENDED()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        k32.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info))
        k32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(int(proc._handle)))
        return job  # keep the handle open for the app's lifetime
    except Exception:  # never block startup over this; the next start reuses a running server anyway
        return None


def find_binary(base_dir):
    exe = "ollama.exe" if platform.system() == "Windows" else "ollama"
    candidates = [os.environ.get("ERP_OLLAMA_BIN", ""),
                  os.path.join(base_dir, "vendor", "ollama", exe),
                  os.path.join(base_dir, "vendor", "ollama", "bin", exe)]
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def api_root(base_url):
    """http://127.0.0.1:11434/v1 -> http://127.0.0.1:11434 (Ollama's native API)."""
    u = urlparse(base_url or "")
    return f"{u.scheme}://{u.netloc}" if u.scheme and u.netloc else ""


class Download:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self, state="idle"):
        self.state = state  # idle | downloading | done | error
        self.message = ""
        self.parts = {}  # digest -> [completed, total]
        self.model = ""

    def snapshot(self):
        with self.lock:
            done = sum(p[0] for p in self.parts.values())
            total = sum(p[1] for p in self.parts.values())
            return {"state": self.state, "message": self.message, "model": self.model,
                    "completed": done, "total": total, "percent": int(done * 100 / total) if total else 0}


class OllamaManager:
    def __init__(self, base_dir, data_dir, session=None):
        self.base_dir = base_dir
        self.data_dir = data_dir
        self.binary = find_binary(base_dir)
        self.proc = None
        self.log_file = None
        self.http = session or requests.Session()
        self.download = Download()
        self._pull_thread = None

    # -- server ---------------------------------------------------------------

    @property
    def bundled(self):
        return self.binary is not None

    def bundled_url(self):
        return f"http://127.0.0.1:{BUNDLED_PORT}/v1"

    def models_dir(self):
        return os.path.join(self.data_dir, "models", "ollama")

    def running(self, root):
        try:
            return self.http.get(f"{root}/api/version", timeout=3).status_code == 200
        except requests.RequestException:
            return False

    def start(self, wait=30):
        """Start the bundled `ollama serve` unless something already answers on its port."""
        if not self.bundled:
            return False
        root = api_root(self.bundled_url())
        if self.running(root):
            return True
        os.makedirs(self.models_dir(), exist_ok=True)
        logs = os.path.join(self.data_dir, "logs")
        os.makedirs(logs, exist_ok=True)
        env = dict(os.environ)
        env.update({
            "OLLAMA_HOST": f"127.0.0.1:{BUNDLED_PORT}",
            "OLLAMA_MODELS": self.models_dir(),
            "OLLAMA_KEEP_ALIVE": "30m",  # keep the model in memory between requests
            "OLLAMA_NUM_PARALLEL": "1",
            "OLLAMA_MAX_LOADED_MODELS": "1",
        })
        self.log_file = open(os.path.join(logs, "ollama.log"), "ab")
        kwargs = {}
        windows = platform.system() == "Windows"
        if windows:
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        elif platform.system() == "Linux":
            kwargs["preexec_fn"] = _linux_die_with_parent
        self.proc = subprocess.Popen([self.binary, "serve"], env=env, stdout=self.log_file, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, cwd=os.path.dirname(self.binary), **kwargs)
        if windows:
            self._job = _windows_kill_on_exit(self.proc)
        atexit.register(self.stop)
        deadline = time.time() + wait
        while time.time() < deadline:
            if self.proc.poll() is not None:
                return False
            if self.running(root):
                return True
            time.sleep(0.3)
        return False

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        if self.log_file:
            self.log_file.close()
            self.log_file = None

    # -- models ---------------------------------------------------------------

    def installed_models(self, root):
        try:
            resp = self.http.get(f"{root}/api/tags", timeout=5)
            return [m.get("name") or m.get("model") for m in resp.json().get("models", [])]
        except (requests.RequestException, ValueError):
            return None

    def has_model(self, root, model):
        names = self.installed_models(root) or []
        wanted = model if ":" in model else model + ":latest"
        return wanted in names or model in names

    def pull(self, root, model):
        """Blocking download with progress in self.download. Returns True on success."""
        d = self.download
        with d.lock:
            d.reset("downloading")
            d.model = model
            d.message = "pulling manifest"
        try:
            with self.http.post(f"{root}/api/pull", json={"model": model, "stream": True}, stream=True,
                                timeout=(10, 600)) as resp:
                if resp.status_code >= 400:
                    raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                for line in resp.iter_lines():
                    if not line:
                        continue
                    ev = json.loads(line)
                    if ev.get("error"):
                        raise RuntimeError(ev["error"])
                    with d.lock:
                        d.message = ev.get("status", d.message)
                        if ev.get("digest") and ev.get("total"):
                            d.parts[ev["digest"]] = [ev.get("completed", 0), ev["total"]]
                    if ev.get("status") == "success":
                        with d.lock:
                            d.state = "done"
                            for p in d.parts.values():
                                p[0] = p[1]
                        return True
            raise RuntimeError("download ended unexpectedly")
        except (requests.RequestException, RuntimeError, ValueError) as e:
            with d.lock:
                d.state = "error"
                d.message = str(e)
            return False

    def ensure_model_async(self, root, model):
        """Start a background download if the model is missing (no-op when already running)."""
        if self._pull_thread and self._pull_thread.is_alive():
            return
        if not root or not model:
            return

        def work():
            if self.bundled and root == api_root(self.bundled_url()):
                self.start()
            deadline = time.time() + 120  # a separately started server (e.g. Docker) may still be booting
            while not self.running(root) and time.time() < deadline:
                time.sleep(3)
            if self.running(root) and not self.has_model(root, model):
                self.pull(root, model)

        self._pull_thread = threading.Thread(target=work, name="ollama-pull", daemon=True)
        self._pull_thread.start()

    def status(self, root, model):
        running = self.running(root)
        installed = self.has_model(root, model) if running else False
        return {"bundled": self.bundled, "running": running, "model": model, "installed": installed,
                "is_ollama": running, "download": self.download.snapshot()}


_manager = None


def manager(app=None):
    """One manager per process."""
    global _manager
    if _manager is None:
        from flask import current_app

        a = app or current_app
        from ... import BASE_DIR

        _manager = OllamaManager(BASE_DIR, a.config["DATA_DIR"])
    return _manager
