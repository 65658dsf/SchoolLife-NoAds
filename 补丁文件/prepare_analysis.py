"""Create the generated .analysis workspace from the supported original base.apk.

Requires Python 3.10+ and JDK 21 on PATH. No third-party Python packages needed.
Use --decompile to also export application Java sources and Android resources.
Existing decoded files, backups, signing keys and output APKs are preserved.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
from pathlib import Path
import shutil
import subprocess
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent.parent
ANALYSIS = ROOT / ".analysis"
TOOLS = ANALYSIS / "tools"
WORK = ANALYSIS / "noads"
DECODED = WORK / "decoded-v2"
APK = ROOT / ("base.apk" if (ROOT / "base.apk").is_file() else "gpsh.apk")
SOURCE_SHA256 = "68cb64ecbe15e0daf26bfbf7b0fc05d72850a5d5a068ed50565e73d923c97024"
APKTOOL = TOOLS / "apktool_3.0.3.jar"
SIGNER = TOOLS / "apksigner.jar"
DOWNLOADS = {
    "android-35.jar": (
        "https://android.googlesource.com/platform/prebuilts/sdk/+/refs/heads/main/35/public/android.jar?format=TEXT",
        "ee568219aa3754977207a8fd849fdbf3a299658dd26dd0907d39ca830587f8aa",
    ),
    "r8-8.7.18.jar": (
        "https://dl.google.com/dl/android/maven2/com/android/tools/r8/8.7.18/r8-8.7.18.jar",
        "58366f77067207c39a17d469de7b05701d2877212a9c55201bcb0af43e59e903",
    ),
    "apktool_3.0.3.jar": (
        "https://github.com/iBotPeaches/Apktool/releases/download/v3.0.3/apktool_3.0.3.jar",
        "dbf930b076c6b9be08d57c449cacefc3bdd6b71ebd59b3066fc0e1f5b14f9423",
    ),
    "uber-apk-signer-1.3.0.jar": (
        "https://github.com/patrickfav/uber-apk-signer/releases/download/v1.3.0/uber-apk-signer-1.3.0.jar",
        "e1299fd6fcf4da527dd53735b56127e8ea922a321128123b9c32d619bba1d835",
    ),
    "jadx.zip": (
        "https://github.com/skylot/jadx/releases/download/v1.5.6/jadx-1.5.6.zip",
        "545ea2be9c242511bc145755cf4bda2485ade42966e096f8b4d3da2a230e8974",
    ),
}
SIGNER_SHA256 = "925fb5189d62fea563eaa24636108cb28a7281c05b63f3478f6c53e7768c7d3b"


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        result = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def require_command(name: str) -> str:
    command = shutil.which(name)
    if command is None:
        raise RuntimeError(f"Cannot find {name} on PATH. Install Python 3.10+ and JDK 21 first.")
    return command


def verify_source() -> None:
    if not APK.is_file():
        raise RuntimeError(f"Place the original supported APK at: {APK}")
    actual = sha256(APK)
    if actual != SOURCE_SHA256:
        raise RuntimeError(f"Unsupported base.apk SHA-256: {actual}; expected {SOURCE_SHA256}")


def download(name: str) -> Path:
    url, expected = DOWNLOADS[name]
    target = TOOLS / name
    TOOLS.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if sha256(target) != expected:
            raise RuntimeError(f"Tool checksum mismatch; move the unexpected file aside: {target}")
        return target
    print(f"Downloading {name} from {url}", flush=True)
    partial = target.with_name(target.name + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "PGSH-NoAds-build"})
    with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as output:
        if name == "android-35.jar":
            output.write(base64.b64decode(response.read()))
        else:
            shutil.copyfileobj(response, output)
    if sha256(partial) != expected:
        raise RuntimeError(f"Downloaded tool checksum mismatch: {partial}")
    partial.replace(target)
    return target


def run_logged(command: list[str], log: Path) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    print(f"Running {Path(command[0]).name}; log: {log}", flush=True)
    with log.open("wb") as output:
        result = subprocess.run(command, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT)
    if result.returncode:
        tail = log.read_text(encoding="utf-8", errors="replace")[-6000:]
        raise RuntimeError(f"Command failed ({result.returncode}). See {log}\n{tail}")


def prepare(decompile: bool = False) -> None:
    java = require_command("java")
    verify_source()
    WORK.mkdir(parents=True, exist_ok=True)
    download("apktool_3.0.3.jar")
    download("android-35.jar")
    download("r8-8.7.18.jar")
    uber = download("uber-apk-signer-1.3.0.jar")
    if not SIGNER.exists():
        with zipfile.ZipFile(uber) as archive:
            SIGNER.write_bytes(archive.read("lib/apksigner_33_0_2.jar"))
    if sha256(SIGNER) != SIGNER_SHA256:
        raise RuntimeError(f"Tool checksum mismatch: {SIGNER}")

    if DECODED.exists():
        required = ["apktool.yml", "smali", "smali_classes4", "smali_classes5"]
        if not all((DECODED / name).exists() for name in required):
            raise RuntimeError(f"Incomplete decode at {DECODED}; move it aside before retrying.")
        print(f"Reusing existing decoded files without overwriting: {DECODED}", flush=True)
    else:
        selected = WORK / "input-selected-v2.apk"
        with zipfile.ZipFile(APK) as source, zipfile.ZipFile(selected, "w", zipfile.ZIP_DEFLATED) as target:
            for name in ("AndroidManifest.xml", "resources.arsc", "classes4.dex", "classes5.dex"):
                target.writestr(name, source.read(name))
            # Apktool needs classes.dex to detect secondary DEX entries. This small
            # original DEX is a decoding placeholder only; it is never repackaged.
            target.writestr("classes.dex", source.read("classes2.dex"))
        run_logged([
            java, "-Xmx4g", "-jar", str(APKTOOL), "d", "--no-res", "--no-assets",
            "-j", "8", "-o", str(DECODED), str(selected),
        ], WORK / "decode-v2.log")

    if decompile:
        jadx_zip = download("jadx.zip")
        jadx = TOOLS / "jadx"
        jar = jadx / "lib" / "jadx-1.5.6-all.jar"
        if not jar.exists():
            with zipfile.ZipFile(jadx_zip) as archive:
                archive.extractall(jadx)
        destination = ANALYSIS / "decompiled"
        run_logged([
            java, "-Xmx6g", "-cp", str(jar), "jadx.cli.JadxCLI", "--no-src",
            "-d", str(destination), "--log-level", "warn", str(APK),
        ], ANALYSIS / "decompile-resources.log")
        run_logged([
            java, "-Xmx6g", "-cp", str(jar), str(ROOT / "补丁文件" / "DecompileApp.java"),
            str(APK), str(destination),
        ], ANALYSIS / "decompile-app.log")
        print("Java export finished. Check decompile-app.log for JADX warnings/errors.", flush=True)
    print(f"Analysis workspace ready: {ANALYSIS}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decompile", action="store_true", help="Also export readable Java and Android resources with JADX")
    args = parser.parse_args()
    try:
        prepare(args.decompile)
    except (OSError, RuntimeError, zipfile.BadZipFile) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
