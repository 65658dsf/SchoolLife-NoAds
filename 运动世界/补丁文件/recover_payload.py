"""Offline reconstruction of embedded DEX index metadata; source APK is read-only."""
import csv
import hashlib
import json
import re
import struct
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / '.analysis'
ROOT.mkdir(exist_ok=True)
OUT = ROOT / 'static-recovered'
OUT.mkdir(exist_ok=True)
apk = ROOT.parent / 'ydsj.apk'
with apk.open('rb') as stream:
    if hashlib.file_digest(stream, 'sha256').hexdigest() != '14e004e0001747ea49712a12d8657ad25eaf027de729b1e573d620a4bed345a1':
        raise ValueError('Unsupported source APK; no files were changed')
with zipfile.ZipFile(apk) as archive:
    b = archive.read('classes.dex')

def uleb(buf, pos):
    val = shift = 0
    for _ in range(5):
        c = buf[pos]; pos += 1
        val |= (c & 127) << shift
        if c < 128:
            return val, pos
        shift += 7
    raise ValueError('invalid ULEB128')

def classdata(pos):
    counts = []
    for _ in range(4):
        n, pos = uleb(b, pos); counts.append(n)
    all_methods = []
    for n in counts[:2]:
        idx = 0
        for _ in range(n):
            diff, pos = uleb(b, pos); idx += diff
            flags, pos = uleb(b, pos)
    for n in counts[2:]:
        idx = 0
        for _ in range(n):
            diff, pos = uleb(b, pos); idx += diff
            flags, pos = uleb(b, pos)
            code, pos = uleb(b, pos)
            all_methods.append((idx, flags, code))
    return pos, all_methods

pattern = bytes.fromhex('00000000010000000000000001000000')
reports = []
for m in re.finditer(re.escape(pattern), b):
    mp = m.start() - 4
    n = struct.unpack_from('<I', b, mp)[0]
    if n != 18:
        continue
    entries = [struct.unpack_from('<HHII', b, mp + 4 + i * 12) for i in range(n)]
    rows = {t: (s, o) for t, u, s, o in entries}
    if len(rows) != n or 0x1000 not in rows or rows[0] != (1, 0):
        continue
    base = mp - rows[0x1000][1]
    if base <= 0:
        continue
    p = base + rows[0x2002][1]
    strings, offsets = [], []
    for i in range(rows[1][0]):
        offsets.append(p - base)
        length, p = uleb(b, p)
        end = b.index(0, p)
        strings.append(b[p:end].replace(b'\xc0\x80', b'\x00').decode('utf-8', errors='surrogatepass'))
        p = end + 1
    following = min(o for t, (s, o) in rows.items() if o > rows[0x2002][1])
    assert p == base + following, ('string block boundary mismatch', hex(base))
    string_id_mismatches = sum(struct.unpack_from('<I', b, base + rows[1][1] + i * 4)[0] != o for i, o in enumerate(offsets))
    types = [strings[struct.unpack_from('<I', b, base + rows[2][1] + i * 4)[0]] for i in range(rows[2][0])]
    methods = []
    for i in range(rows[5][0]):
        cls, proto, name = struct.unpack_from('<HHI', b, base + rows[5][1] + i * 8)
        assert cls < len(types) and proto < rows[3][0] and name < len(strings)
        methods.append((types[cls], strings[name], proto))
    p = base + rows[0x2000][1]
    parsed_class_data = {}
    code_offsets = set()
    for _ in range(rows[0x2000][0]):
        start = p
        p, members = classdata(p)
        parsed_class_data[start - base] = members
        for idx, flags, code in members:
            assert idx < len(methods)
            if code:
                assert code % 4 == 0
                code_offsets.add(code)
    end = max(p, mp + 4 + n * 12)
    end = (end + 3) & ~3
    class_names = []
    declared_methods = []
    for i in range(rows[6][0]):
        cls, access, sup, interfaces, source, annotations, data, statics = struct.unpack_from('<8I', b, base + rows[6][1] + i * 32)
        assert cls < len(types)
        class_names.append(types[cls])
        if data:
            assert data in parsed_class_data
            for idx, flags, code in parsed_class_data[data]:
                assert methods[idx][0] == types[cls], ('declaring class mismatch', i, idx)
                if code:
                    regs, ins, outs, tries, debug, count = struct.unpack_from('<4H2I', b, base + code)
                    assert base + code + 16 + count * 2 <= end
                declared_methods.append((idx, methods[idx][0], methods[idx][1], code, hex(base + code) if code else ''))
    recovered = bytearray(b[base:end])
    recovered[:112] = bytes(112)
    recovered[:8] = b'dex\n035\0'
    struct.pack_into('<9I', recovered, 32, len(recovered), 112, 0x12345678, 0, 0, rows[0x1000][1], rows[1][0], rows[1][1], rows[2][0])
    struct.pack_into('<I', recovered, 68, rows[2][1])
    for t, pos in [(3,72),(4,80),(5,88),(6,96)]:
        struct.pack_into('<II', recovered, pos, *rows[t])
    data_start = min(o for t,(s,o) in rows.items() if t >= 0x1000)
    struct.pack_into('<II', recovered, 104, len(recovered) - data_start, data_start)
    for i, o in enumerate(offsets):
        struct.pack_into('<I', recovered, rows[1][1] + i * 4, o)
    recovered[12:32] = hashlib.sha1(recovered[32:]).digest()
    struct.pack_into('<I', recovered, 8, zlib.adler32(recovered[12:]) & 0xffffffff)
    index = len(reports) + 1
    path = OUT / f'embedded-{index:02d}.dex'
    path.write_bytes(recovered)
    with (OUT / f'embedded-{index:02d}-methods.tsv').open('w', encoding='utf-8', errors='backslashreplace', newline='') as fp:
        w = csv.writer(fp, delimiter='\t'); w.writerow(['method_idx', 'declaring_class', 'method_name', 'code_relative_offset', 'code_original_offset']); w.writerows(declared_methods)
    (OUT / f'embedded-{index:02d}-classes.txt').write_text('\n'.join(class_names), encoding='utf-8', errors='backslashreplace')
    report = {'dex': path.name, 'original_base': hex(base), 'original_end_exclusive': hex(end), 'map_original_offset': hex(mp), 'recovered_size': len(recovered), 'classes':len(class_names), 'method_ids':len(methods), 'class_declared_methods':len(declared_methods), 'code_items_from_classes': len(code_offsets), 'map_code_item_count':rows[0x2001][0], 'strings':len(strings), 'string_id_mismatches_replaced':string_id_mismatches, 'has_app_admanager': 'Lcom/zjwh/android_wh_physicalfitness/advertise/AdManager;' in class_names, 'sha256':hashlib.sha256(recovered).hexdigest(), 'validated':['string_data sequential count ends exactly at next mapped section','all method IDs resolve class/proto/name in bounds','all class-data offsets resolve to parsed class-data items','every declared method ID agrees with enclosing class_def','all nonzero code offsets aligned and instruction arrays within reconstructed file']}
    reports.append(report)
    print(json.dumps(report, ensure_ascii=True))
(OUT / 'recovery-index.json').write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding='utf-8')
