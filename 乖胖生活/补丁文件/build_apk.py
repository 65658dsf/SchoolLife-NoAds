"""Apply the patches, assemble two DEX files, package and locally sign an APK.

Run prepare_analysis.py first. Existing signing keys are reused. Existing output
APKs are preserved: use --output to choose a different destination when rebuilding.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import secrets
import subprocess
import sys

from prepare_analysis import (
    ROOT, WORK, DECODED, APKTOOL, SIGNER, SIGNER_SHA256, DOWNLOADS,
    require_command, run_logged, sha256, verify_source,
)


def build(output: Path) -> None:
    verify_source()
    java = require_command("java")
    keytool = require_command("keytool")
    if output.exists():
        raise RuntimeError(f"Output already exists. Use --output with a new APK filename: {output}")
    for tool, expected in ((APKTOOL, DOWNLOADS[APKTOOL.name][1]), (SIGNER, SIGNER_SHA256)):
        if not tool.exists() or sha256(tool) != expected:
            raise RuntimeError(f"Missing or unexpected tool. Run prepare_analysis.py first: {tool}")
    if not (DECODED / "apktool.yml").exists():
        raise RuntimeError("No decoded workspace. Run prepare_analysis.py first.")
    scripts = Path(__file__).resolve().parent
    # The location layer also touches classes whose advertising methods were
    # patched. Restore that layer first so each patch can verify its own input.
    subprocess.run([sys.executable, str(scripts / "patch_location.py"), "--restore"], cwd=ROOT, check=True)
    for name, extra in (("patch_config.py", []), ("patch_sdk.py", ["--apply"]), ("patch_ui.py", []), ("patch_location.py", [])):
        subprocess.run([sys.executable, str(scripts / name), *extra], cwd=ROOT, check=True)
    run_logged([
        java, "-Xmx4g", "-jar", str(APKTOOL), "b", "--no-apk", "-j", "8", str(DECODED),
    ], WORK / "build.log")
    subprocess.run([sys.executable, str(scripts / "package_apk.py")], cwd=ROOT, check=True)

    keystore = WORK / "local-signing.p12"
    password = WORK / "local-signing.pass"
    if keystore.exists() and not password.exists():
        raise RuntimeError(f"Signing password file missing; restore the matching password file: {password}")
    if not keystore.exists():
        if not password.exists():
            password.write_text(secrets.token_urlsafe(36), encoding="utf-8")
        run_logged([
            keytool, "-genkeypair", "-keystore", str(keystore), "-storetype", "PKCS12",
            "-alias", "noads-local", "-keyalg", "RSA", "-keysize", "2048", "-validity", "3650",
            "-dname", "CN=Local APK Test", "-storepass:file", str(password),
        ], WORK / "keytool.log")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Publish the requested filename only after signing and signature verification.
    candidate = WORK / "base-noads-signed-candidate.apk"
    run_logged([
        java, "-jar", str(SIGNER), "sign", "--ks", str(keystore), "--ks-key-alias", "noads-local",
        "--ks-pass", f"file:{password}", "--v1-signing-enabled", "true",
        "--v2-signing-enabled", "true", "--v3-signing-enabled", "true",
        "--v4-signing-enabled", "false", "--out", str(candidate), str(WORK / "base-noads-unsigned.apk"),
    ], WORK / "signing.log")
    run_logged([
        java, "-jar", str(SIGNER), "verify", "--verbose", "--print-certs", str(candidate),
    ], WORK / "signature-verification.txt")
    # Exclusive creation also protects output files that appear while assembling.
    import shutil
    with candidate.open("rb") as source, output.open("xb") as destination:
        shutil.copyfileobj(source, destination)
    print(f"Built and signature-verified: {output}\nSHA-256: {sha256(output)}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("output/base-noads-1.139.0.apk"), help="Output path, relative to the project root unless absolute")
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    try:
        build(output.resolve())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
