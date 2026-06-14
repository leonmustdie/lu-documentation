#!/usr/bin/env python3
"""
lua_decompile.py — convert Naughty Bear compiled script chunks into
standard Lua 5.1 bytecode and (optionally) decompile them to source.

The game ships scripts as compiled Lua 5.1 with three deviations from
stock luac:
  1. a 0x84-byte engine wrapper before the \\x1bLua image (and a
     variable-length footer after it),
  2. a 13-byte header (the number-size byte appears twice),
  3. function blocks: the top-level proto omits the nups byte, and a
     custom constant type 0xFE holds a 64-bit interned string hash
     (CRC32 of the lowercase string, zero-extended).

This tool re-serializes each script as stock Lua 5.1 bytecode:
  * standard 12-byte header
  * nups=0 inserted at top level
  * 0xFE hash constants replaced with string constants — resolved to
    the original string through a CRC32 dictionary built from every
    ASCII string found in the extracted game files, or
    "__hash_0xXXXXXXXX" when unknown.

If java + unluac.jar are available it also decompiles to .lua source.

usage:
  python3 lua_decompile.py <extract_root>... -o out_dir [--jar unluac.jar]
"""
import argparse
import re
import struct
import subprocess
import sys
import zlib
from pathlib import Path

STRING_RE = re.compile(rb"[\x20-\x7e]{4,}")


def build_hash_dict(roots):
    """CRC32(lowercase) -> string, mined from every chunk + filenames."""
    d = {}
    for root in roots:
        for p in Path(root).rglob("*"):
            if p.is_dir():
                continue
            d[zlib.crc32(p.stem.split("_", 1)[-1].lower().encode())] = \
                p.stem.split("_", 1)[-1]
            try:
                data = p.read_bytes()
            except OSError:
                continue
            for m in STRING_RE.finditer(data):
                s = m.group()
                for piece in re.split(rb"[\\/]", s):
                    if 3 < len(piece) < 96:
                        d[zlib.crc32(piece.lower())] = piece.decode()
                        stem = piece.rsplit(b".", 1)[0]
                        if stem != piece:
                            d[zlib.crc32(stem.lower())] = stem.decode()
    return d


def transcode(raw, hashes):
    """custom chunk bytes -> standard Lua 5.1 bytecode, or raise."""
    i = raw.find(b"\x1bLua")
    if i < 0:
        raise ValueError("no Lua image")
    d = raw[i:i + 11] + raw[i + 12:]          # drop duplicated size byte
    pos = 12
    out = bytearray(b"\x1bLua\x51\x00\x01\x04\x04\x04\x08\x00")

    def u32():
        nonlocal pos
        v = struct.unpack_from("<I", d, pos)[0]
        pos += 4
        return v

    def u8():
        nonlocal pos
        v = d[pos]
        pos += 1
        return v

    def raw_bytes(n):
        nonlocal pos
        b = d[pos:pos + n]
        pos += n
        return b

    def lstr():
        n = u32()
        if n > len(d):
            raise ValueError("bad string length")
        return raw_bytes(n)

    def w32(v):
        out.extend(struct.pack("<I", v))

    def wstr(b):
        w32(len(b))
        out.extend(b)

    def proto(top):
        wstr(lstr())                          # source
        w32(u32())                            # linedefined
        w32(u32())                            # lastlinedefined
        if top:
            out.append(0)                     # nups (omitted in custom)
            out.extend(raw_bytes(3))          # nparams, vararg, maxstack
        else:
            out.extend(raw_bytes(4))
        nc = u32()
        if not (0 < nc < 300000):
            raise ValueError("ncode")
        w32(nc)
        out.extend(raw_bytes(4 * nc))
        nk = u32()
        if nk > 100000:
            raise ValueError("nconst")
        w32(nk)
        for _ in range(nk):
            t = u8()
            if t == 0:
                out.append(0)
            elif t == 1:
                out.append(1)
                out.append(u8())
            elif t == 3:
                out.append(3)
                out.extend(raw_bytes(8))
            elif t == 4:
                out.append(4)
                wstr(lstr())
            elif t == 0xFE:
                h = struct.unpack_from("<Q", d, pos)[0]
                pos_skip = raw_bytes(8)
                name = hashes.get(h & 0xFFFFFFFF, f"__hash_{h & 0xFFFFFFFF:#010x}")
                out.append(4)
                wstr(name.encode() + b"\x00")
            else:
                raise ValueError(f"const type {t}")
        np_ = u32()
        if np_ > 5000:
            raise ValueError("nproto")
        w32(np_)
        for _ in range(np_):
            proto(False)
        nl = u32()
        w32(nl)
        out.extend(raw_bytes(4 * nl))         # lineinfo
        nloc = u32()
        w32(nloc)
        for _ in range(nloc):
            wstr(lstr())
            w32(u32())
            w32(u32())
        nup = u32()
        w32(nup)
        for _ in range(nup):
            wstr(lstr())

    proto(True)
    return bytes(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("roots", nargs="+",
                    help="extraction roots (units with animation/ dirs); "
                         "also mined for the hash dictionary")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--jar", default=str(Path(__file__).parent / "unluac.jar"))
    args = ap.parse_args()
    outroot = Path(args.out)
    outroot.mkdir(parents=True, exist_ok=True)

    print("building hash dictionary...")
    hashes = build_hash_dict(args.roots)
    print(f"  {len(hashes)} known strings")

    jar = Path(args.jar)
    have_java = jar.exists() and subprocess.run(
        ["java", "-version"], capture_output=True).returncode == 0

    n_ok = n_src = n_fail = n_notlua = 0
    seen = set()
    for root in args.roots:
        for p in sorted(Path(root).glob("*/animation/*.bin")):
            raw = p.read_bytes()
            # SAFETY: only chunks that verifiably contain a Lua 5.1
            # image are treated as scripts. PiP (and possibly other)
            # chunks under animation/ are reference stubs, not Lua —
            # touching them as bytecode would produce garbage.
            sig = raw.find(b"\x1bLua")
            if sig < 0 or sig + 5 >= len(raw) or raw[sig + 4] != 0x51:
                n_notlua += 1
                continue
            m = re.search(rb"z:\\[\x20-\x7e]+?\.lua", raw)
            name = m.group().decode().split("\\")[-1][:-4] if m \
                else p.stem.split("_", 1)[-1]
            unit = p.parent.parent.name
            if (unit, name) in seen:
                continue
            seen.add((unit, name))
            dest = outroot / unit
            dest.mkdir(exist_ok=True)
            try:
                std = transcode(raw, hashes)
            except ValueError:
                n_fail += 1
                continue
            (dest / f"{name}.luac").write_bytes(std)
            n_ok += 1
            if have_java:
                r = subprocess.run(["java", "-jar", str(jar),
                                    str(dest / f"{name}.luac")],
                                   capture_output=True, text=True)
                if r.returncode == 0 and r.stdout.strip():
                    (dest / f"{name}.lua").write_text(r.stdout)
                    n_src += 1
    print(f"transcoded {n_ok} scripts ({n_fail} failed, {n_notlua} "
          f"non-Lua chunks skipped); decompiled to source: {n_src}")


if __name__ == "__main__":
    main()
