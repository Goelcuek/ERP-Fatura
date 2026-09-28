"""Build a ready-to-run package of the app with Ollama included.

The customer unzips the package and double-clicks Atolye.bat. Nothing else is installed;
on first start the app launches the bundled Ollama and downloads the AI model.

    # Windows package (can be built on Windows, Linux or macOS):
    python scripts/build_bundle.py --platform windows

    # Only put Ollama into this checkout (vendor/ollama) for a normal install / development:
    python scripts/build_bundle.py --platform linux --only-ollama

Options:
    --ollama-version v0.X.Y   pin a release (default: latest GitHub release)
    --cpu-only                drop the GPU runtimes (much smaller; CPU inference only)
    --with-voice              include offline speech recognition (faster-whisper)
    --ollama-archive PATH     use an already downloaded Ollama archive (offline builds)
    --python-archive PATH     use an already downloaded Python embeddable zip (offline builds)

Downloads are checked against the SHA-256 checksums GitHub publishes for each release asset.
"""

import argparse
import base64
import hashlib
import io
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "build" / "cache"
PYTHON_VERSION = "3.12.10"  # embeddable CPython for the Windows package
GITHUB_API = "https://api.github.com/repos/ollama/ollama/releases"
OLLAMA_REPO = "https://github.com/ollama/ollama"
NUGET = "https://api.nuget.org"
ASSETS = {  # preferred archive per platform, first match wins
    "windows": [r"^ollama-windows-amd64\.zip$"],
    "linux": [r"^ollama-linux-amd64\.tgz$", r"^ollama-linux-amd64\.tar\.zst$"],
}
GPU_DIRS = re.compile(r"^(cuda|rocm)", re.I)
APP_FILES = ["app", "run.py", "customer.json", "requirements.txt", "requirements-voice.txt", "README.md"]


class BuildError(Exception):
    pass


def log(msg):
    print(f"  {msg}", flush=True)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url, dest, expected_sha=None):
    dest = Path(dest)
    if dest.exists() and (expected_sha is None or sha256(dest) == expected_sha):
        log(f"cached {dest.name}")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    log(f"downloading {url}")
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        done = 0
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
                done += len(chunk)
                if total:
                    print(f"\r    {done >> 20} / {total >> 20} MB", end="", flush=True)
    print()
    if expected_sha and sha256(tmp) != expected_sha:
        tmp.unlink()
        raise BuildError(f"checksum mismatch for {url}")
    tmp.replace(dest)
    return dest


# ---------------------------------------------------------------- Ollama

def resolve_release(version):
    url = f"{GITHUB_API}/latest" if version == "latest" else f"{GITHUB_API}/tags/{version}"
    try:
        r = requests.get(url, timeout=30, headers={"Accept": "application/vnd.github+json"})
        if r.status_code == 200:
            return r.json()
        reason = f"HTTP {r.status_code}"
    except requests.RequestException as e:
        reason = str(e)
    # the API is rate-limited or blocked on some networks: the release files themselves usually aren't
    log(f"GitHub API unavailable ({reason}); using the release download links directly")
    return release_without_api(version)


def latest_tag():
    """Newest stable Ollama tag, read with git (no GitHub API needed)."""
    out = subprocess.run(["git", "ls-remote", "--tags", "--refs", f"{OLLAMA_REPO}.git"], capture_output=True,
                         text=True, timeout=120)
    tags = re.findall(r"refs/tags/(v(\d+)\.(\d+)\.(\d+))$", out.stdout, re.M)
    if not tags:
        raise BuildError("cannot find the latest Ollama version; pass --ollama-version vX.Y.Z")
    return [t[0] for t in sorted(tags, key=lambda t: tuple(int(x) for x in t[1:]), reverse=True)]


def release_without_api(version):
    """Release info in the API's shape, from the public download links (checksums from sha256sum.txt)."""
    for tag in latest_tag()[:5] if version == "latest" else [version]:
        base = f"{OLLAMA_REPO}/releases/download/{tag}"
        try:  # a tag whose release isn't published yet has no files: try the one before
            sums = requests.get(f"{base}/sha256sum.txt", timeout=30)
        except requests.RequestException as e:
            raise BuildError(f"cannot reach the Ollama downloads: {e}")
        if sums.status_code != 200:
            continue
        names = [ln.split()[1].lstrip("*./") for ln in sums.text.splitlines() if len(ln.split()) == 2]
        assets = [{"name": n, "browser_download_url": f"{base}/{n}"} for n in names]
        assets.append({"name": "sha256sum.txt", "browser_download_url": f"{base}/sha256sum.txt"})
        return {"tag_name": tag, "assets": assets}
    raise BuildError(f"no downloadable Ollama release found for {version}")


