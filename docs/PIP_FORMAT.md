# Naughty Bear: Panic in Paradise — file format documentation

PiP ("b2" in embedded paths — Bear 2) is a separate game with its own
engine generation. It ships TWO container formats side by side:
legacy x36 archives (byte-identical NB1 data it reuses — see
LU_FORMAT.md) and the new LUH container for all PiP-native content.
This document covers PiP-native formats only.

## 1. LUH container (version 05)

PiP introduces a new container, magic `05 'LUH'`, alongside unchanged
legacy x36 files. Fully decoded; `naughty_lu.py` auto-detects both.

Layout (big-endian):

| offset | content |
|-------:|---------|
| 0x00 | `05 'LUH'`, u32 0x0900, u32 0 |
| 0x0c | u32 record_count |
| 0x10 | records × 39 bytes: `{u32 hash, u32 type, u32 link (ffffffff), u32 size, u32 image_offset, u32 flags, 15B extra}` |
| after | `{u32 image_size, u32 1, u32 image_size, u32 segment_size (0x100000), u32 segment_count, u32 compressed_sizes[n]}` |
| after | XMemCompress LZX streams, one per segment, **fixed 32 KB window**; the final segment may decode at full 0x100000 granularity and is trimmed to the remainder |

Hashes remain CRC32(lowercase). Chunk type ids carry over with the
high nibble bumped where formats changed: texture `14200007 -> 34200007`
(header carries width/height at +0x08/+0x0c), mesh `04000007 ->
34000007` (now a sub-chunk container). Skeleton `04000001` is
**byte-compatible** — the NB1 parser works unchanged.

### 1.1 PiP content survey (423 files decoded)

* Per-item archives: every weapon (`katana.lu`, `quantumrifle.lu`, ...)
  and every costume piece as `<name>_top/_mid/_bottom/_acc.lu` —
  costume meshes ship with partial skeletons (12–64 bones) that bind
  onto the character rig.
* Bear rig is now **115 bones** (NB1: 117); named NPCs use 122–123.
  No 116-bone rig exists in PiP either; NB1's 147 orphan clips are
  presumably cut content.
* anim_clip (04300000): duration still f32 at +0x10 and track count at
  +0x20, but the track table/curve encoding is REWORKED (data appears
  curve-compressed; not yet decoded).
* `tallyscreen.lu` and `global.lu` need the padded-final-segment
  fallback (implemented); all 423/423 LUH files extract.

## 2. Chunk format compatibility matrix (vs NB1)

| content | type id | status |
|---|---|---|
| skeleton | 04000001 | **identical** — NB1 parser works unchanged |
| texture | 34200007 (was 14200007) | **identical chunk format** — NB1 converter works; only the container type id changed |
| mesh | 34000007 (was 04000007) | new sub-chunk container; NOT yet decoded |
| anim_clip | 04300000 | header keeps duration (+0x10) and track count (+0x20); track table + curve encoding REWORKED; NOT yet decoded |
| scripts | — | compiled Lua is NOT present in any examined PiP file; chunks under animation/ are reference stubs ({hash, 04b00000}). Script bodies presumably live in not-yet-obtained files |
| sound | 04c000xx + .cu | .cu manifests same plain-text format |

## 3. Rigs

* Player/NPC bear rig: **115 bones** (NB1: 117)
* Named NPCs: 122–123 bones
* Costume pieces (`*_top/_mid/_bottom/_acc.lu`) carry partial
  skeletons (12–64 bones) that bind onto the character rig — the
  costume system is data-driven via these per-garment sub-rigs.

## 4. Toolchain separation

Use `pip_dump.py` for PiP files and `lu_dump.py` for NB1 files. Both
auto-detect container formats and refuse to run game-inappropriate
decoders; nothing is ever written into the extraction input tree.

## 5. Decode status update (session 2)

Verified gameplay-critical files decode fully: `skinnaughty.lu` (the
**123-bone player bear** rig + 8 textures), `levelcommon.lu`, all area
files, every weapon and costume archive.

### 5.1 Container decode fix
LUH segments decompress to a 1 MB output each via a 32 KB LZX window —
these are independent quantities. The segment-assembly loop now tracks
true per-segment output length and supports frame-terminated decoding
for segments shorter than the nominal 1 MB granularity.

### 5.2 Front-end framing variant (OPEN)
Eight UI/logo files — `startmenu`, `optionsmenu`, `global`,
`tallyscreen`, `gameconfiguration`, `openinglogo`, `manualmenu`,
`trialcompletescreen` — contain one or more segments using a different,
not-yet-decoded XMem framing (leading frame headers read as 0x8010 /
0x8000, inconsistent with the standard u16-compressed-size convention).
Extraction is now **resilient**: undecodable segments are zero-filled,
records landing in them are reported as `undecoded-segment` and skipped,
and all records in decodable segments still extract normally. No file
fails outright.

