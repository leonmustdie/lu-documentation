#!/usr/bin/env python3
"""
lu_dump.py — ONE command to dump everything from Naughty Bear .lu/.cu
files: chunks, textures, meshes, rigged + animated characters, and
decompiled Lua source.

usage:
  python3 lu_dump.py <game_folder_or_lu_files...> -o dump/

output layout:
  dump/
    extracted/<unit>/...        raw chunks by type (manifest.tsv each)
    textures/<unit>/*.png       all textures
    models/<unit>/*.obj         all meshes (with UVs)
    characters/<unit>.glb       rigged + fully animated glTF characters
    scripts/<unit>/*.luac       standard Lua 5.1 bytecode (unluac-ready)
    scripts/<unit>/*.lua        decompiled source (when unluac.jar+java)
    audio/<unit>.txt            .cu sound manifests
    report.txt

requires: numpy, Pillow. Optional: java + unluac.jar (same dir) for
automatic decompilation; build it once with:
  git clone https://github.com/HansWessels/unluac
  javac -d build $(find unluac/src -name "*.java")
  cd build && jar cfe ../unluac.jar unluac.Main unluac
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import naughty_lu                                       # noqa: E402
import lu_convert                                       # noqa: E402
import lua_decompile                                    # noqa: E402


def sh(mod, argv):
    """run a sibling tool's main() with custom argv"""
    old = sys.argv
    sys.argv = [mod.__file__] + argv
    try:
        mod.main()
    finally:
        sys.argv = old


def main():
    ap = argparse.ArgumentParser(
        description="dump everything from Naughty Bear .lu/.cu files")
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--no-glb", action="store_true",
                    help="skip rigged/animated character export")
    args = ap.parse_args()
    out = Path(args.out)
    (out / "extracted").mkdir(parents=True, exist_ok=True)

    lus, cus, pip_files = [], [], []
    for inp in args.inputs:
        p = Path(inp)
        if p.is_dir():
            for f in sorted(p.rglob("*.lu")):
                if f.read_bytes()[:4] == b"\x05LUH":
                    pip_files.append(f)
                else:
                    lus.append(f)
            cus += sorted(p.rglob("*.cu"))
        elif p.suffix.lower() == ".lu":
            if p.read_bytes()[:4] == b"\x05LUH":
                pip_files.append(p)
            else:
                lus.append(p)
        elif p.suffix.lower() == ".cu":
            cus.append(p)
    report = [f"inputs: {len(lus)} .lu, {len(cus)} .cu"]
    if pip_files:
        report.append(f"SKIPPED {len(pip_files)} Panic in Paradise (LUH) "
                      f"files — this tool is for the original Naughty "
                      f"Bear; run pip_dump.py on these instead:")
        report += [f"  {f.name}" for f in pip_files[:20]]

    # 1. extract ------------------------------------------------------
    failed = []
    for f in lus:
        try:
            sh(naughty_lu, ["extract", str(f), "-o", str(out / "extracted")])
        except SystemExit:
            pass
        except Exception as e:
            failed.append(f"{f.name}: {e}")
    if failed:
        report.append("EXTRACTION FAILURES (corrupt/truncated files):")
        report += [f"  {x}" for x in failed]

    units = [u for u in sorted((out / "extracted").iterdir()) if u.is_dir()]
    report.append(f"units extracted: {len(units)}")

    # 2. textures + models -------------------------------------------
    try:
        sh(lu_convert, [str(out / "extracted"), "-o", str(out)])
    except SystemExit:
        pass
    n_tex = n_obj = 0
    for unit in units:
        for ext, sub in ((".png", "textures"), (".dds", "textures"),
                         (".obj", "models")):
            for f in unit.rglob(f"*{ext}"):
                dest = out / sub / unit.name
                dest.mkdir(parents=True, exist_ok=True)
                shutil.move(str(f), dest / f.name)
                if ext == ".obj":
                    n_obj += 1
                elif ext == ".png":
                    n_tex += 1
    report.append(f"textures: {n_tex} png, models: {n_obj} obj")

    # 3. rigged + animated characters ---------------------------------
    if not args.no_glb:
        import lu_rig                                  # noqa: E402
        (out / "characters").mkdir(exist_ok=True)
        shared = [str(u) for u in units
                  if "shared" in u.name or u.name == "multiplayer"]
        n_glb = 0
        for u in units:
            has_skel = any((u / t).exists() and any((u / t).iterdir())
                           for t in ("unk_04000001",))
            has_mesh = (u / "mesh_buffers").exists()
            if not (has_skel and has_mesh):
                continue
            try:
                sh(lu_rig, [str(u)] + [s for s in shared if s != str(u)] +
                   ["-o", str(out / "characters" / f"{u.name}.glb")])
                n_glb += 1
            except (SystemExit, Exception):
                pass
        report.append(f"animated characters exported: {n_glb}")

    # 4. scripts -------------------------------------------------------
    try:
        sh(lua_decompile, [str(out / "extracted"),
                           "-o", str(out / "scripts"),
                           "--jar", str(HERE / "unluac.jar")])
        n_lua = len(list((out / "scripts").rglob("*.lua")))
        report.append(f"scripts decompiled to source: {n_lua}")
    except (SystemExit, Exception) as e:
        report.append(f"script stage error: {e}")

    # 5. audio manifests ----------------------------------------------
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
