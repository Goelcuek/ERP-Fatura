import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_bundle  # noqa: E402


def fake_ollama_zip(path):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("ollama.exe", b"MZ fake")
        zf.writestr("lib/ollama/ggml-cpu.dll", b"cpu")
        zf.writestr("lib/ollama/cuda_v12/ggml-cuda.dll", b"x" * 1000)
        zf.writestr("lib/ollama/rocm/ggml-hip.dll", b"x" * 1000)
    return path


def fake_python_zip(path):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("python.exe", b"MZ")
        zf.writestr("python312.zip", b"")
        zf.writestr("python312._pth", "python312.zip\n.\n\n# Uncomment to run site.main() automatically\n#import site\n")
        zf.writestr("LICENSE.txt", "PSF")
    return path


def test_windows_bundle_offline(tmp_path):
    out = tmp_path / "dist"
    code = build_bundle.main(["--platform", "windows", "--out", str(out), "--skip-packages", "--cpu-only",
                              "--ollama-archive", str(fake_ollama_zip(tmp_path / "o.zip")),
                              "--python-archive", str(fake_python_zip(tmp_path / "p.zip"))])
    assert code == 0
    stage = out / "Atolye"
    assert (stage / "vendor/ollama/ollama.exe").exists()
    assert (stage / "vendor/ollama/lib/ollama/ggml-cpu.dll").exists()
    assert not (stage / "vendor/ollama/lib/ollama/cuda_v12").exists()  # --cpu-only
    assert not (stage / "vendor/ollama/lib/ollama/rocm").exists()
    pth = (stage / "python/python312._pth").read_text().splitlines()
    assert "import site" in pth and "Lib\\site-packages" in pth and ".." in pth and "#import site" not in pth
    assert (stage / "app/__init__.py").exists() and (stage / "run.py").exists()
    assert not list(stage.rglob("__pycache__"))
    bat = (stage / "Atolye.bat").read_bytes()
    assert b"python\\python.exe run.py" in bat and b"\r\n" in bat
    assert b"--https" in (stage / "Atolye-HTTPS.bat").read_bytes()
    assert "MIT License" in (stage / "THIRD_PARTY_NOTICES.txt").read_text()
    kur = (stage / "Kur.bat").read_bytes()
    assert b"-Verb RunAs" in kur and b"python\\python.exe -m app.winsetup install" in kur and b"\r\n" in kur
    assert b"app.winsetup uninstall" in (stage / "Kaldir.bat").read_bytes()
    assert b"app.winsetup stop" in (stage / "Durdur.bat").read_bytes()
    assert (stage / "app/winsetup.py").exists()
    assert "Kur.bat" in (stage / "BENIOKU.txt").read_text()
    with zipfile.ZipFile(out / "Atolye-windows-x64.zip") as zf:
        names = zf.namelist()
    assert "Atolye/Atolye.bat" in names and "Atolye/vendor/ollama/ollama.exe" in names


def test_bundled_binary_is_found_by_the_app(tmp_path):
    from app.services.assistant.ollama import find_binary

    tgz = tmp_path / "ollama-linux-amd64.tgz"
    src = tmp_path / "src"
    (src / "bin").mkdir(parents=True)
    (src / "bin/ollama").write_text("#!/bin/sh\n")
    with tarfile.open(tgz, "w:gz") as tf:
        tf.add(src / "bin", arcname="bin")
    base = tmp_path / "checkout"
    build_bundle.install_ollama("linux", base / "vendor" / "ollama", archive=tgz)
    assert find_binary(str(base)) == str(base / "vendor/ollama/bin/ollama")


