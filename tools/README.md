# Naughty Bear reverse-engineering toolkit — source code (latest builds)

Complete, current source for every tool built to reverse-engineer the
original Naughty Bear (2010, Xbox 360) and Naughty Bear: Panic in
Paradise. This is CODE ONLY — extracted assets ship in the separate
naughtybear1_complete / pip collections. Run any tool on your own files.

Requires Python 3 + numpy + Pillow; Java for unluac (script decompiling).

## Tools

| file | purpose | games |
|---|---|---|
| naughty_lu.py   | container extractor; auto-detects NB1 `x36` (LZX-compressed) and PiP `LUH` formats | both |
| lu_convert.py   | textures -> PNG, meshes -> OBJ (batch) | both |
| lu_rig.py       | rigged `.glb` exporter (skeleton + geometric skin-palette solve); reads `mesh_buffers/` and PiP `type_34000007/` | both |
| lu_anim.py      | rigged + **animated** `.glb` exporter (NB1); uses nb1_clipfix | NB1 |
| nb1_clipfix.py  | NB1 animation channel decoder — all 3 encodings (raw-uniform, keyed Hermite, u8-quantized), size-based family detection | NB1 |
| nb1_reexport.py | batch NB1 animated-character exporter (CLI wrapper) | NB1 |
| pip_anim.py     | rigged + animated `.glb` exporter (PiP); hash-mapped tracks | PiP |
| pip_scripts.py  | extracts PiP **plaintext Lua source** from animation chunks | PiP |
| lua_decompile.py| NB1 compiled-Lua -> standard 5.1 bytecode -> source (drives unluac); resolves 0xFE interned-hash constants via a CRC32 dictionary | NB1 |
| lu_dump.py      | one-shot: dump everything from an NB1 game folder | NB1 |
| pip_dump.py     | one-shot: dump everything from a PiP game folder | PiP |
| unluac.jar      | Lua 5.1 decompiler (built from source, GPL) | NB1 |

## Dependency graph (all import-verified)
    naughty_lu        (standalone)
    nb1_clipfix       (standalone)
    pip_scripts       (standalone)
    lua_decompile     (standalone)
    lu_convert     <- naughty_lu
    lu_rig         <- lu_convert
    lu_anim        <- lu_convert, lu_rig, nb1_clipfix
    nb1_reexport   <- lu_convert, lu_rig, nb1_clipfix, lu_anim
    pip_anim       <- lu_convert, lu_rig, lu_anim
    lu_dump        <- naughty_lu, lu_convert, lu_anim
    pip_dump       <- naughty_lu, lu_convert, lu_rig, pip_scripts

## Quick start
    # original Naughty Bear
    python3 lu_dump.py  /path/to/naughtybear_files/ -o nb1_dump/
    # Panic in Paradise
    python3 pip_dump.py /path/to/pip_files/         -o pip_dump/

Both produce extracted chunks, textures (PNG), models (OBJ), rigged +
animated character GLBs, scripts, audio manifests, and a report.

## Format docs
docs/LU_FORMAT.md  — NB1 container, meshes, textures, skeletons,
                     skinning, animation (incl. the 3 channel encodings),
                     compiled-Lua script format
docs/PIP_FORMAT.md — PiP LUH container, chunk compatibility matrix,
                     plaintext scripts, engine API, mesh + animation
docs/CU_FORMAT.md  — `.cu` audio manifest format

## Known limits
- NB1 animation: ~98% of rotation/translation channels decode; scale
  channels (0x74 family) and a few rare encodings degrade to rest pose.
- PiP animation: dominant uniform encoding handled; minority encodings
  could benefit from nb1_clipfix's size-based decoder (pending).
- PiP Scaleform UI units and in-executable framework bytecode: not decoded.
