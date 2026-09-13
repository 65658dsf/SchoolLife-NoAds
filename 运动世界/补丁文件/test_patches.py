"""Independent static bytecode checks for the five real no-ad patches.

Decodes opcodes and simulates typed registers/calls. This is deliberately NOT an
Android verifier or a device test. No APK or recovered source DEX is overwritten.
Run this file from any directory; results go to .analysis/noads/tests.json.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import struct
import sys
import zipfile
import zlib

sys.dont_write_bytecode = True
from inplace_dex import DexError, DexImage, PatchSession, code_units
from patch_ads import patch

ROOT = Path(__file__).resolve().parent.parent
AD = "Lcom/zjwh/android_wh_physicalfitness/advertise/"
SLOT = AD + "AdManager;->isSlotAllowed(Ljava/util/List;II)Z"
CAN_LOAD = AD + "AdManager;->checkCanLoad(" + AD + "AdPosition;Z)Z"
REWARD = AD + "guandian/GuandianAdProvider;->loadVideoAd(Landroid/app/Activity;Ljava/lang/String;Lkotlin/jvm/functions/Function2;)V"
BANNERS = {
    "Lcom/zjwh/android_wh_physicalfitness/mvi/home/fragment/" + page +
    "$setAdBanner$1$openAdDeferred$1;->invokeSuspend(Ljava/lang/Object;)Ljava/lang/Object;"
    for page in ("HomeFragment", "SportFragment")
}
EXPECTED_TARGETS = {SLOT, CAN_LOAD, REWARD} | BANNERS
BOX = "Lkotlin/coroutines/jvm/internal/OooO00o;->OooO0Oo(I)Ljava/lang/Integer;"
INTEGER_CTOR = "Ljava/lang/Integer;-><init>(I)V"
CALLBACK = "Lkotlin/jvm/functions/Function2;->invoke(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;"
LOADING = AD + "guandian/GuandianAdProvider;->easyRewardVideoAdLoading:Z"
BOOLEAN_FALSE = "Ljava/lang/Boolean;->FALSE:Ljava/lang/Boolean;"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def prototype(descriptor: str) -> tuple[list[str], str]:
    """Parse a descriptor independently of the patch generator."""
    require(descriptor.startswith("("), "invalid method descriptor")
    params = []
    cursor = 1
    while descriptor[cursor] != ")":
        start = cursor
        while descriptor[cursor] == "[":
            cursor += 1
        if descriptor[cursor] == "L":
            cursor = descriptor.index(";", cursor) + 1
        else:
            require(descriptor[cursor] in "ZBCSIJFD", "invalid primitive descriptor")
            cursor += 1
        params.append(descriptor[start:cursor])
    return params, descriptor[cursor + 1:]


@dataclass
class Value:
    category: str
    descriptor: str
    value: object
    initialized: bool = True

    def summary(self) -> dict:
        return {"category": self.category, "descriptor": self.descriptor,
                "value": self.value, "initialized": self.initialized}


def integer(value: int) -> Value:
    return Value("integer", "I", value)


def ref(descriptor: str, value: object = None, initialized: bool = True) -> Value:
    return Value("reference", descriptor, value, initialized)


def compatible(value: Value, descriptor: str, *, constructor: bool = False) -> bool:
    if descriptor in ("Z", "B", "C", "S", "I"):
        return value.category == "integer" and (descriptor != "Z" or value.value in (0, 1))
    if descriptor.startswith(("L", "[")):
        return (value.category == "reference" and (value.initialized or constructor)
                and (descriptor == "Ljava/lang/Object;" or value.descriptor == descriptor))
    return False


class Simulator:
    """Straight-line subset needed by the real patches and actual boxing helper.

    Instructions are decoded from final DEX bytes. Method/field indices resolve
    through actual DEX tables. Unknown opcodes/callees and uninitialized registers
    fail closed. Callback code itself is modelled as returning Kotlin Unit.
    """

    def __init__(self, helper_dex: DexImage):
        self.helper_dex = helper_dex
        self.events: list[dict] = []
        self.references: list[dict] = []
        self.fields = {LOADING: integer(1), BOOLEAN_FALSE: ref("Ljava/lang/Boolean;", False)}
        self.callback_objects: list[Value] = []
        self.last_registers: list[Value | None] = []
        self.initial_parameters: list[Value] = []
        self.trace: list[dict] = []

    def resolve_field(self, dex: DexImage, index: int) -> tuple[str, str]:
        count, off = dex.tables["fields"]
        require(0 <= index < count, "field index out of range")
        owner, typ, name = struct.unpack_from("<HHI", dex.data, off + index * 8)
        require(owner < len(dex.types) and typ < len(dex.types) and name < len(dex.strings),
                "invalid field_id")
        descriptor = dex.types[typ]
        signature = dex.types[owner] + "->" + dex.strings[name] + ":" + descriptor
        self.references.append({"kind": "field", "dex": dex.name, "index": index, "signature": signature})
        return signature, descriptor

    def execute(self, dex: DexImage, signature: str, arguments: list[Value] | None = None,
                depth: int = 0) -> tuple[Value | None, dict]:
        require(depth <= 1, "unexpected recursion")
        method = dex.method(signature)
        item = dex.code_item(method)
        params, result_type = prototype(method.descriptor)
        parameter_types = params if method.access_flags & 8 else [method.declaring_class] + params
        words = sum(2 if p in ("J", "D") else 1 for p in parameter_types)
        require(words == item.ins_size, "signature words and code_item.ins_size disagree")
        require(not any(p in ("J", "D") for p in parameter_types), "wide parameter outside this simulation")
        if arguments is None:
            arguments = [integer(123) if p in "BCSI" else integer(1) if p == "Z" else
                         ref(p, f"parameter_p{i}") for i, p in enumerate(parameter_types)]
        require(len(arguments) == len(parameter_types), "argument count mismatch")
        for arg, typ in zip(arguments, parameter_types):
            require(compatible(arg, typ), "parameter type mismatch")
        regs: list[Value | None] = [None] * item.registers_size
        first_param = item.registers_size - item.ins_size
        regs[first_param:] = arguments
        if depth == 0:
            self.initial_parameters = arguments[:]
        units = struct.unpack("<" + "H" * item.insns_size, dex.data[item.insns_off:item.insns_end])
        pc = 0
        pending: tuple[Value, int] | None = None
        local_trace = []

        def read_reg(index: int, expected: str | None = None, constructor: bool = False) -> Value:
            require(0 <= index < len(regs), "register index out of range")
            value = regs[index]
            require(value is not None, f"read of uninitialized v{index}")
            if expected:
                require(compatible(value, expected, constructor=constructor),
                        f"v{index} type {value.descriptor} incompatible with {expected}")
            return value

        def write_reg(index: int, value: Value) -> None:
            require(0 <= index < len(regs), "destination register out of range")
            regs[index] = value

        while pc < len(units):
            start = pc
            word = units[pc]
            opcode = word & 0xff
            high = word >> 8
            if opcode != 0x0c:
                pending = None
            trace = {"pc": pc, "opcode": hex(opcode), "method": signature}
            if opcode == 0x12:  # const/4 vA, #+B
                target, literal = high & 15, high >> 4
                literal = literal - 16 if literal & 8 else literal
                write_reg(target, integer(literal))
                trace.update(instruction="const/4", destination=target, literal=literal)
                pc += 1
            elif opcode == 0x08:  # move-object/from16 vAA, vBBBB
                require(pc + 1 < len(units), "truncated move-object/from16")
                source = units[pc + 1]
                value = read_reg(source)
                require(value.category == "reference", "move-object source is not a reference")
                write_reg(high, value)
                trace.update(instruction="move-object/from16", destination=high, source=source,
                             source_origin=value.value)
                pc += 2
            elif opcode in (0x62, 0x6a):  # sget-object / sput-boolean
                require(pc + 1 < len(units), "truncated field instruction")
                field, typ = self.resolve_field(dex, units[pc + 1])
                if opcode == 0x62:
                    require(typ.startswith(("L", "[")), "sget-object field is not a reference")
                    require(field == BOOLEAN_FALSE, "unexpected object field read")
                    write_reg(high, self.fields[field])
                    trace.update(instruction="sget-object", destination=high, field=field)
                else:
                    require(typ == "Z" and field == LOADING, "unexpected boolean field write")
                    value = read_reg(high, "Z")
                    self.fields[field] = value
                    self.events.append({"kind": "field_write", "field": field, "value": bool(value.value)})
                    trace.update(instruction="sput-boolean", source=high, field=field)
                pc += 2
            elif opcode == 0x22:  # new-instance vAA, type@BBBB (boxing helper)
                require(pc + 1 < len(units), "truncated new-instance")
                typ_idx = units[pc + 1]
                require(typ_idx < len(dex.types), "new-instance type out of range")
                typ = dex.types[typ_idx]
                require(typ == "Ljava/lang/Integer;", "unexpected allocation")
                write_reg(high, ref(typ, initialized=False))
                self.references.append({"kind": "type", "dex": dex.name, "index": typ_idx, "signature": typ})
                trace.update(instruction="new-instance", destination=high, type=typ)
                pc += 2
            elif opcode in (0x70, 0x71, 0x72):  # invoke-{direct,static,interface}, format 35c
                require(pc + 2 < len(units), "truncated invoke")
                count, register_g = high >> 4, high & 15
                require(count <= 5 and count <= item.outs_size, "invoke exceeds outgoing capacity")
                packed = units[pc + 2]
                indices = [(packed >> shift) & 15 for shift in (0, 4, 8, 12)] + [register_g]
                indices = indices[:count]
                method_index = units[pc + 1]
                require(method_index < len(dex.methods), "invoke method index out of range")
                called = dex.methods[method_index]
                arg_types, ret_type = prototype(called.descriptor)
                if opcode != 0x71:
                    arg_types = [called.declaring_class] + arg_types
                require(len(arg_types) == count, "invoke register count and method signature disagree")
                values = [read_reg(index, typ, constructor=opcode == 0x70 and i == 0)
                          for i, (index, typ) in enumerate(zip(indices, arg_types))]
                self.references.append({"kind": "method", "dex": dex.name, "index": method_index,
                                        "signature": called.signature})
                trace.update(instruction={0x70: "invoke-direct", 0x71: "invoke-static", 0x72: "invoke-interface"}[opcode],
                             registers=indices, called=called.signature)
                pc += 3
                if called.signature == BOX:
                    require(opcode == 0x71 and count == 1, "wrong boxing dispatch")
                    value, nested = self.execute(self.helper_dex, BOX, values, depth + 1)
                    require(value is not None and compatible(value, ret_type), "boxing result type mismatch")
                    trace["helper_trace"] = nested["instructions"]
                    pending = value, pc
                elif called.signature == INTEGER_CTOR:
                    require(opcode == 0x70 and count == 2, "wrong constructor dispatch")
                    require(not values[0].initialized, "constructor receiver already initialized")
                    values[0].value = values[1].value
                    values[0].initialized = True
                elif called.signature == CALLBACK:
                    require(opcode == 0x72 and count == 3, "wrong callback dispatch")
                    require(values[1].descriptor == values[2].descriptor == "Ljava/lang/Boolean;",
                            "callback arguments are not boxed Boolean")
                    self.callback_objects.append(values[0])
                    self.events.append({"kind": "callback", "receiver": values[0].value,
                                        "arguments": [values[1].value, values[2].value],
                                        "loading_when_called": bool(self.fields[LOADING].value)})
                    pending = ref("Lkotlin/Unit;", "Unit"), pc
                else:
                    raise AssertionError("unexpected invoke target: " + called.signature)
            elif opcode == 0x0c:  # move-result-object vAA
                require(pending is not None and pending[1] == pc, "move-result-object is not immediately after invocation")
                require(pending[0].category == "reference", "move-result-object result is not a reference")
                write_reg(high, pending[0])
                pending = None
                trace.update(instruction="move-result-object", destination=high)
                pc += 1
            elif opcode in (0x0e, 0x0f, 0x11):
                if opcode == 0x0e:
                    require(high == 0 and result_type == "V", "invalid return-void")
                    result = None
                    trace.update(instruction="return-void")
                elif opcode == 0x0f:
                    require(result_type in ("Z", "B", "C", "S", "I"), "return primitive incompatible with signature")
                    result = read_reg(high, result_type)
                    trace.update(instruction="return", register=high)
                else:
                    require(result_type.startswith(("L", "[")), "return-object incompatible with signature")
                    result = read_reg(high, result_type)
                    trace.update(instruction="return-object", register=high)
                pc += 1
                require(all(unit == 0 for unit in units[pc:]), "non-NOP instruction remains after return")
                local_trace.append(trace)
                if depth == 0:
                    self.last_registers = regs
                    self.trace = local_trace
                return result, {"instructions": local_trace, "return_code_unit": start,
                                "nop_padding_code_units": len(units) - pc,
                                "parameter_start_register": first_param,
                                "result": None if result is None else result.summary()}
            else:
                raise AssertionError(f"unsupported/reachable opcode {opcode:#x} at code unit {pc}")
            require(pc > start, "decoder made no progress")
            local_trace.append(trace)
        raise AssertionError("method falls through its entire instruction array without returning")


def main() -> None:
    output = ROOT / ".analysis" / "noads"
    output.mkdir(parents=True, exist_ok=True)
    source = ROOT / "ydsj.apk"
    source_digest = digest(source.read_bytes())
    with zipfile.ZipFile(source) as archive:
        original = archive.read("classes.dex")
    recovered_dir = ROOT / ".analysis" / "static-recovered"
    ref_path = recovered_dir / "embedded-08.dex"
    helper_path = recovered_dir / "embedded-07.dex"
    recovered, helper_bytes = ref_path.read_bytes(), helper_path.read_bytes()
    final, manifest, readable = patch(original, recovered)
    original_dex = DexImage(recovered, "embedded-08-original")
    final_dex = DexImage(readable, "embedded-08-patched")
    helper_dex = DexImage(helper_bytes, "embedded-07-original")
    checks = []

    def passed(name: str, **details) -> None:
        checks.append({"test": name, "result": "PASS", **details})

    def rejected(name: str, operation, exception=Exception) -> None:
        try:
            operation()
        except exception as exc:
            passed(name, rejection=str(exc))
        else:
            raise AssertionError("expected rejection: " + name)

    # Independent signatures and source code_off values establish allowed ranges;
    # the generator manifest is checked against them, not trusted as the boundary.
    require(len(manifest["patches"]) == 5, "expected exactly five patch records")
    require({p["signature"] for p in manifest["patches"]} == EXPECTED_TARGETS, "wrong patch targets")
    base = 0x3bfb4dc
    allowed = [(8, 32)]
    for signature in sorted(EXPECTED_TARGETS):
        old_item = original_dex.code_item(signature)
        new_item = final_dex.code_item(signature)
        require(old_item == new_item, "code_item header/layout changed")
        require(original_dex.data[old_item.offset:old_item.insns_off] == readable[new_item.offset:new_item.insns_off],
                "raw code_item header bytes changed")
        require(not old_item.tries_size and not old_item.debug_info_off, "unexpected try/debug data")
        allowed.append((base + old_item.insns_off, base + old_item.insns_end))
    require(len(original) == len(final), "outer DEX length changed")
    cursor = 0
    for start, end in sorted(allowed):
        require(cursor <= start < end <= len(original), "overlapping/out-of-bounds allowed ranges")
        require(original[cursor:start] == final[cursor:start], f"non-target bytes changed before {start:#x}")
        cursor = end
    require(original[cursor:] == final[cursor:], "non-target outer tail changed")
    passed("Only five original instruction arrays and outer bytes 8:32 changed; all code_item headers preserved",
           target_count=5, changed_bytes=sum(a != b for a, b in zip(original, final)),
           allowed_ranges=[{"start": hex(a), "end_exclusive": hex(b)} for a, b in sorted(allowed)])

    for name, data in (("outer_classes.dex", final), ("readable_embedded-08.dex", readable)):
        require(data[12:32] == hashlib.sha1(data[32:]).digest(), name + " SHA-1 invalid")
        require(struct.unpack_from("<I", data, 8)[0] == zlib.adler32(data[12:]) & 0xffffffff,
                name + " Adler-32 invalid")
        passed("DEX header checksums valid", artifact=name, sha256=digest(data))
    require(original_dex.methods == final_dex.methods and original_dex.declared_methods == final_dex.declared_methods,
            "method_ids, flags or class_data changed")
    require(original_dex.strings == final_dex.strings and original_dex.types == final_dex.types and
            original_dex.protos == final_dex.protos and original_dex.tables == final_dex.tables,
            "reference tables changed")
    passed("All method IDs, declarations, access flags, string/type/prototype indices and section offsets retained")

    semantic_results = []
    for signature in sorted(EXPECTED_TARGETS):
        vm = Simulator(helper_dex)
        result, execution = vm.execute(final_dex, signature)
        if signature in (SLOT, CAN_LOAD):
            require(result.category == "integer" and result.value == 0, "ad policy did not return false")
            require(not vm.events and not vm.references, "boolean method introduced side effects")
            # Exercise alternate valid incoming argument state to show the return
            # does not depend on the original nonzero values supplied above.
            zero_args = [integer(0) if v.category == "integer" else v for v in vm.initial_parameters]
            again, _ = Simulator(helper_dex).execute(final_dex, signature, zero_args)
            require(again.value == 0, "return depends on input values")
            behavior = "returns false for both sampled valid parameter states; instruction trace reads no parameters"
        elif signature in BANNERS:
            require(result.category == "reference" and result.descriptor == "Ljava/lang/Integer;" and result.value == 0,
                    "coroutine did not return boxed Integer(0)")
            require(not vm.events, "banner coroutine introduced side effects")
            require({r["signature"] for r in vm.references if r["kind"] == "method"} == {BOX, INTEGER_CTOR},
                    "unexpected boxing call graph")
            behavior = "returns initialized boxed Integer(0), verified through original embedded-07 helper constructor"
        else:
            require(result is None, "reward loader did not return void")
            require(len(vm.events) == 2 and vm.events[0] == {"kind": "field_write", "field": LOADING, "value": False},
                    "reward loading state was not cleared once before callback")
            calls = [event for event in vm.events if event["kind"] == "callback"]
            require(len(calls) == 1 and calls[0]["arguments"] == [False, False] and
                    calls[0]["loading_when_called"] is False, "reward callback tuple/count/order wrong")
            require(vm.fields[LOADING].value == 0, "reward loading flag remains set")
            require(len(vm.callback_objects) == 1 and vm.callback_objects[0] is vm.initial_parameters[3],
                    "called object is not the original p3 callback")
            moves = [row for row in vm.trace if row["instruction"] == "move-object/from16"]
            require(len(moves) == 1 and moves[0]["source"] == 26 and moves[0]["destination"] == 0,
                    "p3=v26 was not moved to v0 correctly")
            invokes = [row for row in vm.trace if row["instruction"] == "invoke-interface"]
            require(len(invokes) == 1 and invokes[0]["registers"] == [0, 1, 1], "35c callback registers decoded incorrectly")
            behavior = "clears loading and calls original p3 (v26) exactly once with (Boolean.FALSE, Boolean.FALSE)"
        semantic_results.append({"signature": signature, "behavior": behavior,
                                 "execution": execution, "events": vm.events,
                                 "resolved_references": vm.references})
        passed("Typed opcode execution and terminal NOP padding", signature=signature, behavior=behavior)

    # Mutation probes prove the semantic checker catches meaningful errors rather
    # than merely comparing output to the generator's hard-coded byte sequences.
    reward_item = final_dex.code_item(REWARD)
    bad_callback = bytearray(readable)
    move_trace = next(row for row in next(x for x in semantic_results if x["signature"] == REWARD)["execution"]["instructions"]
                      if row["instruction"] == "move-object/from16")
    struct.pack_into("<H", bad_callback, reward_item.insns_off + 2 * (move_trace["pc"] + 1), 25)
    rejected("Mutation: p2=v25 cannot masquerade as Function2 callback",
             lambda: Simulator(helper_dex).execute(DexImage(bytes(bad_callback), "mutated"), REWARD), AssertionError)
    bad_return = bytearray(readable)
    slot_item = final_dex.code_item(SLOT)
    bad_return[slot_item.insns_off + 2] = 0x11
    rejected("Mutation: return-object cannot implement a boolean return",
             lambda: Simulator(helper_dex).execute(DexImage(bytes(bad_return), "mutated"), SLOT), AssertionError)
    bad_padding = bytearray(readable)
    struct.pack_into("<H", bad_padding, slot_item.insns_end - 2, 0x000e)
    rejected("Mutation: non-NOP code after the terminal return is detected",
             lambda: Simulator(helper_dex).execute(DexImage(bytes(bad_padding), "mutated"), SLOT), AssertionError)

    rejected("Unsupported source classes.dex hash rejected", lambda: patch(b"unsupported", recovered), DexError)
    rejected("Unsupported recovered DEX hash rejected", lambda: patch(original, b"unsupported"), DexError)
    guards = PatchSession(original)
    guards.add_dex("embedded-08.dex", recovered, base)
    native = next(m for m in original_dex.declared_methods if m.native)
    rejected("Native declaration cannot be patched", lambda: guards.replace("embedded-08.dex", native.signature, code_units(0x000e)), DexError)
    guards.replace("embedded-08.dex", SLOT, code_units(0x0012, 0x000f))
    rejected("Overlapping repeat patch rejected", lambda: guards.replace("embedded-08.dex", SLOT, code_units(0x0012, 0x000f)), DexError)

    require(digest(source.read_bytes()) == source_digest, "source APK changed during tests")
    require(ref_path.read_bytes() == recovered and helper_path.read_bytes() == helper_bytes, "recovered source DEX changed")
    passed("Source APK and recovered verification inputs remain unchanged")
    report = {"schema": "noads-static-tests-v1", "status": "PASS", "passed_checks": len(checks),
              "android_runtime_verified": False,
              "limitations": ["No installation, ART verification or device UI/run-flow test performed.",
                              "The callback body is not executed; simulation models a normally returning callback.",
                              "Protected native code behavior and signature-dependent service compatibility remain untested."],
              "source_apk_sha256": source_digest, "final_classes_dex_sha256": digest(final),
              "checks": checks, "semantic_results": semantic_results}
    destination = output / "tests.json"
    destination.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
    print(json.dumps({"status": "PASS", "passed_checks": len(checks), "report": str(destination)}, ensure_ascii=True))


if __name__ == "__main__":
    main()
