"""Independent read-only checks for the certificate-configuration startup fix.

Consumes the build_startupfix.py outputs; writes only the verification JSON.
Does not import startup_fix.py or execute Android/JNI/server calls.
"""
from __future__ import annotations
from pathlib import Path
import argparse
import hashlib
import json
import re
import struct
import zipfile
import zlib

ROOT=Path(__file__).resolve().parent.parent
WORK=ROOT/'.analysis'/'startup-fix'
ORIGINAL_CERT_MD5='C62C38BE109D2A72528C307BDFC21E34'
TEST_CERT_MD5='6693563C906323213F14A44F14264E62'
CONFIG_OFFSET=0x103B8
CONFIG_LENGTH=254
KEY=b'93dahdkha123asdh'

def decrypt(data: bytes) -> bytes:
    # Independently reconstructed from ARM64 KSA 0x115d28 and PRGA 0x115d9c.
    state=list(range(256))
    j=0
    for i in range(256):
        j=(j+state[i]+KEY[i%len(KEY)])%256
        state[i],state[j]=state[j],state[i]
    a=b=0
    output=bytearray()
    for value in data:
        a=(a+1)%256
        b=(b+state[a])%256
        state[a],state[b]=state[b],state[a]
        output.append(value^state[(state[a]+state[b])%256])
    return bytes(output)

def configuration(data: bytes) -> tuple[bytes,dict[str,str]]:
    size,offset=struct.unpack_from('<II',data,0x68)
    if size+offset!=CONFIG_OFFSET:
        raise ValueError('unexpected standard DEX data end')
    magic,length=struct.unpack_from('<II',data,CONFIG_OFFSET)
    if magic!=0x1A28293D or length!=CONFIG_LENGTH:
        raise ValueError('unexpected configuration magic/size')
    plain=decrypt(data[CONFIG_OFFSET+8:CONFIG_OFFSET+8+length])
    if not plain.endswith(b'\n'):
        raise ValueError('configuration does not end on a line boundary')
    fields={}
    for line in plain.decode('ascii').splitlines():
        key,separator,value=line.partition(':')
        if not separator or not key or key in fields:
            raise ValueError('invalid/duplicate configuration field')
        fields[key]=value
    if len(fields)!=17:
        raise ValueError('unexpected field count')
    return plain,fields

def certificate_guard_rejects(expected: str, actual: str) -> bool:
    # Exact local comparison branch at ARM64 0xf1488..0xf14e0.
    # JNI availability / retrieval and other independent guards are out of scope.
    return bool(actual) and expected!=actual and expected!='NONE'

def run(work: Path=WORK) -> dict:
    before=(work/'classes-before-startupfix.dex').read_bytes()
    after=(work/'classes-patched.dex').read_bytes()
    cert=(work/'test-certificate.der').read_bytes()
    checks=[]
    def check(name,condition):
        if not condition:
            raise AssertionError(name)
        checks.append({'name':name,'passed':True})
    with zipfile.ZipFile(ROOT/'ydsj.apk') as apk:
        original=apk.read('classes.dex')
    before_plain,before_fields=configuration(before)
    after_plain,after_fields=configuration(after)
    original_plain,original_fields=configuration(original)
    check('first_build_preserved_original_configuration',before_plain==original_plain)
    check('original_certificate_matches_pinned_original',original_fields['c']==ORIGINAL_CERT_MD5)
    actual_md5=hashlib.md5(cert).hexdigest().upper()
    check('local_signer_differs_from_original',actual_md5!=ORIGINAL_CERT_MD5)
    check('replacement_matches_actual_exported_certificate',after_fields['c']==actual_md5)
    check('replacement_is_uppercase_md5_not_NONE',bool(re.fullmatch('[0-9A-F]{32}',after_fields['c'])))
    check('all_other_configuration_values_preserved',{k:v for k,v in before_fields.items() if k!='c'}=={k:v for k,v in after_fields.items() if k!='c'})
    check('configuration_order_whitespace_and_length_preserved',after_plain==before_plain.replace(('c:'+ORIGINAL_CERT_MD5+'\n').encode(),('c:'+actual_md5+'\n').encode(),1))
    check('config_cipher_roundtrip',decrypt(after_plain)==after[CONFIG_OFFSET+8:CONFIG_OFFSET+8+CONFIG_LENGTH])
    check('container_length_preserved',len(after)==len(before))
    cert_start=CONFIG_OFFSET+8+before_plain.index(('c:'+ORIGINAL_CERT_MD5).encode())+2
    cert_end=cert_start+32
    check('header_and_ciphertext_outside_certificate_unchanged',after[32:cert_start]==before[32:cert_start] and after[cert_end:]==before[cert_end:])
    check('dex_magic_preserved',after[:8]==before[:8])
    check('all_existing_ad_patch_bytes_preserved',after[CONFIG_OFFSET+8+CONFIG_LENGTH:]==before[CONFIG_OFFSET+8+CONFIG_LENGTH:])
    check('outer_sha1_valid',after[12:32]==hashlib.sha1(after[32:]).digest())
    check('outer_adler32_valid',struct.unpack_from('<I',after,8)[0]==zlib.adler32(after[12:])&0xffffffff)
    cases=[]
    unknown='0123456789ABCDEF0123456789ABCDEF'
    for expected in [ORIGINAL_CERT_MD5,actual_md5]:
        for incoming in [ORIGINAL_CERT_MD5,actual_md5,unknown]:
            rejected=certificate_guard_rejects(expected,incoming)
            check('comparison_'+str(len(cases)+1),rejected==(expected!=incoming))
            cases.append({'expected':expected,'actual':incoming,'reject':rejected})
    for expected,incoming in [('NONE',x) for x in [ORIGINAL_CERT_MD5,actual_md5,unknown,'']]+[(ORIGINAL_CERT_MD5,''),(actual_md5,'')]:
        rejected=certificate_guard_rejects(expected,incoming)
        check('existing_NONE_or_empty_result_semantics_'+str(len(cases)+1),not rejected)
        cases.append({'expected':expected,'actual':incoming,'reject':rejected})
    return {'scope':'Offline config integrity and reconstructed local certificate guard; no Android runtime verification',
            'passed':len(checks),'checks':checks,'certificate_md5':actual_md5,
            'config_offset':hex(CONFIG_OFFSET),'config_length':CONFIG_LENGTH,
            'certificate_cipher_range':[hex(cert_start),hex(cert_end)],
            'cipher_changed_bytes':sum(a!=b for a,b in zip(before[cert_start:cert_end],after[cert_start:cert_end])),
            'native_comparison_evidence':str(ROOT/'.analysis'/'startup-review'/'strings64'/'certificate-compare-tests.json'),
            'cases':cases,'runtime_verified':False}

if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--work-dir',type=Path,default=WORK)
    ap.add_argument('--output',type=Path)
    args=ap.parse_args()
    result=run(args.work_dir)
    output=args.output or args.work_dir/'tests.json'
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,indent=2,ensure_ascii=False),'utf-8')
    print(json.dumps({'passed':result['passed'],'cipher_changed_bytes':result['cipher_changed_bytes'],'runtime_verified':False,'output':str(output)},ensure_ascii=False))
