#!/usr/bin/env python3
"""Version-locked ARM64 hotseat Tuner experiment; writes a separate app only.

The original close predicate remains authoritative outside native hotseat.
No shared socket destructor, game mode getter, or x86_64 code is patched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile

STOCK_SHA256 = "17e6b8de23ccaf05d50d9071f3fa1c63392b51e1ec089469bee710127399c5d5"
UUID = "6490573d64cb38ed9f3c99f8e1273440"
BASE = 0x100000000
CALL = 0x1014A7A40
HELPER = BASE + 0x2000
ANY_MULTIPLAYER = 0x1012A8674
HOTSEAT = 0x1012A8914
CHILD = Path("Contents/MacOS/Civ6_Exe_Child")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def branch(source: int, target: int, *, link: bool = True) -> int:
    delta = target - source
    if delta % 4 or not -(1 << 27) <= delta < (1 << 27):
        raise ValueError("ARM64 branch is unaligned or out of range")
    return (0x94000000 if link else 0x14000000) | ((delta // 4) & 0x3FFFFFF)


def uleb(value: int) -> bytes:
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def read_uleb(data: bytes, pos: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        part = data[pos]
        pos += 1
        value |= (part & 127) << shift
        shift += 7
        if not part & 128:
            return value, pos


def layout(data: bytes) -> dict:
    if data[:4] != b"\xca\xfe\xba\xbe":
        raise ValueError("Expected the pinned Universal Mach-O")
    offset = None
    for index in range(struct.unpack_from(">I", data, 4)[0]):
        cpu, _, off, size, _ = struct.unpack_from(">5I", data, 8 + index * 20)
        if cpu == 0x100000C:
            offset = off
            break
    if offset is None:
        raise ValueError("No ARM64 slice")
    pos = offset + 32
    sections = {}
    found_uuid = None
    starts = None
    for _ in range(struct.unpack_from("<I", data, offset + 16)[0]):
        cmd, length = struct.unpack_from("<II", data, pos)
        if cmd == 0x19:
            segment = data[pos + 8:pos + 24].rstrip(b"\0").decode()
            for index in range(struct.unpack_from("<I", data, pos + 64)[0]):
                entry = pos + 72 + index * 80
                name = data[entry:entry + 16].rstrip(b"\0").decode()
                address, count, fileoff = struct.unpack_from("<QQI", data, entry + 32)
                sections[(segment, name)] = (address, count, offset + fileoff, entry)
        elif cmd == 0x1B:
            found_uuid = data[pos + 8:pos + 24].hex()
        elif cmd == 0x26:
            off, count = struct.unpack_from("<II", data, pos + 8)
            starts = (offset + off, count)
        pos += length
    if found_uuid != UUID or starts is None:
        raise ValueError("Unsupported ARM64 UUID or missing function starts")
    return {"offset": offset, "size": size, "sections": sections, "starts": starts}


def patch_bytes(original: bytes) -> tuple[bytes, dict]:
    if sha(original) != STOCK_SHA256:
        raise ValueError("Unknown/already patched build: no bytes written")
    info = layout(original)
    offset = info["offset"]
    text, text_size, text_file, text_header = info["sections"][("__TEXT", "__text")]
    location = lambda address: offset + address - BASE
    if original[location(CALL):location(CALL) + 4] != bytes.fromhex("0d03f897"):
        raise ValueError("Unexpected original call")
    if any(original[location(HELPER):location(HELPER) + 48]):
        raise ValueError("Helper padding is occupied")
    if struct.unpack_from("<I", original, text_header + 60)[0]:
        raise ValueError("Cannot extend a relocated text section")
    # Same prologue/epilogue as the original callback: preserve x19/x20/FP/LR.
    helper = bytearray(original[location(CALL - 20):location(CALL - 20) + 16])
    helper += struct.pack("<I", branch(HELPER + 16, ANY_MULTIPLAYER))
    helper += struct.pack("<I", 0x34000000 | (4 << 5))  # cbz w0, helper+36
    helper += bytes.fromhex("e00313aa")  # mov x0, x19: original config pointer
    helper += struct.pack("<I", branch(HELPER + 28, HOTSEAT))
    helper += struct.pack("<I", 0x52000000)  # eor w0, w0, #1
    helper += original[location(CALL + 24):location(CALL + 36)]
    if len(helper) != 48:
        raise ValueError("Unexpected helper length")
    result = bytearray(original)
    result[location(HELPER):location(HELPER) + 48] = helper
    struct.pack_into("<I", result, location(CALL), branch(CALL, HELPER))
    # Give the helper a genuine text section and function-start entry without
    # shifting any existing instruction, section end, or link-edit allocation.
    shift = text - HELPER
    struct.pack_into("<QQI", result, text_header + 32,
                     HELPER, text_size + shift, HELPER - BASE)
    fs, allocated = info["starts"]
    first, rest = read_uleb(original, fs)
    if first != text - BASE:
        raise ValueError("Unexpected first function")
    end = rest
    while True:
        value, end = read_uleb(original, end)
        if not value:
            break
    replacement = uleb(HELPER - BASE) + uleb(text - HELPER) + original[rest:end]
    if len(replacement) > allocated or any(original[end:fs + allocated]):
        raise ValueError("No function-start padding")
    result[fs:fs + allocated] = replacement.ljust(allocated, b"\0")
    # First compact-unwind range has the same frame encoding (0x04000001).
    # Extend that range back to the helper; keep every later absolute boundary.
    _, _, unwind, _ = info["sections"][("__TEXT", "__unwind_info")]
    version, common_off, common_n, _, _, index_off, index_n = struct.unpack_from("<7I", original, unwind)
    if version != 1 or index_n != 50:
        raise ValueError("Unexpected unwind index")
    first_offset, page, _ = struct.unpack_from("<III", original, unwind + index_off)
    kind, entries_off, entries_n, _, _ = struct.unpack_from("<I4H", original, unwind + page)
    if first_offset != text - BASE or kind != 3 or entries_n != 1021:
        raise ValueError("Unexpected first unwind page")
    entries = struct.unpack_from("<" + "I" * entries_n, original, unwind + page + entries_off)
    common = struct.unpack_from("<" + "I" * common_n, original, unwind + common_off)
    if entries[0] & 0xFFFFFF or common[entries[0] >> 24] != 0x04000001:
        raise ValueError("Cannot reuse the original frame encoding")
    struct.pack_into("<I", result, unwind + index_off, HELPER - BASE)
    for index, entry in enumerate(entries):
        relative = 0 if index == 0 else (entry & 0xFFFFFF) + shift
        if relative >= 1 << 24:
            raise ValueError("Compact-unwind offset overflow")
        struct.pack_into("<I", result, unwind + page + entries_off + index * 4,
                         (entry & 0xFF000000) | relative)
    patched = bytes(result)
    return patched, {"original_sha256": STOCK_SHA256, "unsigned_sha256": sha(patched),
                     "arm64_uuid": UUID, "call_address": hex(CALL),
                     "helper_address": hex(HELPER), "helper_bytes": helper.hex(),
                     "changed_bytes_before_signing": sum(a != b for a, b in zip(original, patched)),
                     "original_size": len(original), "patched_size": len(patched)}


def ensure_independent_files(source: Path, destination: Path) -> None:
    for relative in (CHILD, Path("Contents/MacOS/Civ6_Exe"), Path("Contents/_CodeSignature/CodeResources")):
        first, second = source / relative, destination / relative
        if first.exists() and second.exists() and os.path.samefile(first, second):
            raise ValueError(f"Destination shares a writable/signable inode with the original: {relative}")
    if (source / "Contents/Info.plist").read_bytes() != (destination / "Contents/Info.plist").read_bytes():
        raise ValueError("Destination bundle metadata differs from the original")


def apply_copy(source: Path, destination: Path, manifest: Path) -> dict:
    source = source.resolve()
    destination = destination.resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("The experiment must use an independent app directory")
    original = (source / CHILD).read_bytes()
    patched, report = patch_bytes(original)
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["cp", "-cR", str(source), str(destination)], check=True)
    ensure_independent_files(source, destination)
    target = destination / CHILD
    if sha(target.read_bytes()) != STOCK_SHA256:
        raise ValueError("Destination is not an untouched clone; refusing overwrite")
    # Replace a new inode, never modify a potentially linked original in place.
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as output:
        temp = Path(output.name)
        output.write(patched)
    os.chmod(temp, target.stat().st_mode)
    os.replace(temp, target)
    subprocess.run(["codesign", "--force", "--sign", "-", str(target)], check=True)
    subprocess.run(["codesign", "--force", "--sign", "-", str(destination)], check=True)
    subprocess.run(["codesign", "--verify", "--strict", str(target)], check=True)
    subprocess.run(["codesign", "--verify", "--strict", str(destination)], check=True)
    if sha((source / CHILD).read_bytes()) != STOCK_SHA256:
        raise RuntimeError("Original executable changed unexpectedly")
    report.update(source_app=str(source), experiment_app=str(destination),
                  signed_sha256=sha(target.read_bytes()), rollback="Exit experiment; launch the untouched Steam app")
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(apply_copy(args.source, args.destination, args.manifest), indent=2))


if __name__ == "__main__":
    main()