def pick_asset(release, platform):
    assets = release.get("assets", [])
    for pattern in ASSETS[platform]:
        for a in assets:
            if re.match(pattern, a["name"]):
                return a
    raise BuildError(f"no {platform} archive in Ollama {release.get('tag_name')}: "
                     f"{', '.join(a['name'] for a in assets)}")


def asset_checksum(release, asset):
    digest = asset.get("digest") or ""
    if digest.startswith("sha256:"):
        return digest.split(":", 1)[1]
    for a in release.get("assets", []):  # older releases publish sha256sum.txt
        if a["name"] == "sha256sum.txt":
            text = requests.get(a["browser_download_url"], timeout=30).text
            for line in text.splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1].lstrip("*./") == asset["name"]:
                    return parts[0]
    raise BuildError(f"no published checksum for {asset['name']}; refusing an unverified download")


def extract(archive, dest):
    archive = Path(archive)
    name = archive.name.lower()
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            safe_members(zf.namelist())
            zf.extractall(dest)
    elif name.endswith((".tgz", ".tar.gz")):
        with tarfile.open(archive, "r:gz") as tf:
            safe_members(tf.getnames())
            _extract_tar(tf, dest)
    elif name.endswith(".tar.zst"):
        try:
            import zstandard
        except ImportError:
            raise BuildError("this Ollama release is .tar.zst: pip install zstandard")
        with open(archive, "rb") as fh:
            data = zstandard.ZstdDecompressor().stream_reader(fh).read()
        with tarfile.open(fileobj=io.BytesIO(data)) as tf:
            safe_members(tf.getnames())
            _extract_tar(tf, dest)
    else:
        raise BuildError(f"unknown archive type: {archive.name}")


def _extract_tar(tf, dest):
    if hasattr(tarfile, "data_filter"):
        tf.extractall(dest, filter="data")
    else:  # Python < 3.12
        tf.extractall(dest)


def safe_members(names):
    for n in names:
        if n.startswith("/") or ".." in Path(n).parts:
            raise BuildError(f"unsafe path in archive: {n}")


def install_ollama(platform, dest, version="latest", archive=None, cpu_only=False):
    dest = Path(dest)
    if archive:
        archive = Path(archive)
        log(f"using local archive {archive.name} (sha256 {sha256(archive)[:16]}…)")
        tag = "local"
    else:
        release = resolve_release(version)
        asset = pick_asset(release, platform)
        checksum = asset_checksum(release, asset)
        tag = release["tag_name"]
        archive = download(asset["browser_download_url"], CACHE / tag / asset["name"], checksum)
        log(f"Ollama {tag} verified (sha256 {checksum[:16]}…)")
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    extract(archive, dest)
    exe = "ollama.exe" if platform == "windows" else "ollama"
    found = next(iter(sorted(dest.rglob(exe), key=lambda p: len(p.parts))), None)
    if found is None:
        raise BuildError(f"{exe} not found in the Ollama archive")
    if platform != "windows":
        found.chmod(0o755)
    removed = 0
    if cpu_only:
        for d in sorted(dest.rglob("*"), key=lambda p: -len(p.parts)):
            if d.is_dir() and GPU_DIRS.match(d.name):
                shutil.rmtree(d)
                removed += 1
    (dest / "VERSION").write_text(f"{tag}\n")
    size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file())
    log(f"Ollama installed in {dest} ({size >> 20} MB{', GPU runtimes removed' if removed else ''})")
    return found


# ---------------------------------------------------------------- Windows package

def install_python(dest, archive=None, version=PYTHON_VERSION):
    """Portable CPython for Windows in `dest`; returns "3.12".

    Source: python.org's embeddable zip, or — when python.org is unreachable — the Python Software
    Foundation's NuGet package (a full portable CPython), verified against NuGet's SHA-512.
    """
    if archive is None:
        url = f"https://www.python.org/ftp/python/{version}/python-{version}-embed-amd64.zip"
        try:
            archive = download(url, CACHE / f"python-{version}-embed-amd64.zip")
        except (requests.RequestException, BuildError) as e:
            log(f"python.org unavailable ({e}); using the Python Software Foundation's NuGet package")
            archive = download_nuget_python(version)
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
    if "tools/python.exe" in names:
        return _install_nuget_python(archive, dest)
    extract(archive, dest)
    pth = next(Path(dest).glob("python3*._pth"), None)
    if pth is None:
        raise BuildError("python*._pth not found in the embeddable Python")
    # enable site-packages and let python find the app next to the python folder
    lines = [ln for ln in pth.read_text().splitlines() if ln.strip() and ln.strip() != "#import site"]
    lines += ["Lib\\site-packages", "..", "import site"]
    pth.write_text("\n".join(dict.fromkeys(lines)) + "\n")
    major_minor = re.search(r"python(\d)(\d+)\._pth", pth.name)
    return f"{major_minor.group(1)}.{major_minor.group(2)}"


