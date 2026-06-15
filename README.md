# Naughty Bear Reverse-Engineering Toolkit

Open tools and file-format documentation for the **Naughty Bear** games
(original *Naughty Bear*, 2010, and *Naughty Bear: Panic in Paradise*) on
Xbox 360. Extracts and converts the games' proprietary `.lu`/`.cu`
archives into open, standard formats (PNG, OBJ, glTF, Lua source).

This project is an **independent, non-commercial preservation and
research effort**. It is not affiliated with, endorsed by, or connected
to the games' original developers or publishers.

## ⚠️ Bring your own files

This repository contains **only original code and documentation**. It
ships **no game data** — no `.lu`/`.cu`/`.xex` files, no extracted
textures, models, scripts, or audio. To use these tools you must supply
files from a copy of the game you legally own. Nothing here redistributes
copyrighted content.

## What it does

| Capability | Output |
|---|---|
| Extract `.lu`/`.cu` archives (both games' container formats) | raw chunks |
| Convert textures | PNG |
| Convert meshes | OBJ |
| Export rigged characters | glTF (`.glb`) with skeleton + weights |
| Export animations | `.glb` with baked animation clips |
| Recover scripts | NB1: decompiled Lua • PiP: original Lua source |

## Quick start

Requires Python 3 with `numpy` and `Pillow`; Java is needed only for
Lua-script decompilation (NB1).

```bash
pip install numpy pillow

# original Naughty Bear
python3 tools/lu_dump.py  /path/to/naughtybear_files/ -o nb1_dump/

# Panic in Paradise
python3 tools/pip_dump.py /path/to/pip_files/         -o pip_dump/
```

Each dumper produces extracted chunks, textures (PNG), models (OBJ),
rigged character GLBs, scripts, audio manifests, and a report.

A small self-made sample (`samples/`) is included to demonstrate the
tools without any game data — see `samples/README.md`.

## Documentation

Full format specifications live in [`docs/`](docs/):

- **[`docs/LU_FORMAT.md`](docs/LU_FORMAT.md)** — NB1 container, meshes,
  textures, skeletons, skinning, animation (all three channel
  encodings), and the compiled-Lua script format.
- **[`docs/PIP_FORMAT.md`](docs/PIP_FORMAT.md)** — Panic in Paradise LUH
  container, chunk-compatibility matrix, plaintext Lua scripts, the
  engine API surface, and mesh/animation formats.
- **[`docs/CU_FORMAT.md`](docs/CU_FORMAT.md)** — `.cu` audio-manifest
  format.

## Tools overview

| File | Purpose |
|---|---|
| `tools/naughty_lu.py` | container extractor (auto-detects NB1 `x36` + PiP `LUH`) |
| `tools/lu_convert.py` | textures → PNG, meshes → OBJ |
| `tools/lu_rig.py` | rigged `.glb` exporter (skeleton + skin solve) |
| `tools/pip_scripts.py` | PiP plaintext-Lua source extractor |
| `tools/lua_decompile.py` + `tools/unluac.jar` | NB1 compiled-Lua → source |
| `tools/lu_dump.py` / `tools/pip_dump.py` | one-shot dumpers |

## Status

Solved for both games: container, textures, skeletons, meshes, rigged characters (mostly), and scripts. Known gaps are tracked in the docs.
(NB1 scale-channel animation; PiP Scaleform UI units and in-executable
framework bytecode).

## License

Code: [MIT](LICENSE). Documentation (`docs/`): [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

## Contributing

Corrections and additional format findings are welcome — open an issue or
pull request. Please do not attach game files or extracted assets to
issues or PRs.
