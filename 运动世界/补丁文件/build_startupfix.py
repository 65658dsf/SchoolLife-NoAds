"""Build the no-ad startup-fix TEST APK; reuse the first test's local key."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import secrets
import sys
import zipfile

from build_apk import (APK, APK_SHA256, ANALYSIS, ROOT, SCRIPTS, find_signer,
                       package_unsigned, publish_candidate, require_command,
                       run_logged, sha256, verify_package)
from patch_ads import DEX_NAME, patch
from startup_fix import patch_expected_certificate

WORK = ANALYSIS / "startup-fix"
KEY_DIR = ANALYSIS / "noads"


def signing_material(keytool: str) -> tuple[Path, Path, bytes]:
    KEY_DIR.mkdir(parents=True, exist_ok=True)
    keystore = KEY_DIR / "local-signing.p12"
    password = KEY_DIR / "local-signing.pass"
    if keystore.exists() and not password.exists():
        raise RuntimeError("Restore the password for the original test signing key; do not replace the key")
    if not keystore.exists():
        if ((KEY_DIR / "signature-verification.txt").exists()
                or (ROOT / "output" / "运动世界-noads-test-7.3.80.apk").exists()):
            raise RuntimeError("Prior test build exists but its signing key is missing; restore local-signing.p12 to preserve update compatibility")
        if not password.exists():
            password.write_text(secrets.token_urlsafe(36), encoding="utf-8")
        run_logged([keytool, "-genkeypair", "-keystore", str(keystore), "-storetype", "PKCS12",
                    "-alias", "ydsj-local-test", "-keyalg", "RSA", "-keysize", "2048", "-validity", "3650",
                    "-dname", "CN=YDSJ Local Test", "-storepass:file", str(password)], WORK / "keytool.log")
    certificate = WORK / "test-certificate.der"
    run_logged([keytool, "-exportcert", "-keystore", str(keystore), "-alias", "ydsj-local-test",
                "-storepass:file", str(password), "-file", str(certificate)], WORK / "export-certificate.log")
    return keystore, password, certificate.read_bytes()


def build(output: Path, tools_dir: Path | None, prepare_only: bool) -> None:
    if not prepare_only and (output.exists() or output.with_name(output.name + ".sha256").exists()):
        raise RuntimeError("Output exists; choose another --output filename")
    if not APK.is_file() or sha256(APK) != APK_SHA256:
        raise RuntimeError("Original APK must match the supported source SHA-256")
    WORK.mkdir(parents=True, exist_ok=True)
    java, keytool = require_command("java"), require_command("keytool")
    keystore, password, certificate = signing_material(keytool)
    recovered = ANALYSIS / "static-recovered" / DEX_NAME
    if not recovered.is_file() or not (recovered.parent / "embedded-07.dex").is_file():
        run_logged([sys.executable, str(SCRIPTS / "recover_payload.py")], WORK / "recover.log")
    with zipfile.ZipFile(APK) as archive:
        original_dex = archive.read("classes.dex")
    ads_dex, ad_manifest, _ = patch(original_dex, recovered.read_bytes())
    final_dex, config_manifest = patch_expected_certificate(ads_dex, certificate)
    (WORK / "classes-before-startupfix.dex").write_bytes(ads_dex)
    (WORK / "classes-patched.dex").write_bytes(final_dex)
    (WORK / "ad-patch-manifest.json").write_text(json.dumps(ad_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (WORK / "config-patch-manifest.json").write_text(json.dumps(config_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    run_logged([sys.executable, str(SCRIPTS / "test_patches.py")], WORK / "ad-tests.log")
    run_logged([sys.executable, str(SCRIPTS / "test_startup_fix.py")], WORK / "startup-tests.log")
    if prepare_only:
        print("Configuration fix and offline checks complete; no APK packaged.")
        return
    signer = find_signer(tools_dir)
    unsigned = WORK / "startupfix-unsigned.apk"
    ledger = package_unsigned(unsigned, final_dex)
    unsigned_check = verify_package(unsigned, ledger)
    candidate = WORK / "startupfix-signed-candidate.apk"
    run_logged([java, "-jar", str(signer), "sign", "--ks", str(keystore), "--ks-key-alias", "ydsj-local-test",
                "--ks-pass", f"file:{password}", "--v1-signing-enabled", "false", "--v2-signing-enabled", "true",
                "--v3-signing-enabled", "true", "--v4-signing-enabled", "false", "--out", str(candidate), str(unsigned)],
               WORK / "signing.log")
    signature_log = WORK / "signature-verification.txt"
    run_logged([java, "-jar", str(signer), "verify", "--verbose", "--print-certs", str(candidate)], signature_log)
    certificate_sha256 = hashlib.sha256(certificate).hexdigest()
    expected_line = "Signer #1 certificate SHA-256 digest: " + certificate_sha256
    if expected_line not in signature_log.read_text(encoding="utf-8"):
        raise RuntimeError("Output signer differs from the certificate used for the configuration patch")
    signed_check = verify_package(candidate, ledger)
    if sha256(APK) != APK_SHA256:
        raise RuntimeError("Original APK changed during the build")
    with zipfile.ZipFile(candidate) as archive:
        if hashlib.sha256(archive.read("classes.dex")).hexdigest() != config_manifest["final_sha256"]:
            raise RuntimeError("Packaged DEX differs from the checked startup fix")
    output.parent.mkdir(parents=True, exist_ok=True)
    output_hash = publish_candidate(candidate, output)
    old_signature_log = KEY_DIR / "signature-verification.txt"
    same_as_first_test = old_signature_log.is_file() and expected_line in old_signature_log.read_text(encoding="utf-8")
    report = {
        "source_sha256": APK_SHA256, "source_unchanged": True,
        "output": output.name, "output_sha256": output_hash, "output_size": output.stat().st_size,
        "modified_entries": ledger["modified_entries"], "changed_ad_methods": ad_manifest["patch_count"],
        "changed_protection_fields": ["c: expected signing-certificate MD5"],
        "other_configuration_fields_unchanged": True, "native_code_unchanged": True,
        "signature_check_still_enabled": True, "certificate_md5": config_manifest["new_expected_md5"],
        "certificate_sha256": certificate_sha256, "same_certificate_as_first_test": same_as_first_test,
        "unsigned_verification": unsigned_check, "signed_verification": signed_check,
        "signatures_verified": True, "published_copy_matches_verified_candidate": True,
        "changed_bytes_in_classes_dex": sum(a != b for a, b in zip(original_dex, final_dex)),
        "runtime_verified": False,
    }
    (WORK / "build-verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (WORK / "entry-ledger.json").write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    with output.with_name(output.name + ".sha256").open("x", encoding="utf-8") as sidecar:
        sidecar.write(output_hash + "  " + output.name + "\n")
    print(json.dumps(report, ensure_ascii=True, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("output/运动世界-noads-startupfix-7.3.80.apk"))
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
