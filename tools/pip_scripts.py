#!/usr/bin/env python3
"""
pip_scripts.py — extract Panic in Paradise Lua scripts.

Unlike the original Naughty Bear (which ships *compiled* Lua bytecode in
its animation/ chunks — see lua_decompile.py), PiP ships **plaintext Lua
source** in its animation chunks (type 04b00000), comments and all.
No decompilation needed; this tool slices the source straight out and
rebuilds the original on-disk script tree from the embedded paths.

Chunk layout (big-endian):
  0x00  u32 name hash
  0x04  u32 type = 0x04b00000
  0x10  u32 path_offset
  0x14  u32 path_length      (the z:\\nb2_data\\...\\X.lua source path)
  0x18  u32 source_offset
  0x1c  u32 source_length    (the Lua source text)
  0x20  u32 source_end
  0x24  u32 trailer_length

usage:
  python3 pip_scripts.py <extract_root>... -o scripts_out/
  # mirrors the original tree, e.g.
  #   scripts_out/nb2_data/assets/scripts/global/factionutil.lua
"""
import argparse
import struct
import sys
from pathlib import Path


def extract_lua(d):
    """(relative_path, source_text) or None."""
    if len(d) < 0x28 or struct.unpack_from(">I", d, 4)[0] != 0x04b00000:
        return None
    po, pl, so, sl = struct.unpack_from(">4I", d, 0x10)
    if not (0 < so < len(d) and 0 < sl <= len(d) - so):
        return None
    if not (0 < po < len(d) and 0 < pl <= len(d) - po):
        return None
    path = d[po:po + pl].split(b"\x00")[0].decode("ascii", "replace")
    src = d[so:so + sl].split(b"\x00")[0].decode("latin-1")
    # normalise the embedded path: drop drive, unify separators
    rel = path.replace("\\", "/").lstrip("/")
    if ":" in rel:
        rel = rel.split(":", 1)[1].lstrip("/")
    return rel, src


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("roots", nargs="+")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--flat", action="store_true",
                    help="write <unit>/<name>.lua instead of mirroring "
                         "the original source tree")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    n = empty = dupe = 0
    seen = {}
    for root in args.roots:
        for p in sorted(Path(root).glob("*/animation/*.bin")):
            d = p.read_bytes()
            if not d:
                empty += 1
                continue
            r = extract_lua(d)
            if r is None:
                continue
            rel, src = r
            if len(src.strip()) < 2:
                empty += 1
                continue
            if args.flat:
                dest = out / p.parent.parent.name / (Path(rel).name)
            else:
                dest = out / rel
            # de-dup identical sources shipped in multiple units
            if dest in seen:
                if seen[dest] == src:
                    dupe += 1
                    continue
                stem = dest.with_suffix("")
                dest = Path(f"{stem}__{p.parent.parent.name}.lua")
            seen[dest] = src
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(src, encoding="utf-8", errors="replace")
            n += 1
    print(f"extracted {n} Lua source files "
          f"({dupe} duplicate copies skipped, {empty} empty chunks)")


if __name__ == "__main__":
    main()
