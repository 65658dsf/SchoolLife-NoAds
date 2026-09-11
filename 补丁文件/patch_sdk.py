"""Reproducible pgad-only no-ad patch; preserve SDK initialization and failure callbacks.

Usage: python patch_sdk.py [--decoded PATH] --apply
       python patch_sdk.py [--decoded PATH] --check
Default is a read-only preflight. All method declarations are matched exactly.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


HERE = Path(__file__).resolve().parent.parent / ".analysis" / "noads"
PREFIX = "com/qiekeji/pgad/"
AD_LISTENER = "Lcom/qiekeji/pgad/interfaces/AdListener;"
VIDEO_LISTENER = "Lcom/qiekeji/pgad/interfaces/VideoAdListener;"
MI_VIDEO_LISTENER = "Lcom/qiekeji/pgad/interfaces/RewardVideoAdListener;"
MESSAGE = "广告已停用"
MARKER = "# noads-sdk: fail unavailable; never report ad/reward completion"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def failed_body(owner: str, listener: str, reward: bool = False) -> str:
    lines = ["    .locals 3", "", "    " + MARKER]
    if reward:
        lines += ["    const/4 v0, 0x0", f"    iput-boolean v0, p0, {owner}->isReward:Z"]
    lines += [
        f"    iget-object v0, p0, {owner}->mListener:{listener}",
        "    if-eqz v0, :noads_done",
        "    const/4 v1, -0x1",
        f'    const-string v2, "{MESSAGE}"',
        f"    invoke-interface {{v0, v1, v2}}, {listener}->onAdFailed(ILjava/lang/String;)V",
        "    :noads_done",
        "    return-void",
    ]
    return "\n".join(lines) + "\n"


FALSE_BODY = "    .locals 1\n\n    " + MARKER + "\n    const/4 v0, 0x0\n    return v0\n"


def call_void_body(owner: str, name: str, invocation: str = "invoke-virtual") -> str:
    return (
        "    .locals 0\n\n    " + MARKER + "\n"
        f"    {invocation} {{p0}}, {owner}->{name}()V\n"
        "    return-void\n"
    )


def specifications() -> list[dict]:
    rows = [
        ("manager/gromore/SplashAd", "loadAd(Landroid/app/Activity;Ljava/lang/String;II)V", "showAd(Landroid/view/ViewGroup;)V", AD_LISTENER, False),
        ("manager/gromore/FeedAd", "load(Landroid/app/Activity;Ljava/lang/String;III)V", "show(Landroid/app/Activity;Landroid/view/ViewGroup;)V", AD_LISTENER, False),
        ("manager/gromore/InterstitialAd", "load(Landroid/app/Activity;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;)V", "show(Landroid/app/Activity;)V", AD_LISTENER, False),
        ("manager/gromore/RewardVideoAd", "load(Landroid/app/Activity;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;)V", "showVideoRender(Landroid/app/Activity;)V", VIDEO_LISTENER, True),
        ("manager/taku/TakuSplashAd", "loadAd(Landroid/app/Activity;Ljava/lang/String;II)V", "showAd(Landroid/view/ViewGroup;)V", AD_LISTENER, False),
        ("manager/taku/TakuNativeAd", "load(Landroid/app/Activity;Ljava/lang/String;)V", "show(Landroid/app/Activity;Landroid/view/ViewGroup;)V", AD_LISTENER, False),
        ("manager/taku/TakuInterstitialAd", "load(Landroid/app/Activity;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;)V", "show(Landroid/app/Activity;)V", AD_LISTENER, False),
        ("manager/taku/TakuRewardVideoAd", "load(Landroid/app/Activity;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;ILjava/lang/String;)V", "showVideoRender(Landroid/app/Activity;)V", VIDEO_LISTENER, True),
        ("manager/xiaomi/MiSplashAd", "loadAd(Ljava/lang/String;)V", "showAd(Landroid/view/ViewGroup;)V", AD_LISTENER, False),
        ("manager/xiaomi/MiInterstitialAd", "loadAd(Ljava/lang/String;)V", "showAd(Landroid/app/Activity;)V", AD_LISTENER, False),
        ("manager/xiaomi/MiRewardVideoAd", "loadAd(Ljava/lang/String;)V", "showAd(Landroid/app/Activity;)V", MI_VIDEO_LISTENER, False),
        ("view/SplashAd", "loadData()V", "showAd(Landroid/view/ViewGroup;)V", AD_LISTENER, False),
        ("view/InterstitialAd", "loadData()V", "showAd(Landroid/app/Activity;)V", AD_LISTENER, False),
    ]
    specs = []

    def add(cls: str, declaration: str, body: str, reason: str, fields=()):
        specs.append({"class": cls, "declaration": declaration, "body": body, "reason": reason, "fields": fields})

    for cls, load, show, listener, reward in rows:
        owner = "L" + PREFIX + cls + ";"
        fields = [f".field private mListener:{listener}"]
        if reward:
            fields.append(".field private isReward:Z")
        for signature, reason in ((load, "Disable load with one unavailable callback"), (show, "Defensive direct/cached show failure; release caller loading")):
            add(cls, ".method public final " + signature, failed_body(owner, listener, reward), reason, fields)
        if reward:
            # Actual DEX getter is getReward(); JADX renamed it getIsReward().
            add(cls, ".method public final getReward()Z", FALSE_BODY, "No reward is available when ads are disabled", fields)

    for signature in ("prepare(Landroid/app/Activity;Ljava/lang/String;)Z", "isCacheReady()Z"):
        add("manager/taku/TakuSplashAd", ".method public final " + signature, FALSE_BODY, "Disable the Taku cached splash bypass")

    # These wrappers own only ad overlays; dismiss preserves their cleanup.
    for cls, load, show_flags in (
        ("manager/HotSplashAd", "load(Landroid/app/Activity;)V", "public"),
        ("manager/HotSplashAdV2", "loadAndShow(Landroid/app/Activity;)V", "private"),
    ):
        owner = "L" + PREFIX + cls + ";"
        body = call_void_body(owner, "dismiss")
        add(cls, ".method public final " + load, body, "Do not create an ad overlay; clean up any existing overlay")
        add(cls, ".method " + show_flags + " final show(Landroid/app/Activity;)V", body, "Do not create an ad overlay; clean up any existing overlay")

    add("manager/gromore/HotSplashAct", ".method public final loadAndShow()V", call_void_body("Landroid/app/Activity;", "finish"), "An ad-only Activity must finish rather than remain blank")
    return specs


def method_block(text: str, declaration: str) -> re.Match:
    # Match declaration flags, name and complete descriptor, never a substring.
    pattern = re.compile(r"^" + re.escape(declaration) + r"\n(?P<body>[\s\S]*?)^\.end method(?=\n|$)", re.MULTILINE)
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one declaration: {declaration}; found {len(matches)}")
    return matches[0]


def locate(decoded: Path, cls: str) -> Path:
    candidates = [p for p in decoded.glob("smali*") if p.is_dir()]
    paths = [p / PREFIX / (cls + ".smali") for p in candidates]
    found = [p.resolve() for p in paths if p.is_file()]
    if len(found) != 1:
        raise RuntimeError(f"Expected exactly one class {cls}, found {len(found)}")
    path = found[0]
    relative = path.relative_to(decoded)
    if not relative.as_posix().split("/", 1)[1].startswith(PREFIX):
        raise RuntimeError(f"Refuse mutation outside the exclusive pgad scope: {path}")
    return path


def build_plan(decoded: Path) -> list[dict]:
    grouped = {}
    for spec in specifications():
        grouped.setdefault(spec["class"], []).append(spec)
    plan = []
    for cls, specs in grouped.items():
        path = locate(decoded, cls)
        old_bytes = path.read_bytes()
        old = old_bytes.decode("utf-8").replace("\r\n", "\n")
        if MARKER in old:
            raise RuntimeError(f"Already patched class {cls}; use --check, or begin from the original decoded APK")
        expected_owner = "L" + PREFIX + cls + ";"
        if not re.search(r"^\.class [^\n]* " + re.escape(expected_owner) + "$", old, re.MULTILINE):
            raise RuntimeError(f"Class descriptor mismatch in {path}")
        new = old
        changes = []
        for spec in specs:
            for field in spec["fields"]:
                if len(re.findall(r"^" + re.escape(field) + "$", old, re.MULTILINE)) != 1:
                    raise RuntimeError(f"Missing or unexpected listener/reward field in {path}: {field}")
            original = method_block(old, spec["declaration"])
            current = method_block(new, spec["declaration"])
            replacement = spec["declaration"] + "\n" + spec["body"] + ".end method"
            if any(token in spec["body"] for token in ("->onLoaded(", "->onAdClosed(", "->onReward(", "->onRewardResult(", "->onVideoComplete(")):
                raise RuntimeError("Forbidden success/close/reward callback in patch")
            new = new[:current.start()] + replacement + new[current.end():]
            changes.append({
                "declaration": spec["declaration"],
                "original_line": old[:original.start()].count("\n") + 1,
                "reason": spec["reason"],
                "before_method_sha256": sha(original.group().encode("utf-8")),
                "after_method_sha256": sha(replacement.encode("utf-8")),
            })
        newline = "\r\n" if b"\r\n" in old_bytes else "\n"
        new_bytes = new.replace("\n", newline).encode("utf-8")
        plan.append({"path": path, "relative": path.relative_to(decoded).as_posix(), "before": old_bytes, "after": new_bytes, "methods": changes})
    return plan


def check(decoded: Path, manifest_path: Path):
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    count = 0
    for entry in data["files"]:
        path = decoded / entry["path"]
        raw = path.read_bytes()
        if sha(raw) != entry["after_sha256"]:
            raise RuntimeError(f"Patched class hash mismatch: {path}")
        backup = manifest_path.parent / entry["backup"]
        if sha(backup.read_bytes()) != entry["before_sha256"]:
            raise RuntimeError(f"Original backup hash mismatch: {backup}")
        text = raw.decode("utf-8").replace("\r\n", "\n")
        for change in entry["methods"]:
            block = method_block(text, change["declaration"]).group()
            if sha(block.encode("utf-8")) != change["after_method_sha256"]:
                raise RuntimeError(f"Patched method mismatch: {change['declaration']}")
            count += 1
    print(json.dumps({"status": "verified", "files": len(data["files"]), "methods": count, "manifest": str(manifest_path)}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decoded", type=Path, default=HERE / "decoded-v2")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--apply", action="store_true")
    group.add_argument("--check", action="store_true")
    args = parser.parse_args()
    decoded = args.decoded.resolve(strict=True)
    manifest_path = decoded.parent / "sdk-changes.json"
    if args.check:
        check(decoded, manifest_path)
        return
    if manifest_path.exists():
        # Re-running --apply is deliberately non-destructive and hash checked.
        check(decoded, manifest_path)
        return
    plan = build_plan(decoded)  # Validate every class before creating backups or writes.
    print(json.dumps({"status": "preflight-ok", "files": len(plan), "methods": sum(len(p["methods"]) for p in plan), "apply": args.apply}, ensure_ascii=False))
    if not args.apply:
        return
    backup_root = decoded.parent / "sdk-backup"
    for p in plan:
        backup = backup_root / p["relative"]
        if backup.exists() and backup.read_bytes() != p["before"]:
            raise RuntimeError(f"Refuse to overwrite a differing backup: {backup}")
    # All original classes are persisted before any class mutation.
    for p in plan:
        backup = backup_root / p["relative"]
        backup.parent.mkdir(parents=True, exist_ok=True)
        if not backup.exists():
            backup.write_bytes(p["before"])
    files = []
    for p in plan:
        p["path"].write_bytes(p["after"])
        files.append({"path": p["relative"], "backup": (Path("sdk-backup") / p["relative"]).as_posix(), "before_sha256": sha(p["before"]), "after_sha256": sha(p["after"]), "methods": p["methods"]})
    manifest_path.write_text(json.dumps({"schema_version": 1, "scope": PREFIX, "decoded": str(decoded), "callback": {"method": "onAdFailed", "code": -1, "message": MESSAGE}, "reward_success": False, "sdk_initialization_changed": False, "files": files}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    check(decoded, manifest_path)


if __name__ == "__main__":
    main()
