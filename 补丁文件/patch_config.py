"""Reproducibly filter only audited visual-ad slot keys from AdBean constructors.

No remote calls, reward simulation, broad ImageBean mutation or navigation edits.
Only AdBean.smali and the new NoAdsSlots.smali are changed under decoded-v2.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parent.parent
ROOT = WORKSPACE / ".analysis" / "noads"
PACKAGE = Path("smali_classes5/com/qiekj/user/entity/home")
TARGET = ROOT / "decoded-v2" / PACKAGE / "AdBean.smali"
HELPER = TARGET.with_name("NoAdsSlots.smali")
BACKUP = ROOT / "config-backup" / "AdBean.smali"
SLOTS_SOURCE = ROOT / "decoded-v2/smali_classes5/com/qiekj/user/ad/SlotKeyKt.smali"
CLASS = "Lcom/qiekj/user/entity/home/AdBean;"
HELPER_CLASS = "Lcom/qiekj/user/entity/home/NoAdsSlots;"
FILTER = HELPER_CLASS + "->filter(Ljava/lang/String;Ljava/util/List;)Ljava/util/List;"

# An explicit set, not name/prefix matching. Mixed business configuration,
# newcomer/benefit activities, navigation entries, and unknown keys are kept.
BLOCKED_NAMES = """
AFTER_DATA_PACKAGE AFTER_PAY_STATUS_CSJ AFTER_PAY_SUCCESS_DIALOG
AFTER_STARTUP_VIDEO BEFORE_DEVICE_START_CSJ BEFORE_STARTUP_VIDEO
CLOSE_TIP_BANNER COMMUNITY_BANNER COMMUNITY_BANNER_2 COMMUNITY_FLOAT_AD
DEVICE_FLOAT_AD DEVICE_FLOAT_AD_FALLBACK DEVICE_NUMBER_CARD
DEVICE_STARTUP__AFTER_FIXED DEVICE_STARTUP__FIXED DEVICE_TOP_BANNER
DRYER_SKU_BANNER HAIR_SKU_BANNER WASH_SKU_BANNER
HOME_DIALOG HOME_DIALOG_V2 HOME_EXIT_DIALOG HOME_FENCE_LEFT
HOME_FENCE_RIGHT_BOTTOM HOME_FENCE_RIGHT_TOP HOME_FIRST_BANNER
HOME_FLOAT_AD HOME_FLOAT_AD_FALLBACK HOME_HEAD_BANNER HOME_MALL_BANNER
HOME_MALL_LEFT HOME_MALL_RIGHT HOME_PAY_SCUESS_RETURN_DIALOG
HOME_PAY_SUCCESS_RETURN_VIDEO HOME_THREE_BANNER HOME_THREE_NATIVE_FLOW
HOME_TURNTABLE_AD HOME_TURNTABLE_AD_FALLBACK
INTEGRAL_INTERSTITIAL INTEGRAL_REWARD_FLOW_AD INTEGRAL_SIGN_RESULT_AD
MOXI_GAME_VIDEO MY_BOTTOM MY_DEVICE_BUTTON MY_DEVICE_DIALOG
MY_DEVICE_FLOAT_AD MY_DEVICE_FLOAT_AD_FALLBACK MY_FRAGMENT_DIALOG
ORDER_DETAIL_BOTTOM ORDER_DETAIL_CSJ ORDER_DETAIL_DIALOG ORDER_DETAIL_FIXED
ORDER_DETAIL_NUMBER_CARD ORDER_DIALOG ORDER_LIST_SUSPENSION ORDER_LIST_TOP
ORDER_PREVIEW_BOTTOM_BANNER PAY_SUCCESS_BOTTOM_BANNER SCAN_ADX_ACTIVITY
SCAN_BOTTOM SCAN_PASTEBOARD_AD SCAN_SUSPENSION SPLASH_COLD SPLASH_DEFAULT_AD
SPLASH_HOT VIVO_INTEGRAL_INTERSTITIAL VIVO_ORDER_DIALOG
""".split()

BLOCKED_GETTERS = {
    "getDeviceDialogKey": "android_device_dialog_1_35_0",
    "getPaySuccessDialogKey": "android_pay_success_dialog_1_35_0",
    "getAfterUnlockSuccessDialogKey": "android_unlock_success_dialog_1_35_0",
}

ORDINARY = ".method public constructor <init>(Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;Ljava/util/List;)V"
SERIALIZED = ".method public synthetic constructor <init>(ILjava/lang/String;Ljava/lang/String;Ljava/lang/String;Ljava/util/List;Lkotlinx/serialization/internal/SerializationConstructorMarker;)V"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def method_parts(text: str) -> dict[str, str]:
    return {
        match.group(0).splitlines()[0]: match.group(0)
        for match in re.finditer(r"(?m)^\.method .*?^\.end method", text, re.S)
    }


def patch_assignment(body: str, register: str, slot_register: str | None) -> str:
    anchor = f"    iput-object {register}, p0, {CLASS}->images:Ljava/util/List;"
    if body.count(anchor) != 1:
        raise RuntimeError(f"Unexpected constructor images assignment: {register}")
    if slot_register is None:
        prefix = f"    iget-object v0, p0, {CLASS}->slotKey:Ljava/lang/String;\n\n"
        slot_register = "v0"
    else:
        prefix = ""
    injected = (
        "    # NoAdsSlots: keep the stored list and constructor iteration consistent.\n"
        + prefix
        + f"    invoke-static {{{slot_register}, {register}}}, {FILTER}\n\n"
        + f"    move-result-object {register}\n\n"
        + anchor
    )
    return body.replace(anchor, injected, 1)


def helper_text(blocked: list[str]) -> str:
    lines = [
        f".class public final {HELPER_CLASS}",
        ".super Ljava/lang/Object;",
        '.source "NoAdsSlots.smali"',
        "",
        "# Exact visual-ad slots only. Unknown, null and empty keys retain data.",
        ".method public static filter(Ljava/lang/String;Ljava/util/List;)Ljava/util/List;",
        "    .locals 2",
        "",
    ]
    for slot in blocked:
        lines += [
            f'    const-string/jumbo v0, "{slot}"',
            "",
            "    invoke-virtual {v0, p0}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z",
            "",
            "    move-result v1",
            "",
            "    if-nez v1, :blocked",
            "",
        ]
    lines += [
        "    return-object p1",
        "",
        "    :blocked",
        "    new-instance v0, Ljava/util/ArrayList;",
        "",
        "    invoke-direct {v0}, Ljava/util/ArrayList;-><init>()V",
        "",
        "    return-object v0",
        ".end method",
        "",
    ]
    return "\n".join(lines)


def read_slots(source: str) -> tuple[dict[str, str], list[str]]:
    """Read the original decoded constants without needing a JADX Java export."""
    fields = re.findall(
        r'^\.field public static final (\w+):Ljava/lang/String; = "([^"\\]+)"\s*$',
        source,
        re.M,
    )
    constants = dict(fields)
    if len(constants) != len(fields):
        raise RuntimeError("Duplicate named slot constants")
    missing = sorted(set(BLOCKED_NAMES) - constants.keys())
    if missing:
        raise RuntimeError(f"Unknown named slots: {missing}")
    for getter, expected in BLOCKED_GETTERS.items():
        signature = f".method public static final {getter}()Ljava/lang/String;"
        matches = re.findall(
            rf'^{re.escape(signature)}\r?\n(.*?)^\.end method\s*$',
            source,
            re.M | re.S,
        )
        if len(matches) != 1:
            raise RuntimeError(f"Missing or duplicate slot getter: {getter}")
        # Ignore assembler/debug directives; require the exact two instructions
        # so changed branching or a different return value cannot pass unnoticed.
        instructions = [
            line.strip() for line in matches[0].splitlines()
            if line.strip() and not line.lstrip().startswith((".", "#"))
        ]
        if len(instructions) != 2 or not re.fullmatch(
            rf'const-string(?:/jumbo)? (v\d+), "{re.escape(expected)}"\nreturn-object \1',
            "\n".join(instructions),
        ):
            raise RuntimeError(f"Slot getter changed: {getter}")
    blocked = sorted({constants[name] for name in BLOCKED_NAMES} | set(BLOCKED_GETTERS.values()))
    if len(blocked) != 70:
        raise RuntimeError(f"Expected exactly 70 audited ad slots, found {len(blocked)}")
    return constants, blocked


def main() -> None:
    constants, blocked = read_slots(SLOTS_SOURCE.read_text(encoding="utf-8"))
    protected = {
        "android_integral_h5_switch", "main_csj_mall_switch",
        "android_home_newcomer_switch", "android_point_home_online_earning",
        "community_home_youzan", "android_social_challenge",
        "android_integral_goose_egg_alert", "android_home_goose_egg_alert",
        "android_topic_activity_banner", "appPop", "android_douyin_switch",
        "integral_csj_mall_ad", "android_bank_subsidy_activity_ad",
        "android_home_local_life_ad", "android_home_penguin_welfare",
        "android_integral_masyq", "android_home_top_image_popup",
        "android_integral_guide_dialog", "android_integral_gain_dialog",
        "android_home_sign_tips_dialog", "home_douyin_traffic_split",
    }
    if protected.intersection(blocked):
        raise RuntimeError("Protected business slot included in blacklist")
    current = TARGET.read_bytes()
    original = BACKUP.read_bytes() if BACKUP.exists() else current
    text = original.decode("utf-8").replace("\r\n", "\n")
    if HELPER_CLASS in text:
        raise RuntimeError("Backup/input is already patched; refuse ambiguous source")
    methods = method_parts(text)
    ordinary = patch_assignment(methods[ORDINARY], "p4", "p3")
    serialized = patch_assignment(methods[SERIALIZED], "p1", None)
    serialized = patch_assignment(serialized, "p5", None)
    patched = text.replace(methods[ORDINARY], ordinary, 1).replace(methods[SERIALIZED], serialized, 1)
    changed = [name for name, body in method_parts(patched).items() if methods.get(name) != body]
    if set(changed) != {ORDINARY, SERIALIZED}:
        raise RuntimeError(f"Unexpected changed methods: {changed}")
    if patched.count("invoke-static") - text.count("invoke-static") != 3:
        raise RuntimeError("Expected exactly three constructor filter calls")
    if "move-result-object p4\n\n    iput-object p4" not in ordinary:
        raise RuntimeError("Ordinary constructor iterator would retain original list")
    generated = patched.encode("utf-8")
    helper = helper_text(blocked).encode("utf-8")
    if current not in (original, generated):
        raise RuntimeError("AdBean changed since backup; will not overwrite unrelated work")
    if HELPER.exists() and HELPER.read_bytes() != helper:
        raise RuntimeError("Existing helper has unexpected content")
    BACKUP.parent.mkdir(parents=True, exist_ok=True)
    if not BACKUP.exists():
        BACKUP.write_bytes(original)
    TARGET.write_bytes(generated)
    HELPER.write_bytes(helper)
    report = {
        "target": str(TARGET), "helper": str(HELPER), "backup": str(BACKUP),
        "before_sha256": sha(original), "after_sha256": sha(generated),
        "helper_sha256": sha(helper), "changed_methods": changed,
        "added_methods": [HELPER_CLASS + "->filter(Ljava/lang/String;Ljava/util/List;)Ljava/util/List;"],
        "blocked_slot_count": len(blocked), "blocked_slots": blocked,
        "preserved_defined_constants": {name: value for name, value in constants.items() if value not in blocked},
        "unknown_or_empty_slot": "preserve original list",
        "blocked_slot_result": "new mutable java.util.ArrayList",
        "reward_behavior": "unchanged; no fabricated reward or success callbacks",
        "checks": ["exact source anchors", "only two existing methods changed", "three calls before images assignments", "ordinary iterator uses filtered p4", "known business slot exclusions"],
    }
    (ROOT / "config-method-changes.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": "patched", "blocked_slots": len(blocked), "changed_methods": len(changed), "new_methods": 1, "after_sha256": sha(generated)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
