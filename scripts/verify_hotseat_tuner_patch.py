#!/usr/bin/env python3
"""Offline verification: emulate real ARM64 predicates and the candidate shim.

Run with: uv run --with unicorn --with capstone python scripts/verify_hotseat_tuner_patch.py --binary PATH
No game process is opened and no executable file is modified.
"""
from __future__ import annotations

import argparse
import bisect
import json
from pathlib import Path
import struct

from hotseat_tuner_patch import (BASE, CALL, HELPER, ANY_MULTIPLAYER, HOTSEAT,
                                branch, layout, patch_bytes, read_uleb)


def unwind_entries(data: bytes) -> list[tuple[int, int]]:
    _, _, root, _ = layout(data)["sections"][("__TEXT", "__unwind_info")]
    _, common_off, common_n, _, _, index_off, index_n = struct.unpack_from("<7I", data, root)
    common = struct.unpack_from("<" + "I" * common_n, data, root + common_off)
    entries = []
    for i in range(index_n - 1):
        start, page, _ = struct.unpack_from("<III", data, root + index_off + i * 12)
        kind, off, count, localoff, localcount = struct.unpack_from("<I4H", data, root + page)
        assert kind == 3
        encodings = common + struct.unpack_from("<" + "I" * localcount, data, root + page + localoff)
        for entry in struct.unpack_from("<" + "I" * count, data, root + page + off):
            entries.append((BASE + start + (entry & 0xFFFFFF), encodings[entry >> 24]))
    return entries


def verify(original: bytes) -> dict:
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
    from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
    from unicorn.arm64_const import (UC_ARM64_REG_X0, UC_ARM64_REG_X19,
                                    UC_ARM64_REG_X20, UC_ARM64_REG_X29,
                                    UC_ARM64_REG_X30, UC_ARM64_REG_SP,
                                    UC_ARM64_REG_PC, UC_ARM64_REG_W0)
    patched, report = patch_bytes(original)
    info = layout(patched)
    off = info["offset"]
    code = patched[off:off + 0x1AAC000]
    disassembler = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    shim = list(disassembler.disasm(code[HELPER - BASE:HELPER - BASE + 48], HELPER))
    assert len(shim) == 12 and shim[-1].mnemonic == "ret"
    assert struct.unpack_from("<I", code, CALL - BASE)[0] == branch(CALL, HELPER)

    old = unwind_entries(original)
    new = unwind_entries(patched)
    assert old[1:] == new[1:] and old[0][1] == new[0][1] == 0x04000001
    starts, size = info["starts"]
    pos = starts
    address = BASE
    functions = []
    while pos < starts + size:
        delta, pos = read_uleb(patched, pos)
        if not delta:
            break
        address += delta
        functions.append(address)
    assert functions[0] == HELPER and len(functions) == 74151
    old_addrs = [a for a, _ in old]
    new_addrs = [a for a, _ in new]
    for address in functions[1:]:
        assert old[bisect.bisect_right(old_addrs, address) - 1][1] == new[bisect.bisect_right(new_addrs, address) - 1][1]

    mem = 0x200000000
    config, obj, vtable, variant, getter, stop = (mem + n * 0x1000 for n in range(1, 7))
    sp = mem + 0xF000
    marker = {UC_ARM64_REG_X19: 0x123419, UC_ARM64_REG_X20: 0x123420,
              UC_ARM64_REG_X29: 0x123429, UC_ARM64_REG_X30: stop, UC_ARM64_REG_SP: sp}
    global_page = (0x1012A8680 & ~4095) + (53630 << 12)

    def execute(entry: int, mode: int, flags: int = 0, value_type: int = 2) -> int:
        uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        uc.mem_map(BASE, len(code))
        uc.mem_write(BASE, code)
        uc.mem_map(mem, 0x10000)
        uc.mem_map(global_page, 0x1000)
        uc.mem_write(config + 0x88, struct.pack("<Q", obj))
        uc.mem_write(obj, struct.pack("<Q", vtable))
        uc.mem_write(vtable + 0x70, struct.pack("<Q", getter))
        uc.mem_write(getter, bytes.fromhex("c0035fd6"))
        uc.mem_write(stop, bytes.fromhex("c0035fd6"))
        uc.mem_write(variant, struct.pack("<H", value_type))
        uc.mem_write(variant + 8, struct.pack("<I", mode))
        uc.mem_write(variant + 0x17, bytes([flags]))
        for reg, value in marker.items():
            uc.reg_write(reg, value)
        uc.reg_write(UC_ARM64_REG_X0, config)

        def hook(engine, address, _size, _data):
            if address == getter:
                engine.reg_write(UC_ARM64_REG_X0, variant)
                engine.reg_write(UC_ARM64_REG_PC, engine.reg_read(UC_ARM64_REG_X30))
            elif address == stop:
                engine.emu_stop()

        uc.hook_add(UC_HOOK_CODE, hook)
        uc.emu_start(entry, stop + 4, count=1000)
        assert uc.reg_read(UC_ARM64_REG_PC) == stop
        for reg, value in marker.items():
            assert uc.reg_read(reg) == value, (entry, reg)
        return uc.reg_read(UC_ARM64_REG_W0)

    cases = {"single": 0xE9685EE7, "hotseat": 0x0C67CEE3,
             "LAN": 0x0138409A, "cloud": 0x10FF95A6,
             "internet": 0xE75806E8, "crossplay": 0x453F23E2,
             "unknown": 0x12345678}
    outcomes = {}
    for name, value in cases.items():
        before = execute(ANY_MULTIPLAYER, value)
        is_hotseat = execute(HOTSEAT, value)
        after = execute(HELPER, value)
        assert after == (before and not is_hotseat)
        assert (before != after) == (name == "hotseat")
        outcomes[name] = {"before_close": before, "after_close": after}
    for flags, kind in [(0x20, 2), (0, 0)]:
        assert execute(HELPER, 0x0C67CEE3, flags, kind) == execute(ANY_MULTIPLAYER, 0x0C67CEE3, flags, kind)
    for invalid in (b"", patched):
        try:
            patch_bytes(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError("Unknown/already-patched input was accepted")
    report.update(native_instruction_emulation=outcomes, invalid_variants_preserved=True,
                  original_function_unwind_encodings_checked=74150,
                  register_and_stack_restoration=True,
                  helper_disassembly=[f"{i.address:#x}: {i.mnemonic} {i.op_str}" for i in shim])
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = verify(args.binary.read_bytes())
    text = json.dumps(result, indent=2) + "\n"
    if args.report:
        args.report.write_text(text)
    print(text)
