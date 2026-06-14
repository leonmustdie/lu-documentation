#!/usr/bin/env python3
"""
pip_dump.py — dump everything currently decodable from Naughty Bear:
Panic in Paradise .lu/.cu files (LUH containers).

PiP is a separate game from the original Naughty Bear; use lu_dump.py
for NB1 files. This tool only processes LUH-format files and never
runs NB1-only decoders on PiP data. x36-format files found in the
input are listed in the report and skipped (point lu_dump.py at them).

what it produces today:
  dump/extracted/<unit>/...    raw chunks by type (manifest.tsv each)
  dump/textures/<unit>/*.png   all textures (format identical to NB1)
  dump/skeletons/<unit>.txt    per-skeleton bone reports
  dump/audio/*.txt             .cu sound manifests
  dump/report.txt              everything decoded / skipped, with reasons

not yet decoded for PiP (skipped safely, listed in the report):
  meshes (34000007 sub-chunk containers), animation clips (reworked
  curve encoding), scripts (no Lua bytecode ships in the PiP files
  examined so far — only path references).

usage:
  python3 pip_dump.py <game_folder_or_files...> -o pipdump/
"""
import argparse
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import naughty_lu                                       # noqa: E402
import lu_convert                                       # noqa: E402
from lu_rig import parse_skeleton                       # noqa: E402
import pip_scripts                                       # noqa: E402


def main():
    ap = argparse.ArgumentParser(
        description="dump Panic in Paradise .lu/.cu files")
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("-o", "--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    (out / "extracted").mkdir(parents=True, exist_ok=True)

    luh, x36, cus = [], [], []
    seen = set()
    for inp in args.inputs:
        p = Path(inp)
        cands = (sorted(p.rglob("*")) if p.is_dir() else [p])
        for f in cands:
            if not f.is_file():
                continue
            key = (f.name, f.stat().st_size)
            if key in seen:
                continue
            seen.add(key)
            if f.suffix.lower() == ".cu":
                cus.append(f)
            elif f.suffix.lower() == ".lu":
                magic = f.read_bytes()[:4]
                (luh if magic == b"\x05LUH" else x36).append(f)
    report = [f"inputs: {len(luh)} LUH (PiP), {len(x36)} x36 (NB1 — "
              f"skipped, use lu_dump.py), {len(cus)} .cu"]
    if x36:
        report.append("x36 files skipped: " + ", ".join(f.name for f in x36))

    # 1. extract (LUH only) --------------------------------------------
    failed = []
    import argparse as _ap
    for f in luh:
        try:
            naughty_lu.cmd_extract(_ap.Namespace(
                files=[str(f)], out=str(out / "extracted"), names=None))
        except SystemExit:
            pass
        except Exception as e:
            failed.append(f"{f.name}: {e}")
    if failed:
        report.append("EXTRACTION FAILURES (likely corrupt dumps):")
        report += [f"  {x}" for x in failed]
    units = [u for u in sorted((out / "extracted").iterdir()) if u.is_dir()]
    report.append(f"units extracted: {len(units)}")

    # 2. textures (chunk format identical to NB1) ----------------------
    n_tex = n_badtex = 0
    for u in units:
        for tdir in ("type_34200007", "texture"):
            src = u / tdir
            if not src.exists():
                continue
            dest = out / "textures" / u.name
            for c in sorted(src.glob("*.bin")):
                d = c.read_bytes()
                if not lu_convert.is_texture_chunk(d):
                    n_badtex += 1
                    continue
                dest.mkdir(parents=True, exist_ok=True)
                try:
                    w = lu_convert.convert_texture(d, dest / c.stem)
                    n_tex += sum(1 for x in w if str(x).endswith(".png"))
                except Exception:
                    n_badtex += 1
    report.append(f"textures: {n_tex} png ({n_badtex} skipped)")

    # 3. skeleton reports ----------------------------------------------
    n_sk = 0
    for u in units:
        lines = []
        for c in sorted(u.glob("unk_04000001/*.bin")):
            try:
                sk = parse_skeleton(c.read_bytes())
            except Exception:
                sk = None
            if sk:
                lines.append(f"{c.name}: {len(sk)} bones")
                n_sk += 1
        if lines:
            d = out / "skeletons"
            d.mkdir(exist_ok=True)
            (d / f"{u.name}.txt").write_text("\n".join(lines) + "\n")
    report.append(f"skeletons parsed: {n_sk}")

    # 4. rigged characters + rigid props (34000007 mesh = NB1 family) --
    import subprocess
    n_glb = n_obj = 0
    chardir = out / "characters"; propdir = out / "models"
    for u in units:
        meshes = list(u.glob("type_34000007/*.bin"))
        if not meshes: continue
        skels = list(u.glob("unk_04000001/*.bin"))
        has_rig = any(parse_skeleton(s.read_bytes()) and
                      len(parse_skeleton(s.read_bytes())) >= 10 for s in skels)
        if has_rig:
            chardir.mkdir(parents=True, exist_ok=True)
            r = subprocess.run([sys.executable, str(HERE/"lu_rig.py"), str(u),
                                "-o", str(chardir/f"{u.name}.glb")],
                               capture_output=True, text=True)
            if (chardir/f"{u.name}.glb").exists(): n_glb += 1
        else:
            propdir.mkdir(parents=True, exist_ok=True)
            for m in meshes:
                try:
                    lu_convert.convert_mesh(m.read_bytes(), propdir/f"{u.name}_{m.stem}")
                    n_obj += 1
                except Exception: pass
    report.append(f"rigged character GLBs: {n_glb}; rigid prop OBJs: {n_obj}")
    # scripts: PiP ships plaintext Lua source in animation chunks
    sdir = out / "scripts"
    n_lua = 0
    for u in units:
        for c in sorted(u.glob("animation/*.bin")):
            r = pip_scripts.extract_lua(c.read_bytes())
            if r and len(r[1].strip()) > 2:
                rel, src = r
                dest = sdir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not dest.exists():
                    dest.write_text(src, encoding="utf-8", errors="replace")
                    n_lua += 1
    report.append(f"Lua source scripts extracted: {n_lua} (plaintext, "
                  f"original tree rebuilt under scripts/)")

    # 5. audio ----------------------------------------------------------
    if cus:
        (out / "audio").mkdir(exist_ok=True)
        for f in cus:
            shutil.copy(f, out / "audio" / (f.stem + ".txt"))
        report.append(f"audio manifests copied: {len(cus)}")

    (out / "report.txt").write_text("\n".join(report) + "\n")
    print("\n".join(report))
    print(f"\ndone -> {out}")


if __name__ == "__main__":
    main()
