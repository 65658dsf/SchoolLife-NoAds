"""Bounded code_item replacement for a recovered DEX embedded in a container.

The recovered DEX is an INDEX ONLY. Its rebuilt header/string IDs must never be
copied over a protected payload. PatchSession changes only explicitly selected
code_item ranges in the original container; it does not repair any checksum.

No DEX instruction assembler or verifier is included. The caller is responsible
for the replacement's opcode, register, control-flow and return-type validity.
Replacement instructions must terminate on every reachable path before padding.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import struct
from typing import Iterable


class DexError(ValueError):
    """Invalid input or an operation outside the permitted mutation scope."""


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(data).hexdigest()


def _bounds(data: bytes | bytearray, offset: int, size: int) -> None:
    if offset < 0 or size < 0 or offset + size > len(data):
        raise DexError(f"out-of-bounds range: {offset:#x}+{size:#x}")


def uleb128(data: bytes, offset: int) -> tuple[int, int]:
    result = 0
    for i in range(5):
        _bounds(data, offset, 1)
        value = data[offset]
        offset += 1
        result |= (value & 0x7f) << (7 * i)
        if value < 0x80:
            if result > 0xffffffff:
                raise DexError("ULEB128 exceeds 32 bits")
            return result, offset
    raise DexError("unterminated ULEB128")


def sleb128(data: bytes, offset: int) -> tuple[int, int]:
    result = shift = 0
    for _ in range(5):
        _bounds(data, offset, 1)
        value = data[offset]
        offset += 1
        result |= (value & 0x7f) << shift
        shift += 7
        if value < 0x80:
            if value & 0x40:
                result -= 1 << shift
            if not -(1 << 31) <= result < (1 << 31):
                raise DexError("SLEB128 exceeds 32 bits")
            return result, offset
    raise DexError("unterminated SLEB128")


@dataclass(frozen=True)
class Method:
    method_idx: int
    declaring_class: str
    name: str
    descriptor: str
    access_flags: int | None = None
    code_off: int | None = None
    class_data_off: int | None = None

    @property
    def signature(self) -> str:
        return f"{self.declaring_class}->{self.name}{self.descriptor}"

    @property
    def native(self) -> bool:
        return bool((self.access_flags or 0) & 0x100)

    @property
    def abstract(self) -> bool:
        return bool((self.access_flags or 0) & 0x400)


@dataclass(frozen=True)
class CodeItem:
    offset: int
    registers_size: int
    ins_size: int
    outs_size: int
    tries_size: int
    debug_info_off: int
    insns_size: int
    insns_off: int
    insns_end: int
    end: int


class DexImage:
    """Read a standard, already recovered DEX without changing its bytes."""

    def __init__(self, data: bytes, name: str = "dex"):
        self.data = bytes(data)
        self.name = name
        _bounds(self.data, 0, 112)
        if self.data[:4] != b"dex\n" or self.data[7] != 0:
            raise DexError("a standard recovered DEX is required")
        file_size, header_size, endian = struct.unpack_from("<III", self.data, 32)
        if file_size != len(self.data) or header_size != 112 or endian != 0x12345678:
            raise DexError("unsupported/inconsistent DEX header")
        self.tables = {
            name: struct.unpack_from("<II", self.data, offset)
            for name, offset in (("strings", 56), ("types", 64), ("protos", 72),
                                 ("fields", 80), ("methods", 88), ("classes", 96))
        }
        for name, width in (("strings", 4), ("types", 4), ("protos", 12),
                            ("fields", 8), ("methods", 8), ("classes", 32)):
            count, off = self.tables[name]
            _bounds(self.data, off, count * width)
        self.strings = self._read_strings()
        self.types = self._read_types()
        self.protos = self._read_protos()
        self.methods = self._read_methods()
        self.declared_methods = self._read_class_data()
        self._by_signature = {m.signature: m for m in self.declared_methods}
        if len(self._by_signature) != len(self.declared_methods):
            raise DexError("duplicate declared method signature")
        self._code_users: dict[int, list[Method]] = {}
        for method in self.declared_methods:
            if method.code_off:
                self._code_users.setdefault(method.code_off, []).append(method)

    def _index(self, seq: list, index: int, label: str):
        if not 0 <= index < len(seq):
            raise DexError(f"invalid {label} index: {index}")
        return seq[index]

    def _read_strings(self) -> list[str]:
        count, off = self.tables["strings"]
        strings = []
        for i in range(count):
            string_off = struct.unpack_from("<I", self.data, off + i * 4)[0]
            expected_units, start = uleb128(self.data, string_off)
            end = self.data.find(b"\x00", start)
            if end < 0:
                raise DexError("unterminated string_data")
            try:
                value = self.data[start:end].replace(b"\xc0\x80", b"\0").decode(
                    "utf-8", errors="surrogatepass")
            except UnicodeDecodeError as exc:
                raise DexError(f"invalid MUTF-8 at {start:#x}") from exc
            units = len(value.encode("utf-16-le", errors="surrogatepass")) // 2
            if units != expected_units:
                raise DexError("string_data UTF-16 length mismatch")
            strings.append(value)
        return strings

    def _read_types(self) -> list[str]:
        count, off = self.tables["types"]
        return [self._index(self.strings, struct.unpack_from("<I", self.data, off + i * 4)[0],
                            "string") for i in range(count)]

    def _read_protos(self) -> list[str]:
        count, off = self.tables["protos"]
        protos = []
        for i in range(count):
            shorty, result, params = struct.unpack_from("<III", self.data, off + i * 12)
            self._index(self.strings, shorty, "shorty")
            arguments = []
            if params:
                _bounds(self.data, params, 4)
                n = struct.unpack_from("<I", self.data, params)[0]
                _bounds(self.data, params + 4, n * 2)
                arguments = [self._index(self.types, struct.unpack_from("<H", self.data,
                              params + 4 + j * 2)[0], "type") for j in range(n)]
            protos.append("(" + "".join(arguments) + ")" + self._index(self.types, result, "type"))
        return protos

    def _read_methods(self) -> list[Method]:
        count, off = self.tables["methods"]
        methods = []
        for i in range(count):
            cls, proto, name = struct.unpack_from("<HHI", self.data, off + i * 8)
            methods.append(Method(i, self._index(self.types, cls, "type"),
                                  self._index(self.strings, name, "string"),
                                  self._index(self.protos, proto, "proto")))
        return methods

    def _read_class_data(self) -> list[Method]:
        count, off = self.tables["classes"]
        declared = []
        for i in range(count):
            row = struct.unpack_from("<8I", self.data, off + 32 * i)
            owner = self._index(self.types, row[0], "class type")
            data_off = row[6]
            if not data_off:
                continue
            cursor = data_off
            counts = []
            for _ in range(4):
                n, cursor = uleb128(self.data, cursor)
                counts.append(n)
            for field_count in counts[:2]:
                index = 0
                for _ in range(field_count):
                    diff, cursor = uleb128(self.data, cursor)
                    _, cursor = uleb128(self.data, cursor)
                    index += diff
                    if index >= self.tables["fields"][0]:
                        raise DexError("class_data field index outside field_ids")
            for method_count in counts[2:]:
                index = 0
                for _ in range(method_count):
                    diff, cursor = uleb128(self.data, cursor)
                    flags, cursor = uleb128(self.data, cursor)
                    code, cursor = uleb128(self.data, cursor)
                    index += diff
                    original = self._index(self.methods, index, "method")
                    if original.declaring_class != owner:
                        raise DexError("class_data method owner mismatch")
                    if code and code % 4:
                        raise DexError("unaligned code_item")
                    declared.append(Method(index, owner, original.name, original.descriptor,
                                           flags, code, data_off))
        return declared

    def method(self, signature: str) -> Method:
        try:
            return self._by_signature[signature]
        except KeyError as exc:
            raise DexError(f"declared method not found: {signature}") from exc

    def find(self, declaring_class: str, name: str, descriptor: str) -> Method:
        return self.method(f"{declaring_class}->{name}{descriptor}")

    def code_item(self, method: Method | str) -> CodeItem:
        if isinstance(method, str):
            method = self.method(method)
        if method.native or method.abstract or not method.code_off:
            raise DexError(f"no editable code (native/abstract/absent): {method.signature}")
        off = method.code_off
        _bounds(self.data, off, 16)
        regs, ins, outs, tries, debug, count = struct.unpack_from("<4H2I", self.data, off)
        if regs < ins:
            raise DexError("registers_size is less than ins_size")
        start = off + 16
        end = start + count * 2
        _bounds(self.data, start, count * 2)
        insns_end = end
        if tries:
            end += 2 if count & 1 else 0
            _bounds(self.data, end, 8 * tries)
            try_offsets = []
            for i in range(tries):
                try_start, try_count, handler = struct.unpack_from("<IHH", self.data, end + i * 8)
                if try_start + try_count > count:
                    raise DexError("try range exceeds instruction capacity")
                try_offsets.append(handler)
            handler_base = end + 8 * tries
            n, cursor = uleb128(self.data, handler_base)
            handler_offsets = set()
            for _ in range(n):
                handler_offsets.add(cursor - handler_base)
                ntypes, cursor = sleb128(self.data, cursor)
                for _ in range(abs(ntypes)):
                    typ, cursor = uleb128(self.data, cursor)
                    addr, cursor = uleb128(self.data, cursor)
                    self._index(self.types, typ, "catch type")
                    if addr >= count:
                        raise DexError("catch target exceeds instruction capacity")
                if ntypes <= 0:
                    catch_all, cursor = uleb128(self.data, cursor)
                    if catch_all >= count:
                        raise DexError("catch-all target exceeds instruction capacity")
            if any(handler not in handler_offsets for handler in try_offsets):
                raise DexError("try_item does not reference a handler start")
            end = cursor
        return CodeItem(off, regs, ins, outs, tries, debug, count, start, insns_end, end)


class PatchSession:
    """Accumulate audited replacements; no filesystem or APK mutation occurs."""

    def __init__(self, original_container: bytes):
        self.original = bytes(original_container)
        self._patched = bytearray(original_container)
        self.images: dict[str, tuple[DexImage, int, int]] = {}
        self.patches: list[dict] = []

    def add_dex(self, name: str, recovered: bytes, original_base: int,
                protected_prefix: int = 4096) -> DexImage:
        if name in self.images or original_base < 0 or protected_prefix < 0:
            raise DexError("duplicate DEX name or invalid base/protected prefix")
        image = DexImage(recovered, name)
        _bounds(self.original, original_base, len(recovered))
        if protected_prefix > len(recovered):
            raise DexError("protected prefix exceeds DEX length")
        # Validates that the reference and the protected source really have the
        # same tail before trusting reference code offsets or instruction bytes.
        if self.original[original_base + protected_prefix:original_base + len(recovered)] != recovered[protected_prefix:]:
            raise DexError(f"recovered tail differs from original container: {name}")
        for previous, base, _ in self.images.values():
            if original_base < base + len(previous.data) and base < original_base + len(recovered):
                raise DexError("overlapping embedded DEX ranges")
        self.images[name] = image, original_base, protected_prefix
        return image

    def replace(self, dex_name: str, signature: str, instructions: bytes,
                *, reason: str = "", registers_size: int | None = None,
                outs_size: int | None = None, clear_debug_info: bool = True,
                allow_exception_tail_retirement: bool = False) -> dict:
        """Replace one entire body, preserving all offsets and insns_size.

        ins_size always stays unchanged: it is constrained by the signature.
        registers/outs are preserved unless explicitly overridden. A registers
        override requires clearing debug info, since locals then become stale.
        Caller must account for incoming arguments occupying the LAST ins_size
        registers, and for each invoke's argument words in outs_size.

        By default, a method with tries_size > 0 is REFUSED. If explicitly
        allowed, tries_size becomes zero and old padding/try/handlers are zeroed.
        It is now unreferenced slack; neighboring code_items never move. A strict
        sequential code_item-section scanner may reject such slack even though
        readers following class_data code_off can still address every method.
        """
        try:
            image, base, protected = self.images[dex_name]
        except KeyError as exc:
            raise DexError(f"unknown DEX: {dex_name}") from exc
        method = image.method(signature)
        item = image.code_item(method)
        if item.tries_size and not allow_exception_tail_retirement:
            raise DexError("target has exception tables; retirement requires explicit opt-in")
        if len(image._code_users[item.offset]) != 1:
            raise DexError("shared code_item requires explicit handling; refusing collateral changes")
        if item.offset < protected:
            raise DexError("target overlaps protected DEX prefix")
        replacement = bytes(instructions)
        if not replacement or len(replacement) % 2:
            raise DexError("replacement must contain a positive whole number of 16-bit code units")
        if len(replacement) > item.insns_size * 2:
            raise DexError("replacement exceeds original instruction capacity")
        regs = item.registers_size if registers_size is None else registers_size
        outs = item.outs_size if outs_size is None else outs_size
        if not isinstance(regs, int) or not item.ins_size <= regs <= 0xffff:
            raise DexError("registers_size must be uint16 and at least ins_size")
        if not isinstance(outs, int) or not 0 <= outs <= 0xffff:
            raise DexError("outs_size must be uint16")
        if regs != item.registers_size and not clear_debug_info:
            raise DexError("changing registers_size requires clearing stale debug_info_off")
        start, end = base + item.offset, base + item.end
        for existing in self.patches:
            if start < existing["original_end_exclusive"] and existing["original_offset"] < end:
                raise DexError("code_item overlaps an existing patch")
        original = self.original[start:end]
        if original != image.data[item.offset:item.end]:
            raise DexError("code_item bytes differ from recovered index")
        updated = bytearray(original)
        struct.pack_into("<4H2I", updated, 0, regs, item.ins_size, outs, 0,
                         0 if clear_debug_info else item.debug_info_off, item.insns_size)
        updated[16:16 + item.insns_size * 2] = replacement.ljust(item.insns_size * 2, b"\0")
        updated[item.insns_end - item.offset:] = b"\0" * (item.end - item.insns_end)
        record = {
            "dex": dex_name, "method_idx": method.method_idx, "signature": signature,
            "reason": reason, "access_flags": method.access_flags,
            "code_relative_offset": item.offset, "original_base": base,
            "original_offset": start, "original_end_exclusive": end,
            "code_header_before": asdict(item),
            "code_header_after": {"registers_size": regs, "ins_size": item.ins_size,
                                  "outs_size": outs, "tries_size": 0,
                                  "debug_info_off": 0 if clear_debug_info else item.debug_info_off,
                                  "insns_size": item.insns_size},
            "replacement_code_units": len(replacement) // 2,
            "nop_padding_code_units": item.insns_size - len(replacement) // 2,
            "retired_exception_tail_bytes": item.end - item.insns_end,
            "replacement_hex": replacement.hex(),
            "original_hex": original.hex(), "patched_hex": updated.hex(),
            "changed_bytes": sum(a != b for a, b in zip(original, updated)),
        }
        self._patched[start:end] = updated
        self.patches.append(record)
        return record

    def finish(self) -> tuple[bytes, dict]:
        """Return a fresh patched container and a reversible, validated manifest."""
        ranges = sorted(self.patches, key=lambda p: p["original_offset"])
        if len(self.original) != len(self._patched):
            raise DexError("container length changed")
        cursor = 0
        changed = 0
        for patch in ranges:
            start, end = patch["original_offset"], patch["original_end_exclusive"]
            if self.original[cursor:start] != self._patched[cursor:start]:
                raise DexError("non-target bytes changed")
            if self.original[start:end] != bytes.fromhex(patch["original_hex"]):
                raise DexError("manifest original bytes mismatch")
            if self._patched[start:end] != bytes.fromhex(patch["patched_hex"]):
                raise DexError("manifest patched bytes mismatch")
            changed += patch["changed_bytes"]
            cursor = end
        if self.original[cursor:] != self._patched[cursor:]:
            raise DexError("non-target tail changed")
        manifest = {
            "schema": "inplace-code-item-patches-v1", "container_size": len(self.original),
            "source_sha256": sha256(self.original), "patched_sha256": sha256(self._patched),
            "changed_bytes": changed, "patch_count": len(ranges),
            "verified": {"same_container_length": True, "all_non_target_bytes_identical": True,
                         "indices_and_class_data_unchanged": True,
                         "protected_prefixes_unchanged": True,
                         "native_and_abstract_methods_unchanged": True},
            "checksums": "Not changed by this engine; packaging owns any container checksum repair.",
            "dexes": [{"name": name, "original_base": base, "size": len(img.data),
                       "protected_prefix": protected, "recovered_sha256": sha256(img.data)}
                      for name, (img, base, protected) in self.images.items()],
            "patches": ranges,
        }
        return bytes(self._patched), manifest


def code_units(*units: int) -> bytes:
    """Encode already-assembled uint16 instructions as DEX little-endian bytes."""
    if any(not isinstance(value, int) or not 0 <= value <= 0xffff for value in units):
        raise DexError("code units must be uint16")
    return struct.pack("<" + "H" * len(units), *units)


def inspect_methods(path: Path, contains: str = "") -> Iterable[dict]:
    image = DexImage(path.read_bytes(), path.name)
    for method in image.declared_methods:
        if contains in method.signature:
            result = asdict(method)
            result["signature"] = method.signature
            if method.code_off and not method.native and not method.abstract:
                result["code_item"] = asdict(image.code_item(method))
            yield result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recovered_dex", type=Path)
    parser.add_argument("--contains", default="")
    args = parser.parse_args()
    print(json.dumps(list(inspect_methods(args.recovered_dex, args.contains)),
                     ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