def download_nuget_python(version):
    dest = CACHE / f"python.{version}.nupkg"
    try:
        reg = requests.get(f"{NUGET}/v3/registration5-semver1/python/{version}.json", timeout=30).json()
        entry = requests.get(reg["catalogEntry"], timeout=30).json()
    except (requests.RequestException, ValueError, KeyError) as e:
        raise BuildError(f"cannot read the NuGet catalog for Python {version}: {e}")
    if entry.get("packageHashAlgorithm") != "SHA512" or "Python Software Foundation" not in entry.get("authors", ""):
        raise BuildError("unexpected NuGet package metadata for Python; refusing it")
    want = base64.b64decode(entry["packageHash"])
    if not (dest.exists() and hashlib.sha512(dest.read_bytes()).digest() == want):
        download(f"{NUGET}/v3-flatcontainer/python/{version}/python.{version}.nupkg", dest)
        if hashlib.sha512(dest.read_bytes()).digest() != want:
            dest.unlink()
            raise BuildError("checksum mismatch for the NuGet Python package")
    log(f"Python {version} (NuGet) verified (sha512 {want.hex()[:16]}…)")
    return dest


def _install_nuget_python(archive, dest):
    """The NuGet layout keeps CPython in tools/; copy what runs (not the C headers/libs for building)."""
    dest = Path(dest)
    with zipfile.ZipFile(archive) as zf:
        safe_members(zf.namelist())
        for info in zf.infolist():
            rel = info.filename[len("tools/"):] if info.filename.startswith("tools/") else None
            if not rel or info.is_dir() or rel.split("/")[0] in ("include", "libs"):
                continue
            out = dest / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(out, "wb") as fh:
                shutil.copyfileobj(src, fh)
    m = next(filter(None, (re.fullmatch(r"python(\d)(\d+)\.dll", f.name) for f in dest.glob("python*.dll"))), None)
    if not m:
        raise BuildError("python3XX.dll not found in the NuGet Python")
    # a ._pth file makes it behave like the embeddable one: isolated from any other Python on the PC
    # (no registry, no PYTHONPATH, no user site-packages) and finding the app in the folder above
    (dest / f"python{m.group(1)}{m.group(2)}._pth").write_text(
        "Lib\nDLLs\n.\nLib\\site-packages\n..\nimport site\n")
    return f"{m.group(1)}.{m.group(2)}"


def install_wheels(target, py_version, with_voice):
    reqs = ["-r", str(ROOT / "requirements.txt")]
    if with_voice:
        reqs += ["-r", str(ROOT / "requirements-voice.txt")]
    cmd = [sys.executable, "-m", "pip", "install", "--quiet", "--target", str(target), "--platform", "win_amd64",
           "--python-version", py_version, "--implementation", "cp", "--only-binary=:all:", "--upgrade", *reqs]
    log("installing Python packages for Windows")
    subprocess.run(cmd, check=True)


BAT = """@echo off
REM {title}
cd /d "%~dp0"
echo Atolye ERP baslatiliyor... Kapatmak icin bu pencereyi kapatin.
python\\python.exe run.py {args}%*
pause
"""

# run as administrator (asks via UAC), then python -m app.winsetup <action>
ADMIN_BAT = """@echo off
REM {title}
cd /d "%~dp0"
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo Yonetici izni isteniyor...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
chcp 65001 >nul
python\\python.exe -m app.winsetup {action}
echo.
pause
"""

STOP_BAT = """@echo off
REM Arka planda calisan Atolye ERP'yi durdurur (or. guncellemeden once).
cd /d "%~dp0"
chcp 65001 >nul
python\\python.exe -m app.winsetup stop
timeout /t 5
"""

README_TR = (
    "Atolye ERP\r\n\r\n"
    "KURULUM (onerilen)\r\n"
    "1. Bu klasoru kalici bir yere cikarin (or. C:\\Atolye).\r\n"
    "2. Kur.bat dosyasina cift tiklayin ve yonetici iznini onaylayin. Kur.bat:\r\n"
    "   - uygulamayi Windows her acildiginda pencere olmadan baslatir,\r\n"
    "   - masaustune 'Atolye' kisayolu koyar,\r\n"
    "   - telefon/tabletlerin baglanabilmesi icin guvenlik duvarinda izin verir (yalnizca yerel ag),\r\n"
    "   - atolyeye ozel sertifikayi bu bilgisayara tanitir (https, uyari yok),\r\n"
    "   - uygulamayi baslatir ve telefon kurulum sayfasini (QR kodlari) acar.\r\n"
    "3. Ilk acilista yapay zeka modeli arka planda indirilir (2-3 GB, bir kez).\r\n"
    "   Ilerlemeyi Ayarlar > Asistan sayfasinda gorebilirsiniz.\r\n\r\n"
    "TELEFON / TABLET\r\n"
    "Uygulamada sol alttaki telefon simgesine (veya Ayarlar > Telefon ve tabletler) tiklayin ve QR kodlarini\r\n"
    "telefonun kamerasiyla okutun. Telefon ayni Wi-Fi agina bagli olmalidir.\r\n\r\n"
    "DIGER\r\n"
    "Durdur.bat  - arka planda calisan uygulamayi durdurur (guncellemeden once).\r\n"
    "Kaldir.bat  - Kur.bat'in yaptiklarini geri alir. Verileriniz silinmez.\r\n"
    "Atolye.bat / Atolye-HTTPS.bat - kurulum yapmadan, pencere acik kaldigi surece calistirir.\r\n"
    "Tum veriler 'data' klasorundedir; yedekler Ayarlar > Yedekler sayfasindan alinir.\r\n"
    "Kayitlar: data\\logs\\server.log\r\n"
)

