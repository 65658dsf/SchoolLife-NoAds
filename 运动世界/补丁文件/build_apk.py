"""Reproduce the FIRST no-ad TEST APK (known to fail the internal certificate check).

Use build_startupfix.py for the current certificate-configuration fix.

Requires Python 3.11+ and JDK 21. Never installs, modifies the original APK,
uninstalls an existing application, or overwrites a named output APK.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile

from patch_ads import APK_SHA256, DEX_NAME, patch

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
APK = ROOT / "ydsj.apk"
ANALYSIS = ROOT / ".analysis"
WORK = ANALYSIS / "noads"
SIGNER_SHA256 = "925fb5189d62fea563eaa24636108cb28a7281c05b63f3478f6c53e7768c7d3b"


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def require_command(name: str) -> str:
    value = shutil.which(name)
    if not value:
        raise RuntimeError(f"Required command is missing: {name}")
    return value


def run_logged(command: list[str], log: Path) -> None:
    print(f"Running {Path(command[0]).name}; log: {log.name}", flush=True)
    with log.open("wb") as output:
        result = subprocess.run(command, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}); inspect {log}")


def find_signer(tools_dir: Path | None) -> Path:
    locations = [tools_dir] if tools_dir else [
        ANALYSIS / "tools", ROOT.parent / "乖胖生活" / ".analysis" / "tools",
    ]
    for location in locations:
        candidate = location / "apksigner.jar"
        if candidate.is_file():
            if sha256(candidate) != SIGNER_SHA256:
                raise RuntimeError(f"Unexpected signer checksum: {candidate}")
            return candidate.resolve()
    raise RuntimeError("Place the verified apksigner.jar in .analysis/tools, or pass --tools-dir.")


def signature_entry(name: str) -> bool:
    parts = name.upper().split("/")
    return len(parts) == 2 and parts[0] == "META-INF" and (
        parts[1] == "MANIFEST.MF" or parts[1].endswith((".SF", ".RSA", ".DSA", ".EC"))
    )


def package_unsigned(destination: Path, patched_dex: bytes) -> dict:
    ledger, removed = [], []
    with zipfile.ZipFile(APK) as source, zipfile.ZipFile(destination, "w", allowZip64=True) as target:
        names = source.namelist()
        if len(names) != len(set(names)):
            raise RuntimeError("Duplicate ZIP names are unsupported")
        for info in source.infolist():
            if signature_entry(info.filename):
                removed.append(info.filename)
                continue
            old = source.read(info)
            data = patched_dex if info.filename == "classes.dex" else old
            new = copy.copy(info)
            new.extra = b""
            if new.compress_type == zipfile.ZIP_STORED:
                alignment = 16384 if new.filename.startswith("lib/") and new.filename.endswith(".so") else 4
                data_offset = target.fp.tell() + 30 + len(new.filename.encode("utf-8"))
                padding = (-data_offset) % alignment
                if 0 < padding < 4:
                    padding += alignment
                if padding:
                    new.extra = struct.pack("<HH", 0xCAFE, padding - 4) + bytes(padding - 4)
            target.writestr(new, data)
            ledger.append({"name": info.filename, "sha256": hashlib.sha256(data).hexdigest(),
                           "modified": data != old, "size": len(data)})
    changed = [row["name"] for row in ledger if row["modified"]]
    if changed != ["classes.dex"]:
        raise RuntimeError(f"Unexpected changed entries: {changed}")
    return {"modified_entries": changed, "removed_old_signature_entries": removed, "entries": ledger}


def verify_package(path: Path, ledger: dict) -> dict:
    expected = {row["name"]: row for row in ledger["entries"]}
    aligned = 0
    with zipfile.ZipFile(path) as archive, path.open("rb") as stream:
        names = archive.namelist()
        actual = {name for name in names if not signature_entry(name)}
        if len(names) != len(set(names)) or actual != set(expected):
            raise RuntimeError("APK contents differ from the expected source entry set")
        for info in archive.infolist():
            if signature_entry(info.filename):
                continue
            if hashlib.sha256(archive.read(info)).hexdigest() != expected[info.filename]["sha256"]:
                raise RuntimeError(f"Content mismatch: {info.filename}")
            if info.compress_type == zipfile.ZIP_STORED:
                stream.seek(info.header_offset)
                header = stream.read(30)
                name_length, extra_length = struct.unpack_from("<HH", header, 26)
                offset = info.header_offset + 30 + name_length + extra_length
                alignment = 16384 if info.filename.startswith("lib/") and info.filename.endswith(".so") else 4
                if offset % alignment:
                    raise RuntimeError(f"Unaligned ZIP entry: {info.filename}")
                aligned += 1
    return {"verified_entries": len(expected), "aligned_stored_entries": aligned,
            "native_library_alignment": 16384, "entry_hashes_match": True}


def publish_candidate(candidate: Path, output: Path) -> str:
    """Validate the copied bytes and publish a complete file without overwrite."""
    expected = sha256(candidate)
    handle, name = tempfile.mkstemp(prefix=".ydsj-", suffix=".partial", dir=output.parent)
    temporary = Path(name)
    try:
        with os.fdopen(handle, "wb") as destination, candidate.open("rb") as source:
            shutil.copyfileobj(source, destination)
            destination.flush()
            os.fsync(destination.fileno())
        if sha256(temporary) != expected:
            raise RuntimeError("Published copy differs from the verified signed APK")
        if os.name == "nt":
            os.rename(temporary, output)  # Windows rename refuses an existing destination.
        else:
            os.link(temporary, output)  # Atomic creation, also refuses an existing file.
        return expected
    finally:
        if temporary.exists():
            temporary.unlink()


def build(output: Path, tools_dir: Path | None, prepare_only: bool = False) -> None:
    if (output.exists() or output.with_name(output.name + ".sha256").exists()) and not prepare_only:
        raise RuntimeError(f"Output exists; select a new --output filename: {output}")
    if not APK.is_file() or sha256(APK) != APK_SHA256:
        raise RuntimeError("Unsupported/missing original ydsj.apk; source must match the documented SHA-256")
    WORK.mkdir(parents=True, exist_ok=True)
    recovered = ANALYSIS / "static-recovered" / DEX_NAME
    if not recovered.is_file() or not (recovered.parent / "embedded-07.dex").is_file():
        run_logged([sys.executable, str(SCRIPTS / "recover_payload.py")], WORK / "recover.log")
    with zipfile.ZipFile(APK) as source:
        original = source.read("classes.dex")
    patched, manifest, readable = patch(original, recovered.read_bytes())
    (WORK / "classes-patched.dex").write_bytes(patched)
    readable_dir = WORK / "readable-dex"
    readable_dir.mkdir(exist_ok=True)
    (readable_dir / DEX_NAME).write_bytes(readable)
    (WORK / "patch-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    run_logged([sys.executable, str(SCRIPTS / "test_patches.py")], WORK / "tests.log")
    if prepare_only:
        print("Patch copies and offline checks completed; no APK was packaged.")
        return
    java, keytool = require_command("java"), require_command("keytool")
    signer = find_signer(tools_dir)
    unsigned = WORK / "ydsj-noads-unsigned.apk"
    ledger = package_unsigned(unsigned, patched)
    unsigned_check = verify_package(unsigned, ledger)
    keystore, password = WORK / "local-signing.p12", WORK / "local-signing.pass"
    if keystore.exists() and not password.exists():
        raise RuntimeError("Restore the password file for the existing local signing key")
    if not keystore.exists():
        if not password.exists():
            password.write_text(secrets.token_urlsafe(36), encoding="utf-8")
        run_logged([keytool, "-genkeypair", "-keystore", str(keystore), "-storetype", "PKCS12",
                    "-alias", "ydsj-local-test", "-keyalg", "RSA", "-keysize", "2048", "-validity", "3650",
                    "-dname", "CN=YDSJ Local Test", "-storepass:file", str(password)], WORK / "keytool.log")
    candidate = WORK / "ydsj-noads-signed-candidate.apk"
    run_logged([java, "-jar", str(signer), "sign", "--ks", str(keystore), "--ks-key-alias", "ydsj-local-test",
                "--ks-pass", f"file:{password}", "--v1-signing-enabled", "false", "--v2-signing-enabled", "true",
                "--v3-signing-enabled", "true", "--v4-signing-enabled", "false", "--out", str(candidate), str(unsigned)],
               WORK / "signing.log")
    run_logged([java, "-jar", str(signer), "verify", "--verbose", "--print-certs", str(candidate)], WORK / "signature-verification.txt")
    signed_check = verify_package(candidate, ledger)
    if sha256(APK) != APK_SHA256:
        raise RuntimeError("Original APK changed during the build")
    output.parent.mkdir(parents=True, exist_ok=True)
    published_hash = publish_candidate(candidate, output)
    report = {"source_sha256": APK_SHA256, "source_unchanged": True,
              "output": output.name, "output_sha256": published_hash, "output_size": output.stat().st_size,
              "modified_entries": ledger["modified_entries"], "changed_methods": manifest["patch_count"],
              "unsigned_verification": unsigned_check, "signed_verification": signed_check,
              "signatures_verified": True, "published_copy_matches_verified_candidate": True,
              "runtime_verified": False, "entry_ledger": ledger}
    (WORK / "build-verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with output.with_name(output.name + ".sha256").open("x", encoding="utf-8") as sidecar:
        sidecar.write(report["output_sha256"] + "  " + output.name + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "entry_ledger"}, ensure_ascii=True, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("output/运动世界-noads-test-7.3.80.apk"))
    parser.add_argument("--tools-dir", type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    try:
        build(output.resolve(), args.tools_dir.resolve() if args.tools_dir else None, args.prepare_only)
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile) as error:
        parser.exit(1, f"Build failed: {error}\n")


if __name__ == "__main__":
    main()
