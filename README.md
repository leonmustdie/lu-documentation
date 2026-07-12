# Naughty Bear Reverse-Engineering Documentation

File-format documentation for the **Naughty Bear** games (original
*Naughty Bear*, 2010, and *Naughty Bear: Panic in Paradise*) on Xbox 360:
the `.lu`/`.cu` container formats, meshes, textures, skeletons, skinning,
and script formats, reverse-engineered from scratch.

This project is an **independent, non-commercial preservation and
research effort**. It is not affiliated with, endorsed by, or connected
to the games' original developers or publishers.

> **Looking for the tools?** The extractor, converter, and Lua
> read/edit/ship pipeline that originated in this repo now live in
> **[Seam Ripper](https://github.com/leonmustdie/seam-ripper)**, an
> upgraded, actively maintained rewrite with a GUI and a tested container
> layout/integrity check. This repo no longer ships tool code, it's the
> format write-up, still the reference for anyone extending or
> cross-checking the tools themselves.

## Bring your own files

This repository contains **only original documentation**. It ships **no
game data**, no `.lu`/`.cu`/`.xex` files, no extracted textures, models,
scripts, or audio. Nothing here redistributes copyrighted content.

## What's documented

| Capability | Format detail |
|---|---|
| `.lu`/`.cu` archives (both games' container formats) | header, record table, compressed/stored data image |
| Textures | Xenos format, tiling, byteswap |
| Meshes | vertex/index layout, submesh descriptors |
| Skeletons and skinning | bone hierarchy, skin-palette binding |
| Scripts | NB1: compiled Lua 5.1 bytecode • PiP: plaintext Lua source |

## Documentation

Full format specifications live in [`docs/`](docs/):

- **[`docs/LU_FORMAT.md`](docs/LU_FORMAT.md)** — NB1 container, meshes,
  textures, skeletons, skinning, and the compiled-Lua script format.
- **[`docs/PIP_FORMAT.md`](docs/PIP_FORMAT.md)** — Panic in Paradise LUH
  container, chunk-compatibility matrix, plaintext Lua scripts, the
  engine API surface, and mesh formats.
- **[`docs/CU_FORMAT.md`](docs/CU_FORMAT.md)** — `.cu` audio-manifest
  format.

## Samples

A small hand-authored sample (`samples/table_flat.lu`), containing no
game data, demonstrates the container format end to end, extract it with
[Seam Ripper](https://github.com/leonmustdie/seam-ripper)'s tools. See
`samples/README.md`.

## Status

Solved for both games: container, textures, skeletons, meshes, rigged
characters (mostly), and scripts. Known gaps are tracked in the docs
(NB1/PiP animation edge cases; PiP Scaleform UI units and in-executable
framework bytecode).

## License

Documentation (`docs/`, this README): [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
The sample generator (`samples/make_sample.py`) and repository scaffolding: [MIT](LICENSE).

## Contributing

Corrections and additional format findings are welcome, open an issue or
pull request. Please do not attach game files or extracted assets to
issues or PRs. Tooling contributions belong in
[Seam Ripper](https://github.com/leonmustdie/seam-ripper), not here.
