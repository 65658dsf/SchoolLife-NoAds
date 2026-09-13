"""Five guarded, length-preserving advertising patches for the supported APK.

No new methods, fields, classes, strings, native declarations or resources.
The encrypted DEX prefixes are NOT replaced with the recovered DEX headers.
"""
from __future__ import annotations

import hashlib
import struct
import zlib
from inplace_dex import DexError, DexImage, PatchSession, code_units
from ad_entry_specs import ENTRY_PATCH_SPECS

APK_SHA256 = "14e004e0001747ea49712a12d8657ad25eaf027de729b1e573d620a4bed345a1"
DEX_SHA256 = "7fef0c676c81467c170650383ab0403386dab3e907ffcbd19ab8592fa45379bd"
RECOVERED_SHA256 = "eef2374d728f696088f35b1e44e380c99a38679b57c8b0cdbf568c5e199b6617"
DEX_NAME = "embedded-08.dex"
DEX_BASE = 0x3BFB4DC
PACKAGE = "Lcom/zjwh/android_wh_physicalfitness/"

BANNER_SPECS = [
    {
        "class": PACKAGE + "mvi/home/fragment/" + page + "$setAdBanner$1$openAdDeferred$1;",
        "method": "invokeSuspend",
        "descriptor": "(Ljava/lang/Object;)Ljava/lang/Object;",
        "replacement_kind": "cmcc_disabled_integer_zero",
        "observed": {
            "method_idx": index, "code_off": offset, "registers_size": 7,
            "ins_size": 2, "outs_size": 3, "tries_size": 0, "insns_size": 61,
        },
        "reason": "Resolve only the CMCC advertisement enable query to Integer(0); the original coroutine clears cachedCmccBanners, keeps cachedOpsBanners, and completes the banner update.",
    }
    for page, index, offset in (("HomeFragment", 18412, 0x2877C4), ("SportFragment", 18870, 0x28EAEC))
]


def method_id(dex: DexImage, cls: str, name: str, descriptor: str) -> int:
    matches = [m.method_idx for m in dex.methods if (m.declaring_class, m.name, m.descriptor) == (cls, name, descriptor)]
    if len(matches) != 1:
        raise DexError(f"Expected one referenced method: {cls}->{name}{descriptor}")
    return matches[0]


def field_id(dex: DexImage, cls: str, name: str, descriptor: str) -> int:
    count, off = dex.tables["fields"]
    matches = []
    for index in range(count):
        owner, kind, field_name = struct.unpack_from("<HHI", dex.data, off + index * 8)
        if (dex.types[owner], dex.strings[field_name], dex.types[kind]) == (cls, name, descriptor):
            matches.append(index)
    if len(matches) != 1:
        raise DexError(f"Expected one referenced field: {cls}->{name}:{descriptor}")
    return matches[0]


def signature(spec: dict) -> str:
    return spec["class"] + "->" + spec["method"] + spec["descriptor"]


def replacement(dex: DexImage, spec: dict) -> bytes:
    method = dex.method(signature(spec))
    item = dex.code_item(method)
    expected = spec["observed"]
    for name in ("registers_size", "ins_size", "outs_size", "tries_size", "insns_size"):
        if getattr(item, name) != expected[name]:
            raise DexError(f"Unexpected {name} for {method.signature}")
    if (method.method_idx, method.code_off) != (expected["method_idx"], expected["code_off"]):
        raise DexError("Unexpected method identity/offset")
    if item.tries_size or item.debug_info_off:
        raise DexError("This patch set requires no exception or debug tables")
    kind = spec["replacement_kind"]
    if kind == "return_false":
        return code_units(0x0012, 0x000F)  # const/4 v0,0; return v0
    if kind == "cmcc_disabled_integer_zero":
        # Use the same boxing helper already referenced by this coroutine.
        box = method_id(dex, "Lkotlin/coroutines/jvm/internal/OooO00o;", "OooO0Oo", "(I)Ljava/lang/Integer;")
        return code_units(0x0012, 0x1071, box, 0x0000, 0x000C, 0x0011)
    if kind == "reward_unavailable_callback":
        refs = spec["references"]
        loading = field_id(dex, *refs["loading_field"])
        false = field_id(dex, *refs["false_field"])
        callback = method_id(dex, *refs["callback_method"])
        p3 = item.registers_size - item.ins_size + 3
        if p3 != expected["callback_register"] or item.outs_size < 3:
            raise DexError("Unexpected callback register or outgoing capacity")
        return code_units(
            0x0012,              # const/4 v0,0
            0x006A, loading,     # sput-boolean v0, easyRewardVideoAdLoading
            0x0008, p3,          # move-object/from16 v0,p3
            0x0162, false,       # sget-object v1, Boolean.FALSE
            0x3072, callback, 0x0110,  # invoke-interface {v0,v1,v1}, Function2.invoke
            0x000E,              # return-void
        )
    raise DexError(f"Unknown replacement kind: {kind}")


def repair_dex_header(data: bytearray) -> None:
    data[12:32] = hashlib.sha1(data[32:]).digest()
    struct.pack_into("<I", data, 8, zlib.adler32(data[12:]) & 0xFFFFFFFF)


def patch(original: bytes, recovered: bytes) -> tuple[bytes, dict, bytes]:
    if hashlib.sha256(original).hexdigest() != DEX_SHA256:
        raise DexError("Unsupported original classes.dex")
    if hashlib.sha256(recovered).hexdigest() != RECOVERED_SHA256:
        raise DexError("Unexpected recovered index; regenerate from the supported APK")
    session = PatchSession(original)
    dex = session.add_dex(DEX_NAME, recovered, DEX_BASE)
    for spec in ENTRY_PATCH_SPECS + BANNER_SPECS:
        session.replace(DEX_NAME, signature(spec), replacement(dex, spec),
                        reason=spec["reason"], clear_debug_info=False)
    raw, manifest = session.finish()
    final = bytearray(raw)
    repair_dex_header(final)  # The OUTER, ordinary DEX header only.
    if len(final) != len(original) or final[:8] != original[:8] or final[32:DEX_BASE] != original[32:DEX_BASE]:
        raise DexError("Unexpected outer-shell mutation")
    if final[DEX_BASE:DEX_BASE + 4096] != original[DEX_BASE:DEX_BASE + 4096]:
        raise DexError("Protected embedded prefix changed")
    # Fresh readable copy for offline verification only, never packaged.
    readable = bytearray(recovered)
    readable[4096:] = final[DEX_BASE + 4096:DEX_BASE + len(recovered)]
    repair_dex_header(readable)
    manifest["outer_header_checksum_repair"] = {
        "offset": 8, "size": 24,
        "before_hex": original[8:32].hex(), "after_hex": final[8:32].hex(),
        "purpose": "Standard outer DEX SHA-1 and Adler-32; embedded encrypted headers remain byte-identical.",
    }
    manifest["final_sha256"] = hashlib.sha256(final).hexdigest()
    manifest["readable_patched_dex_sha256"] = hashlib.sha256(readable).hexdigest()
    manifest["changed_bytes_including_outer_checksums"] = sum(a != b for a, b in zip(original, final))
    manifest["scope"] = {
        "changed_methods": 5, "unchanged_native_declarations": True,
        "unchanged_indices_offsets_sizes": True, "unchanged_resources_and_libraries": True,
        "reward_result": [False, False], "cmcc_ad_switch": 0,
        "shared_resource_api_and_operational_banners": "retained",
        "runtime_verified": False,
    }
    return bytes(final), manifest, bytes(readable)
