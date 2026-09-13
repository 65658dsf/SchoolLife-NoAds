"""Add a system-location fallback to application one-shot location clients.

--restore removes this patch layer before reapplying advertising patches.
Backups and SHA-256 checks protect unrelated edits. SDK classes and keys are intact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile

from prepare_analysis import APK, APKTOOL, DECODED, ROOT, WORK, download, require_command, run_logged, sha256, verify_source

SCRIPTS = Path(__file__).resolve().parent
REPORT = WORK / "location-changes.json"
BACKUP = WORK / "location-backup"
CLIENT = "Lcom/amap/api/location/AMapLocationClient;"
COMPAT = "Lcom/qiekj/user/location/CompatibleLocationClient;"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def restore() -> None:
    if not REPORT.exists():
        return
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    pending = []
    for entry in report["files"]:
        path = DECODED / entry["path"]
        original = (BACKUP / entry["path"]).read_bytes()
        if digest(original) != entry["before_sha256"]:
            raise RuntimeError(f"Location backup changed: {path}")
        if sha256(path) not in (entry["before_sha256"], entry["after_sha256"]):
            raise RuntimeError(f"Refuse to overwrite an unrelated change: {path}")
        pending.append((path, original))
    for item in report["helpers"]:
        path = DECODED / item["path"]
        if not path.is_file() or sha256(path) != item["sha256"]:
            raise RuntimeError(f"Location helper changed: {path}")
    for path, original in pending:
        path.write_bytes(original)
    print(f"Restored {len(pending)} files from the previous location patch layer", flush=True)


def compile_helpers() -> list[Path]:
    android = download("android-35.jar")
    r8 = download("r8-8.7.18.jar")
    sources = sorted((SCRIPTS / "location-src").rglob("*.java"))
    stubs = sorted((SCRIPTS / "location-stubs").rglob("*.java"))
    fingerprint = hashlib.sha256(b"location-build-v1-api25")
    for file in sources + stubs:
        fingerprint.update(file.relative_to(SCRIPTS).as_posix().encode())
        fingerprint.update(file.read_bytes())
    work = WORK / "location-build" / fingerprint.hexdigest()[:20]
    classes = work / "classes"
    decoded = work / "decoded"
    marker = work / "verified-helpers.json"
    if marker.exists():
        entries = json.loads(marker.read_text(encoding="utf-8"))
        paths = [decoded / entry["path"] for entry in entries]
        if all(path.is_file() and sha256(path) == entry["sha256"] for path, entry in zip(paths, entries)):
            return paths
        raise RuntimeError(f"Cached helper build changed: {work}")
    if decoded.exists():
        raise RuntimeError(f"Incomplete helper decode. Move this generated directory aside and retry: {work}")
    classes.mkdir(parents=True, exist_ok=True)
    javac = require_command("javac")
    java = require_command("java")
    run_logged([
        javac, "-encoding", "UTF-8", "-source", "8", "-target", "8", "-bootclasspath", str(android),
        "-d", str(classes), *map(str, sources + stubs),
    ], work / "javac.log")
    helper_jar = work / "location-helper.jar"
    stub_jar = work / "compile-only-stubs.jar"
    with zipfile.ZipFile(helper_jar, "w") as helpers, zipfile.ZipFile(stub_jar, "w") as api:
        for file in sorted(classes.rglob("*.class")):
            relative = file.relative_to(classes).as_posix()
            target = helpers if relative.startswith("com/qiekj/user/location/") else api
            target.write(file, relative)
    dex = work / "dex"
    dex.mkdir(exist_ok=True)
    run_logged([
        java, "-cp", str(r8), "com.android.tools.r8.D8", "--min-api", "25",
        "--lib", str(android), "--classpath", str(stub_jar), "--output", str(dex), str(helper_jar),
    ], work / "d8.log")
    # Reuse Apktool's decoder so no separate baksmali distribution is required.
    mini = work / "helper.apk"
    with zipfile.ZipFile(APK) as original, zipfile.ZipFile(mini, "w") as archive:
        for name in ("AndroidManifest.xml", "resources.arsc"):
            archive.writestr(name, original.read(name))
        archive.write(dex / "classes.dex", "classes.dex")
    run_logged([
        java, "-jar", str(APKTOOL), "d", "--no-res", "--no-assets", "-o", str(decoded), str(mini),
    ], work / "decode.log")
    paths = sorted((decoded / "smali").rglob("*.smali"))
    if not paths or any(not file.relative_to(decoded / "smali").as_posix().startswith("com/qiekj/user/location/") for file in paths):
        raise RuntimeError("Helper DEX unexpectedly contains API stubs or other classes")
    marker.write_text(json.dumps([{"path": p.relative_to(decoded).as_posix(), "sha256": sha256(p)} for p in paths], indent=2), encoding="utf-8")
    return paths


def patch() -> None:
    verify_source()
    helpers = compile_helpers()
    # Compose on top of the current advertising patch layer.
    planned = {}
    constructors = 0
    app = DECODED / "smali_classes5/com/qiekj/user"
    for path in sorted(app.rglob("*.smali")):
        if "/location/" in path.as_posix():
            continue
        old = path.read_bytes()
        text = old.decode("utf-8").replace("\r\n", "\n")
        matches = re.findall(r"^    new-instance [^\n]+, " + re.escape(CLIENT) + "$", text, re.M)
        if not matches:
            continue
        count = len(matches)
        constructor = r"^(    invoke-direct(?:/range)? \{[^\n]+\}, )" + re.escape(CLIENT) + r"-><init>\(Landroid/content/Context;\)V$"
        text, calls = re.subn(constructor, lambda m: m[1] + COMPAT + "-><init>(Landroid/content/Context;)V", text, flags=re.M)
        if calls != count:
            raise RuntimeError(f"Unexpected AMap client constructor in {path}")
        text = re.sub(r"^(    new-instance [^\n]+, )" + re.escape(CLIENT) + "$", lambda m: m[1] + COMPAT, text, flags=re.M)
        planned[path] = (old, text.encode("utf-8"))
        constructors += count
    if len(planned) != 20:
        raise RuntimeError(f"Expected 20 application classes with location clients, found {len(planned)}; use --restore before reapplying")

    path = app / "manager/CacheUtil.smali"
    original = path.read_bytes()
    text = original.decode("utf-8").replace("\r\n", "\n")
    pattern = r"(?ms)^\.method public final setLocation\(Lcom/qiekj/user/ui/amap/UserLocation;\)V\n.*?^\.end method"
    block = re.search(pattern, text)
    if block is None:
        raise RuntimeError("CacheUtil.setLocation signature changed")
    anchor = '    invoke-static {p1, v0}, Lkotlin/jvm/internal/Intrinsics;->checkNotNullParameter(Ljava/lang/Object;Ljava/lang/String;)V'
    if block[0].count(anchor) != 1 or "    :cond_0\n" not in block[0]:
        raise RuntimeError("Unexpected CacheUtil.setLocation body")
    guard = f"""

    invoke-static {{p1}}, Lcom/qiekj/user/location/LocationCacheBridge;->normalize(Lcom/qiekj/user/ui/amap/UserLocation;)Lcom/qiekj/user/ui/amap/UserLocation;
    move-result-object p1
    invoke-static {{}}, {COMPAT}->isDeliveringSystemLocation()Z
    move-result v0
    if-nez v0, :cond_0"""
    replaced = block[0].replace(anchor, anchor + guard, 1)
    planned[path] = (original, (text[:block.start()] + replaced + text[block.end():]).encode("utf-8"))

    path = app / "ad/AdExtKt$getLocationInfo$lambda$4$$inlined$onceLocation$1.smali"
    original = path.read_bytes()
    text = original.decode("utf-8").replace("\r\n", "\n")
    wrong_message = r'    const-string/jumbo v1, "\u83b7\u53d6\u5b9a\u4f4d\u6743\u9650\u5931\u8d25"'
    if text.count(wrong_message) != 1:
        raise RuntimeError("Original misleading location error message changed")
    replacement = f"    invoke-static {{p1}}, {COMPAT}->errorMessage(Lcom/amap/api/location/AMapLocation;)Ljava/lang/String;\n    move-result-object v1"
    planned[path] = (original, text.replace(wrong_message, replacement, 1).encode("utf-8"))

    previous = json.loads(REPORT.read_text(encoding="utf-8")) if REPORT.exists() else {"helpers": []}
    old_helpers = {item["path"]: item["sha256"] for item in previous["helpers"]}
    helper_plan = []
    for source in helpers:
        # classes5 is already near the DEX method-reference limit. Keep helper
        # implementations in classes4; app call sites remain in classes5.
        relative = Path("smali_classes4/com/qiekj/user/location") / source.name
        target = DECODED / relative
        data = source.read_bytes()
        if target.exists() and sha256(target) not in (digest(data), old_helpers.get(relative.as_posix())):
            raise RuntimeError(f"Existing location helper has unexpected contents: {target}")
        helper_plan.append((target, data))
    new_helper_paths = {p.relative_to(DECODED).as_posix() for p, _ in helper_plan}
    retired_helpers = []
    for relative, expected in old_helpers.items():
        if relative in new_helper_paths:
            continue
        if not relative.startswith(("smali_classes4/com/qiekj/user/location/", "smali_classes5/com/qiekj/user/location/")):
            raise RuntimeError(f"Unexpected helper path in previous report: {relative}")
        path = DECODED / relative
        if path.exists():
            if sha256(path) != expected:
                raise RuntimeError(f"Refuse to remove a modified obsolete helper: {path}")
            retired_helpers.append(path)
    for path, (before, after) in planned.items():
        backup = BACKUP / path.relative_to(DECODED)
        if backup.exists() and backup.read_bytes() != before:
            raise RuntimeError(f"Location patch input differs from its backup: {path}")
    entries = []
    for path, (before, after) in planned.items():
        relative = path.relative_to(DECODED)
        backup = BACKUP / relative
        backup.parent.mkdir(parents=True, exist_ok=True)
        if not backup.exists():
            backup.write_bytes(before)
        entries.append({"path": relative.as_posix(), "before_sha256": digest(before), "after_sha256": digest(after)})
    helper_entries = [{"path": p.relative_to(DECODED).as_posix(), "sha256": digest(data)} for p, data in helper_plan]
    # Persist recovery hashes before mutations, allowing safe restore after interruption.
    REPORT.write_text(json.dumps({"schema": 1, "client_constructors": constructors, "files": entries, "helpers": helper_entries}, indent=2), encoding="utf-8")
    for path, data in helper_plan:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    for path in retired_helpers:
        path.unlink()
    for path, (before, after) in planned.items():
        path.write_bytes(after)
    print(f"Location patch: {constructors} client constructors in 20 classes, cache and error message, {len(helpers)} helper classes", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restore", action="store_true")
    args = parser.parse_args()
    try:
        restore() if args.restore else patch()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
