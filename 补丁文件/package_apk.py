"""Replace only the patched DEX entries, retaining all original Android resources.

Uncompressed native libraries are aligned to 16 KiB for extractNativeLibs=false.
This script does not sign or install the APK.
"""
from pathlib import Path
import zipfile, copy, struct, hashlib, json

ROOT=Path(__file__).resolve().parent.parent
WORK=ROOT/'.analysis/noads'
OUTPUT=ROOT/'output'
OUTPUT.mkdir(exist_ok=True)
replacements={name:WORK/'decoded-v2/build/apk'/name for name in ['classes4.dex','classes5.dex']}
assert all(p.exists() and p.stat().st_size>1000000 for p in replacements.values())
input_apk=ROOT/'base.apk'
unsigned=WORK/'base-noads-unsigned.apk'
ledger=[]
def digest(b):return hashlib.sha256(b).hexdigest()
def signature_entry(name):
    parts=name.upper().split('/')
    return len(parts)==2 and parts[0]=='META-INF' and (parts[1]=='MANIFEST.MF' or parts[1].endswith(('.SF','.RSA','.DSA','.EC')))
with zipfile.ZipFile(input_apk) as source,zipfile.ZipFile(unsigned,'w',allowZip64=True) as target:
    for info in source.infolist():
        if signature_entry(info.filename):continue
        old=source.read(info.filename)
        data=replacements[info.filename].read_bytes() if info.filename in replacements else old
        new=copy.copy(info)
        new.extra=b''
        if new.compress_type==zipfile.ZIP_STORED:
            align=16384 if new.filename.startswith('lib/') and new.filename.endswith('.so') else 4
            offset=target.fp.tell()+30+len(new.filename.encode('utf-8'))
            pad=(-offset)%align
            if 0<pad<4:pad+=align
            if pad:new.extra=struct.pack('<HH',0xCAFE,pad-4)+bytes(pad-4)
        target.writestr(new,data)
        ledger.append({'name':info.filename,'sha256':digest(data),'modified':data!=old})
with zipfile.ZipFile(unsigned) as z,unsigned.open('rb') as f:
    assert z.testzip() is None
    for i in z.infolist():
        if i.compress_type!=zipfile.ZIP_STORED:continue
        f.seek(i.header_offset)
        header=f.read(30)
        nl,el=struct.unpack_from('<HH',header,26)
        data_offset=i.header_offset+30+nl+el
        align=16384 if i.filename.startswith('lib/') and i.filename.endswith('.so') else 4
        assert data_offset%align==0,(i.filename,data_offset,align)
changed=[r['name'] for r in ledger if r['modified']]
assert sorted(changed)==sorted(replacements),changed
report={'source_sha256':digest(input_apk.read_bytes()),'unsigned_sha256':digest(unsigned.read_bytes()),
 'modified_entries':changed,'entry_count':len(ledger),'native_library_alignment':16384,'entries':ledger}
(WORK/'package-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='entries'},ensure_ascii=False))
print(str(unsigned))