NOTICES = """Third-party software included in this package
==============================================

Ollama ({ollama_version}) - https://github.com/ollama/ollama
MIT License, Copyright (c) Ollama.
Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
documentation files (the "Software"), to deal in the Software without restriction, including without limitation the
rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit
persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice
and this permission notice shall be included in all copies or substantial portions of the Software.
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.

Python (python/LICENSE.txt) - Python Software Foundation License.
Python packages in python/Lib/site-packages - see each package's *.dist-info/LICENSE file.

AI models are not included. They are downloaded on first start from the Ollama library under their own licenses
(e.g. Qwen models: see https://ollama.com/library).
"""


def build_windows(out, args):
    name = "Atolye"
    stage = Path(out) / name
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    log(f"staging in {stage}")
    for f in APP_FILES:
        src = ROOT / f
        if src.is_dir():
            shutil.copytree(src, stage / f, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        elif src.exists():
            shutil.copy2(src, stage / f)
    py_version = install_python(stage / "python", args.python_archive)
    if not args.skip_packages:
        install_wheels(stage / "python" / "Lib" / "site-packages", py_version, args.with_voice)
    install_ollama("windows", stage / "vendor" / "ollama", args.ollama_version, args.ollama_archive, args.cpu_only)
    ollama_version = (stage / "vendor" / "ollama" / "VERSION").read_text().strip()
    (stage / "Atolye.bat").write_text(BAT.format(title="Atolye ERP", args="").replace("\n", "\r\n"))
    (stage / "Atolye-HTTPS.bat").write_text(
        BAT.format(title="Atolye ERP (https, telefon/tablet mikrofonu icin)", args="--https ").replace("\n", "\r\n"))
    (stage / "THIRD_PARTY_NOTICES.txt").write_text(NOTICES.format(ollama_version=ollama_version))
    (stage / "Kur.bat").write_text(
        ADMIN_BAT.format(title="Atolye ERP kurulumu: otomatik baslatma, kisayol, guvenlik duvari, sertifika",
                         action="install").replace("\n", "\r\n"))
    (stage / "Kaldir.bat").write_text(
        ADMIN_BAT.format(title="Kur.bat'in yaptiklarini geri alir (veriler silinmez)",
                         action="uninstall").replace("\n", "\r\n"))
    (stage / "Durdur.bat").write_text(STOP_BAT.replace("\n", "\r\n"))
    (stage / "BENIOKU.txt").write_text(README_TR)
    archive = Path(out) / f"{name}-windows-x64.zip"
    log(f"creating {archive.name}")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(stage.rglob("*")):
            if f.is_file():
                zf.write(f, Path(name) / f.relative_to(stage))
    log(f"done: {archive} ({archive.stat().st_size >> 20} MB)")
    return archive


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--platform", choices=["windows", "linux"], default="windows")
    p.add_argument("--ollama-version", default="latest")
    p.add_argument("--ollama-archive")
    p.add_argument("--python-archive")
    p.add_argument("--cpu-only", action="store_true")
    p.add_argument("--with-voice", action="store_true")
    p.add_argument("--only-ollama", action="store_true", help="just install Ollama into vendor/ollama")
    p.add_argument("--skip-packages", action="store_true", help=argparse.SUPPRESS)  # tests
    p.add_argument("--out", default=str(ROOT / "dist"))
    args = p.parse_args(argv)
    try:
        if args.only_ollama:
            install_ollama(args.platform, ROOT / "vendor" / "ollama", args.ollama_version, args.ollama_archive,
                           args.cpu_only)
        elif args.platform == "windows":
            build_windows(args.out, args)
        else:
            raise BuildError("For Linux servers use --only-ollama with start.sh, or docker compose (see README).")
    except (BuildError, requests.RequestException, subprocess.CalledProcessError) as e:
        print(f"\nBuild failed: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
