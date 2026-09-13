"""Update only this APK's local expected signing-certificate MD5.

Preserves the signature check, risk handler, termination policy, native library,
and every other protected configuration field. The new MD5 must be derived from
the same DER certificate used to sign the output APK.
"""
from __future__ import annotations
import hashlib
import struct
from patch_ads import repair_dex_header

SOURCE_AD_PATCHED_SHA256 = "a49e20a7b6d4f6d1fa3788081a8cb62625957fa9fd5b1df31a1fa8ddbfc1cf50"
CONFIG_OFFSET = 0x103B8
CONFIG_MAGIC = 0x1A28293D
CONFIG_SIZE = 254
CONFIG_KEY = b"93dahdkha123asdh"
ORIGINAL_CERTIFICATE_MD5 = "C62C38BE109D2A72528C307BDFC21E34"


def rc4(data: bytes, key: bytes = CONFIG_KEY) -> bytes:
    state = list(range(256))
    j = 0
    for i in range(256):
        j = (j + state[i] + key[i % len(key)]) & 255
        state[i], state[j] = state[j], state[i]
    result = bytearray()
    i = j = 0
    for value in data:
        i = (i + 1) & 255
        j = (j + state[i]) & 255
        state[i], state[j] = state[j], state[i]
        result.append(value ^ state[(state[i] + state[j]) & 255])
    return bytes(result)


def patch_expected_certificate(ad_patched_dex: bytes, signing_certificate_der: bytes) -> tuple[bytes, dict]:
    if hashlib.sha256(ad_patched_dex).hexdigest() != SOURCE_AD_PATCHED_SHA256:
        raise ValueError("Startup fix requires the exact reviewed five-method advertising patch")
    if len(signing_certificate_der) < 128 or signing_certificate_der[0] != 0x30:
        raise ValueError("Expected the DER certificate exported from the actual signing keystore")
    magic, length = struct.unpack_from("<II", ad_patched_dex, CONFIG_OFFSET)
    if (magic, length) != (CONFIG_MAGIC, CONFIG_SIZE):
        raise ValueError("Unsupported protection configuration header")
    start = CONFIG_OFFSET + 8
    original_ciphertext = ad_patched_dex[start:start + length]
    plaintext = rc4(original_ciphertext)
    lines = plaintext.splitlines(keepends=True)
    expected_line = b"c:" + ORIGINAL_CERTIFICATE_MD5.encode("ascii") + b"\n"
    if [line for line in lines if line.startswith(b"c:")] != [expected_line]:
        raise ValueError("Original expected certificate does not match the supported official APK")
    if b"p:com.zjwh.android_wh_physicalfitness\n" not in lines:
        raise ValueError("Unexpected package name in protection configuration")
    md5 = hashlib.md5(signing_certificate_der).hexdigest().upper()
    if md5 == ORIGINAL_CERTIFICATE_MD5:
        raise ValueError("This fix is intended for a distinct local test certificate")
    new_plaintext = plaintext.replace(expected_line, b"c:" + md5.encode("ascii") + b"\n", 1)
    new_ciphertext = rc4(new_plaintext)
    cert_start = plaintext.index(expected_line) + 2
    cert_end = cert_start + 32
    if (new_plaintext[:cert_start] != plaintext[:cert_start]
            or new_plaintext[cert_end:] != plaintext[cert_end:]
            or len(new_ciphertext) != CONFIG_SIZE):
        raise ValueError("Unexpected change outside the expected certificate value")
    final = bytearray(ad_patched_dex)
    final[start:start + length] = new_ciphertext
    repair_dex_header(final)
    ranges = [(8, 32), (start + cert_start, start + cert_end)]
    cursor = 0
    for lo, hi in ranges:
        if final[cursor:lo] != ad_patched_dex[cursor:lo]:
            raise ValueError("Unexpected startup-fix modification")
        cursor = hi
    if final[cursor:] != ad_patched_dex[cursor:]:
        raise ValueError("Unexpected modification after configuration")
    manifest = {
        "schema": "local-expected-signing-certificate-v1",
        "source_sha256": SOURCE_AD_PATCHED_SHA256,
        "final_sha256": hashlib.sha256(final).hexdigest(),
        "configuration_offset": CONFIG_OFFSET, "configuration_length": length,
        "certificate_ciphertext_offset": start + cert_start,
        "certificate_value_length": 32,
        "old_expected_md5": ORIGINAL_CERTIFICATE_MD5, "new_expected_md5": md5,
        "signing_certificate_sha256": hashlib.sha256(signing_certificate_der).hexdigest(),
        "ciphertext_before_hex": original_ciphertext[cert_start:cert_end].hex(),
        "ciphertext_after_hex": new_ciphertext[cert_start:cert_end].hex(),
        "outer_checksum_before_hex": ad_patched_dex[8:32].hex(),
        "outer_checksum_after_hex": final[8:32].hex(),
        "changed_bytes": sum(a != b for a, b in zip(ad_patched_dex, final)),
        "other_configuration_fields_unchanged": True,
        "native_code_unchanged": True, "signature_check_still_enabled": True,
        "runtime_verified": False,
    }
    return bytes(final), manifest