def test_release_asset_selection_and_checksums(monkeypatch):
    release = {"tag_name": "v9.9.9", "assets": [
        {"name": "OllamaSetup.exe", "browser_download_url": "u0"},
        {"name": "ollama-windows-amd64.zip", "browser_download_url": "u1", "digest": "sha256:" + "a" * 64},
        {"name": "ollama-linux-amd64.tgz", "browser_download_url": "u2"},
        {"name": "sha256sum.txt", "browser_download_url": "u3"}]}
    win = build_bundle.pick_asset(release, "windows")
    assert win["name"] == "ollama-windows-amd64.zip"
    assert build_bundle.asset_checksum(release, win) == "a" * 64

    class R:
        text = "b" * 64 + "  ./ollama-linux-amd64.tgz\n" + "c" * 64 + "  ./ollama-darwin.tgz\n"
    monkeypatch.setattr(build_bundle.requests, "get", lambda *a, **k: R())
    linux = build_bundle.pick_asset(release, "linux")
    assert build_bundle.asset_checksum(release, linux) == "b" * 64

    with pytest.raises(build_bundle.BuildError, match="refusing"):
        build_bundle.asset_checksum({"assets": []}, {"name": "x.zip"})


def test_unsafe_archive_rejected(tmp_path):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("../evil.exe", b"x")
    with pytest.raises(build_bundle.BuildError, match="unsafe"):
        build_bundle.extract(bad, tmp_path / "out")


def test_download_verifies_checksum(tmp_path, monkeypatch):
    class Resp:
        headers = {"content-length": "3"}
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def raise_for_status(self): pass
        def iter_content(self, n): yield b"abc"
    monkeypatch.setattr(build_bundle.requests, "get", lambda *a, **k: Resp())
    with pytest.raises(build_bundle.BuildError, match="checksum"):
        build_bundle.download("http://x/f", tmp_path / "f", expected_sha="0" * 64)
    assert not (tmp_path / "f").exists()
    ok = build_bundle.download("http://x/f", tmp_path / "f",
                               expected_sha="ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
    assert ok.read_bytes() == b"abc"


def fake_nuget_python(path):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("python.nuspec", "<package/>")
        for f in ("python.exe", "pythonw.exe", "python3.dll", "python312.dll", "LICENSE.txt"):
            zf.writestr(f"tools/{f}", b"MZ")
        zf.writestr("tools/Lib/os.py", "")
        zf.writestr("tools/DLLs/_ssl.pyd", b"MZ")
        zf.writestr("tools/include/Python.h", "")
        zf.writestr("tools/libs/python312.lib", b"")
    return path


def test_nuget_python_is_isolated_like_the_embeddable_one(tmp_path):
    dest = tmp_path / "python"
    assert build_bundle.install_python(dest, fake_nuget_python(tmp_path / "python.3.12.10.nupkg")) == "3.12"
    assert (dest / "pythonw.exe").exists() and (dest / "Lib/os.py").exists() and (dest / "DLLs/_ssl.pyd").exists()
    assert not (dest / "include").exists() and not (dest / "libs").exists()  # build-only files left out
    pth = (dest / "python312._pth").read_text().splitlines()
    assert pth == ["Lib", "DLLs", ".", "Lib\\site-packages", "..", "import site"]


def test_ollama_release_without_github_api(monkeypatch):
    class R:
        def __init__(self, status, text=""):
            self.status_code, self.text = status, text

    sums = ("aa" * 32 + "  ./ollama-windows-amd64.zip\n" + "bb" * 32 + "  ./ollama-linux-amd64.tgz\n")

    def fake_get(url, **kw):
        if "api.github.com" in url:
            return R(403)
        if "/v9.9.9/" in url:
            return R(404)  # newest tag, release not published yet
        return R(200, sums)

    monkeypatch.setattr(build_bundle.requests, "get", fake_get)
    monkeypatch.setattr(build_bundle, "latest_tag", lambda: ["v9.9.9", "v9.9.8"])
    release = build_bundle.resolve_release("latest")
    assert release["tag_name"] == "v9.9.8"
    win = build_bundle.pick_asset(release, "windows")
    assert win["browser_download_url"] == "https://github.com/ollama/ollama/releases/download/v9.9.8/ollama-windows-amd64.zip"
    assert build_bundle.asset_checksum(release, win) == "aa" * 32