### 5.3 Player mesh (OPEN)
`skinnaughty.lu` carries the rig and textures but no standalone
`mesh_buffers` chunk; PiP appears to store skinned geometry inside the
`34000007` container (a reworked mesh format, still to be decoded).
Costume/weapon meshes are likely the same. Decoding 34000007 is the
prerequisite for exporting PiP characters as GLB.

## 6. Scripts — PiP ships PLAINTEXT Lua source (session 3)

Correcting session 2: PiP does ship its scripts — as **plaintext Lua
source**, not compiled bytecode. They live in the same `animation`
chunks (type 04b00000) that hold compiled bytecode in NB1, but the
payload is readable source text, comments and XML doc-tags intact.
`pip_scripts.py` slices them out and rebuilds the original tree.

Chunk layout (big-endian):

| offset | field |
|-------:|-------|
| 0x00 | u32 name hash |
| 0x04 | u32 type = 0x04b00000 |
| 0x10 | u32 path_offset |
| 0x14 | u32 path_length (embedded `z:\nb2_data\...\X.lua` path) |
| 0x18 | u32 source_offset |
| 0x1c | u32 source_length (the Lua source text) |
| 0x20 | u32 source_end |
| 0x24 | u32 trailer_length |

**381 unique scripts** (838 chunks incl. per-unit duplicates) covering
the full gameplay layer: AI/scare state machines, destructibles,
interactives (jukebox, fridge, arcade, defluffication machine, etc.),
cutscene manager, faction utilities, the wardrobe/costume system, and
the engine-binding libraries. Engine functions are exposed to Lua as an
`engine.*` table (e.g. `engine.ScriptUtil_StringToHash`); string-hash
constants are inlined with a self-documenting comment, e.g.
`--[[HASH:"ST_CKDeflufficationMachine":0x7061d03b]]1885458491`.

## 7. The executable (default.xex) and engine.ini

`engine.ini` confirms the engine generation: project codename
**nbear2**, A2M/Behaviour's in-house engine, **FMOD** audio, **Scaleform**
UI (which is what the front-end/menu .lu units in §5.2 contain —
compiled Flash GFx, hence their different segment framing), and a Lua VM
with its own GC thread (`THREAD_STACKSIZE_LUAGC`, `OPTIM_TOGGLE_LUAGC`,
`no-lua-prints`). Retail heap 390 MB / storage 210 MB.

`default.xex` is a **retail-encrypted** XEX2 image (encryption type 1,
compression none): the PE body is AES ciphertext, so it contains no
readable strings or Lua until decrypted. Decryption requires the
published Xbox 360 retail key — a DRM circumvention step that the
standard preservation tools (e.g. xextool) perform; this toolkit does
not implement it. Once a decrypted image is produced by such a tool,
its PE/.data can be analysed normally. Note: with 381 Lua scripts
already recovered as source, the engine-side C++ in the XEX is mostly
of interest for the native functions behind the `engine.*` API, not for
gameplay logic.

## 8. Decrypted executable analysis (session 4)

A user-decrypted `naughty.xex` (xextool `-e u`, retail key) was analysed
(pure inspection of the decrypted image — no circumvention performed by
this toolkit). Findings:

* The PE body is now readable: kernel imports (`xboxkrnl.exe`), the
  embedded `engine.ini`/`A2M_INI`, FMOD and Scaleform strings.
* **40 embedded compiled-Lua chunks** (Lua 5.1, **little-endian**, with
  a console-build header quirk — `lua_Number` is a 4-byte float and the
  trailing flag reads as 0x08, same family of non-stock header as NB1).
  11 parsed cleanly and are the **core framework layer**: `base`,
  `table`, `simple`, `objectcache`, `orderedset`, `priorityqueue`,
  `unorderedarrayset`, `introspection`, `wrapper`, `socketscheduler`,
  `timer`. These are engine plumbing beneath the 381 data-driven
  gameplay scripts; decompiling them needs the same header-fixup
  transcode as NB1, adapted for LE + float numbers (not yet done — low
  priority since gameplay logic is all in the source scripts).

* **engine.* native API surface: 872 distinct functions** harvested from
  the gameplay scripts — see PIP_ENGINE_API.md. This is the C++↔Lua
  contract and the concrete reimplementation checklist. Largest
  subsystems by call volume: GSLoading (loading screens), ScriptUtil,
  HudCombatComboMeter, ModeledObject, FearEvent, HazingScoreMgr /
  HazingPlayerManager (the scare/torment scoring core), MainCameraParameters
  (55 setters), DamageFXConfigHelper, the grade-tier enums
  (eBRONZE/SILVER/GOLD/PLATINE_GRADE).
