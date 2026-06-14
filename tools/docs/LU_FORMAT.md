# Naughty Bear `.lu` Resource Container Format

Reverse-engineered from *Naughty Bear: Gold Edition* (Xbox 360) — A2M /
Artificial Mind & Movement engine. Validated against `payphone.lu`,
`dynamicobjects_shared_resources.lu`, and `naughtyisland_shared_resources.lu`.

**`.lu` files are not Lua bytecode.** (Lua bytecode begins with `1B 4C 75 61`,
"\x1BLua"; these don't.) They are big-endian binary resource containers — most
likely "loadable units" — holding textures, animations, scene graphs, sounds,
skeletons, mesh data, and gameplay object definitions. The data payload is
optionally compressed with the Xbox 360's XCompress library (XMemCompress LZX
streams).

All multi-byte integers are **big-endian** (PowerPC / Xenon). All offsets
written as `0x20 + rel` are relative to file offset `0x20`, the origin used by
every internal pointer in the header.

---

## 1. Top-level layout

```
+-------------------------------+ 0x00
| fixed header                  |
+-------------------------------+ 0x20
| pointer-based header structs  |   dep names, pool info, footer
|   ...                         |
| record table (0x18 * count)   |
+-------------------------------+ data_base = 0x20 + [0x40]
| data region                   |   raw image, or XMemCompress LZX stream
+-------------------------------+ EOF
```

## 2. Fixed header (0x00 – 0x1F)

| offset | size | value / meaning |
|-------:|-----:|-----------------|
| 0x00 | 1 | `0x03` — version |
| 0x01 | 3 | platform tag, `"x36"` = Xbox 360 build |
| 0x04 | 4 | `0x04100000` — format/build version (same in all samples) |
| 0x08 | 4 | `0x276686F2` — build/toolchain hash (same in all samples) |
| 0x0C | 4 | `0x00000004` |
| 0x10 | 4 | `0x00000004` |
| 0x14 | 1 | dependency count |
| 0x15 | 2 | `0x0000` (padding) |
| 0x17 | 9 | magic string `"--\nITCRAP"` (yes, really) |

## 3. Pointer header (from 0x20)

`0xFFFFFFFF` words in this region are **runtime pointer placeholders** —
slots the engine patches with real pointers after loading. (The same is true
of the placeholder in each record, and `0xEFEFEFEF` fills inside chunk data.)

| offset | meaning |
|-------:|---------|
| 0x20 | placeholder |
| 0x24 | rel offset of dependency name strings (`0xFFFFFFFF` if none) |
| 0x28 | length of dependency name(s) incl. NUL (0 if none) |
| 0x2C | placeholder |
| 0x30 | 0 |
| 0x34 | rel offset → **pool info** struct |
| 0x38 | rel offset → **footer** struct |
| 0x3C | 0 |
| 0x40 | `data_base - 0x20` (header size minus 0x20) |
| 0x44 | dependency filename strings, NUL-terminated (`0xBF` pad after) |

Dependency names are full `.lu` filenames (e.g.
`dynamicobjects_shared_resources.lu`). Only a single-dependency sample was
available; multiple deps are presumed to be consecutive NUL-terminated
strings.

### 3.1 Pool info struct

| field | compressed file | raw file | meaning |
|------:|----------------:|---------:|---------|
| +0x00 | 2 | 0 | codec (2 = LZX, 0 = raw) |
| +0x04 | image size | image size | **image size** — total uncompressed data size |
| +0x08 | `0x00100000` | 0 | **LZX window size** in bytes (2^20 = 1 MiB); 0 if raw |
| +0x0C | N | 1 | **compression segment count** |
| +0x10 | rel → sizes array | `0xFFFFFFFF` | pointer (rel 0x20) to `u32 segment_sizes[N]` |
| +0x14 | N | 0 | segment count again |

The image is divided into ⌈image_size / window⌉ window-size (1 MiB) slices;
each slice is compressed as an **independent XMemCompress stream** whose
stored byte count is `segment_sizes[i]`. The last slice holds the remainder.
`sum(segment_sizes) == file_size − data_base` exactly. Small files fit in a
single segment, which is the degenerate case originally observed in
payphone. **If a segment's stored size equals its uncompressed slice size,
that segment is stored raw** (the codec is bypassed for incompressible or
trivially small data — e.g. the 16-byte stub images in `ep2*_area4.lu`).

`image_size` always equals the exact end of the highest chunk
(`max(offset+size)` over the record table).

### 3.2 Footer struct

| field | meaning |
|------:|---------|
| +0x00 | rel offset of record table |
| +0x04 | record count |

The footer is followed by a `BF BF BF BF` pad word, then the record table,
which ends exactly at `data_base`.

## 4. Record table

`count` records, 0x18 bytes each:

| offset | field | meaning |
|-------:|-------|---------|
| +0x00 | hash | **CRC32 (standard zlib polynomial) of the lower-cased resource name** — e.g. `crc32("payphone") = 0x4AF33561` |
| +0x04 | type | resource type code (§6) |
| +0x08 | — | `0xFFFFFFFF` runtime pointer placeholder |
| +0x0C | size | uncompressed chunk size in bytes |
| +0x10 | offset | chunk offset **within the decompressed image** (`0xFFFFFFFF` for external references) |
| +0x14 | flags | pool / linkage flags (§5) |

Chunks are packed in record order in the image with small alignment gaps
(0x10 for CPU data; GPU/audio data is 0x800/0x1000-aligned).

Names are not stored in the table. Many can be recovered because chunk data
embeds them: `sound_binding`/`sound_event` chunks contain the `FX_*` name in
plaintext, `game_object` chunks contain the object name, and most scene-type
chunks begin with their own `{hash, type}` pair, so hashes can be confirmed.
Hashing is case-insensitive (names are lower-cased before CRC32).

## 5. Flags word

Observed values: `04F000EF`, `0CF000EF`, `0BF004EF`, `04F00CEF`, `0CF001EF`.
Byte layout `PP F0 XX EF`:

| bits | meaning |
|------|---------|
| byte 0 (`PP`) | destination memory pool: `04` = CPU/main RAM, `0C` = GPU/physical (textures, mesh buffers, skeletons), `0B` = audio (wave data) |
| byte 2 bit 0 (`XX & 01`) | **external reference**: chunk is not stored in this file; resolve by `hash` in a dependency `.lu` (offset is `0xFFFFFFFF`) |
| byte 2 other bits (`0C`, `04`) | unknown sub-pool/usage flags |
| bytes 1, 3 | constant `F0` / `EF` |

External references were verified: payphone's record 71 (`hash 2106B103`,
texture) is byte-identical to the chunk of the same hash stored in
`dynamicobjects_shared_resources.lu` (and in
`naughtyisland_shared_resources.lu`, which duplicates it).

## 6. Resource type codes

| code | name | evidence |
|-----:|------|----------|
| `14200007` | texture | data begins `34 20 00 07`, contains width/height u32s and a 4CC tag (`rutx` = "xtur" reversed, `tess`, …) |
| `04000007` | mesh/GPU buffers | GPU pool, large, contains `34 20 00 04` sub-blocks |
| `04800018` | skeleton | |
| `04B00000` | animation clip | resolved names: `payphone_call`, `payphone_repair`, `payphone_sabotage`, `payphone_dostealthkill`, … |
| `04300012` | scene dictionary | exactly one, always record 0 |
| `0430000E` | scene root | exactly one per scene; begins with `{hash, type}` twice, then a table of `{04 30 00 0F, offset}` child pointers |
| `04300000` | scene node | |
| `04300003` | scene/anim binding | |
| `04300009` | scene link | fixed 0x48 bytes |
| `04C00000` | sound event | contains `fx_l1_*` names |
| `04C0000F` | sound cue | |
| `04C00008` | sound wave data | audio pool, sample data |
| `04C00010` | sound binding | contains the `FX_*` name and an embedded `{hash, type}` cross-reference to its wave chunk |
| `04C0000A` | sound params | |
| `00000102` | gameplay object definition | contains the plaintext object name (`payphonenb`) |
| `04000001` | unknown | float-heavy |
| `0420002F` | unknown (collision/physics?) | |

## 7. Data region & compression

* **Raw** (`codec == 0`): the data region *is* the image; `chunk =
  file[data_base + offset : … + size]`.
* **Compressed** (`codec == 2`): the data region is a concatenation of
  `segment_count` **independent XMemCompress streams** (Xbox 360 XCompress,
  `XMEMCODEC_LZX`), one per 1 MiB (window-size) slice of the image; the
  per-segment stored sizes come from the pool-info array (§3.1). Each
  stream is a chain of frames:

  ```
  frame := u16_be compressed_size, payload    ; uncompressed = min(0x8000, remaining)
         | 0xFF, u16_be uncompressed_size,
                 u16_be compressed_size, payload      ; explicit (partial) frame
  ```

  Frames repeat until their uncompressed sizes total the slice size (a few
  pad bytes may follow within the segment). Within one segment the
  concatenated payloads form **one continuous LZX bitstream**
  (frame-realigned to 16 bits every 0x8000 output bytes, no block resets),
  decoded with the window size from the pool-info struct (`0x100000` →
  window bits 20). Segments whose stored size equals the slice size are raw
  copies (no frames at all). Validated bit-exact across all 255 sample
  files (single- and multi-segment, raw segments included).

## 8. Validation summary

* Header-derived table position, record count, and `data_base` are exact in
  all three samples; record tables end precisely at `data_base`.
* `image_size` equals `max(offset+size)` in all samples; chunks never
  overlap; raw files' chunks tile the data region.
* Decompressed payphone chunks match raw shared-resource chunks structurally
  (identical texture headers, identical scene-root layout), and the
  duplicated texture `2106B103` is byte-identical across files.
* Entropy: payphone data region = 7.997 bits/byte (compressed) vs 1.7–6.6
  for raw regions.
* `crc32(lowercase(name))` confirmed against 33 independently recovered
  names embedded in chunk data.

## 9. Known limitations

* Only X360 (`"x36"`) samples were available; the PS3 variant presumably has
  a different platform tag and may use different compression/endianness.
* Multi-dependency header layout is unconfirmed (all samples have ≤ 1 dep).
* Several type codes and flag bits remain unidentified (see tables).
* Inner chunk formats (texture pixel layout/tiling, animation curves, …) are
  containers' contents and out of scope here, though headers are sketched
  in §6.

---

## 10. Texture chunk format (type `14200007`)

Validated against all 158 textures in the three sample files; all decode to
correct, recognizable images.

| offset | meaning |
|-------:|---------|
| +0x00 | tag `34 20 00 07` |
| +0x08 | height (texels) |
| +0x0C | width |
| +0x10 | bits per pixel (4 = DXT1, 8 = DXT5) |
| +0x14 | 4CC class tag (`rutx`, `salc`, `tess` — purpose unknown) |
| +0x20 | header size (0x40) |
| +0x24 | mip level count |
| +0x28 | Xenos format dword; **low 6 bits = GPU texture format**: `0x12` DXT1, `0x14` DXT4/5 (observed dwords `1A200152`, `1A200154`) |
| +0x38 | pixel data offset within chunk (always 0x1000; GPU data is 4 KB-aligned) |
| +0x3C | pixel data size |

Pixel data decoding (Xbox 360 GPU conventions):
1. **Byte-swap** every 16-bit word (Xenos stores DXT data big-endian).
2. **Untile** with the standard `XGAddress2DTiledOffset` algorithm, operating
   on DXT blocks (8 or 16 bytes per block). Tiled surfaces are padded to
   32×32 blocks, so untile on the aligned grid and crop.
3. The base mip level starts at the data offset; wrap in a DDS header and
   any DDS-capable tool (including Blender) reads it.

## 11. Mesh chunk format (type `04000007`)

Validated on the payphone mesh (2 submeshes, 730 verts, 610 tris; UV-mapped
render matches the texture atlas).

* Chunk begins `00 00 00 20`; internal sub-blocks are tagged `34 20 00 xx`.
  A submesh list `{offset, count}` sits at +0x28/+0x2C, pointing to
  per-submesh descriptor blocks.
* Each descriptor block contains, among other fields:
  * a bounding sphere (4 floats: center xyz + radius);
  * a **buffer quad `{vb_offset, vb_size, ib_offset, ib_size}`** — chunk-
    relative, buffers 4 KB-aligned (payphone: `{0x1000, 0x3DC0, 0x5000,
    0x59C}` and `{0x6000, 0x19D0, 0x8000, 0x282}`);
  * texture references as **`{crc32_hash, 0x04200007}` pairs** (resolve via
    the record table / dependency files — this binds submesh to diffuse
    texture);
  * a reference `{hash, 0x0420002F}` to the unknown chunk type (material or
    collision data, presumably).
* After each index buffer there is a trailer `{0x20, index_count,
  6 (= strip primitive), 0, ptr → vertex declaration blob}`; the declaration
  is an Xbox 360 D3D vertex-element list terminated by `FFFFFFFF`.
* **Vertex buffers** (big-endian): `float3 position` at +0; vertex **stride
  varies per submesh** (32 = pos + unit float4, likely a tangent-frame
  quaternion + UV; 28 and others observed). **UV** is a pair of half-floats
  near the end of the vertex (stride 32 → +28; stride 28 → +24). DirectX
  texture-space V (flip for OpenGL/Blender conventions: v' = 1 − v).
* **Index buffers**: 16-bit big-endian **triangle strips** with `0xFFFF`
  primitive-restart tokens; degenerate windows are skipped, winding
  alternates per strip step.
* Beware: padding fill `BF BF BF BF` decodes as the float −1.498, which can
  fool naive vertex scans.

The companion tool `lu_convert.py` implements both pipelines (textures →
DDS/PNG, meshes → OBJ/MTL with material binding) with per-submesh stride and
UV-offset auto-detection.

## 12. Skinned vertex layouts (characters)

Character meshes (bears) extend the prop layouts with skinning data
**before** the position, so position is not at vertex offset 0. Observed
layouts (all big-endian; `sNNpMMuvKK` = stride NN, position at +MM, UV at
+KK):

| layout | composition |
|--------|-------------|
| `s52p16uv48` | `u8×4` bone indices, `float3` blend weights (sum = 1, w₄ implicit), `float3` **position**, `float3` unit normal, packed tangent dword, 4 bytes, `half2` UV |
| `s48p12uv44` | `u8×4` indices, `float2` weights (2-bone), `float3` position, normal, tangent, `half2` UV |
| `s40p4uv36` | `u8×4` indices (rigid single-bone), `float3` position, `float3` normal, packed dwords, `half2` UV |
| `s32p4uv28` | `u8×4` indices, `float3` position, `float3` normal, `half2` UV |
| `s36p24` | indices, weights, 2 × u32, `float3` position — **no UV** (shadow-volume proxy mesh) |
| `s28p0uv24` | `float3` position (no prefix), `float3` normal, `half2` UV |

Note that even the "static" payphone vertices begin with a 4-byte index
prefix (`01 00 00 00`) — position at +4, not +0.

### 12.1 Vertex declarations

After each index buffer (in the following descriptor area) lives an Xbox
360 **vertex declaration**: 12-byte elements `{gpu_format,
usage<<16|usage_index, offset}` terminated by `FFFFFFFF`. Format
`0x001A2286` is `32_32_32_FLOAT` (the position); elements with offsets ≥
0x10000 belong to a second stream or are end markers. The declared offsets
describe the *unskinned reference layout*: the largest declared offset is
the **UV offset** (reliable), and the real position offset equals the
declared one plus the size of the injected per-vertex skinning block
(0–20 bytes). `lu_convert.py` therefore uses the declaration for stride/UV
and resolves the position offset with a constrained search scored by
triangle-strip locality and cross-submesh bounding-box consistency.

### 11.1 Submesh trailer (authoritative geometry metadata)

Immediately after each index buffer (4-byte aligned) sits a trailer:

| field | meaning |
|------:|---------|
| +0x00 | **vertex stride** in bytes |
| +0x04 | **count** — triangle count for lists; index-related for strips (unreliable, do not trust for strips) |
| +0x08 | **primitive type**: 4 = triangle **list**, 6 = triangle **strip** |
| +0x0C | 0 |
| +0x10 | pointer to the vertex declaration (chunk-relative) |
| +0x14 | declaration element count |

Misreading the first field as a tag (it is the stride) and decoding lists
as strips were the two big sources of "polygon soup": a list decoded as a
strip emits ~2x phantom triangles stretched across the model (the bears'
main bodies are lists; most props are strips). Index buffers may end with
`BFBF` pad words, which must be trimmed. **Buffer-offset quadruples found
by scanning that have no valid trailer are false positives** (descriptor
data that happens to look like a quad) and must be dropped — they were the
source of NaN-vertex submeshes.

### 11.2 Implicit-position declarations

Many static meshes (bear trap, area props, cutscene props) have
declarations whose element list starts at offset 0xC with **no
`0x001A2286` float3-position element**: the position is implicit at offset
0, before the first declared element, and there is **no skinning prefix**
(so no position-shift search applies). Skinned meshes declare the position
explicitly (usually at +4) and the real offset is the declared one plus
the injected per-vertex skinning block (0-20 bytes of blend weights /
extra indices).

## 13. `.cu` companion files

`.cu` files are **plain ASCII text**, CRLF line endings — no container, no
compression. Every line is a path of the form `streams\NAME.xma`: they are
**streamed-audio manifests** listing the Xbox 360 XMA audio streams the
same-named `.lu` unit needs (the engine evidently preloads/registers this
list when the unit is mounted). 193/193 game files follow this exactly
(22,239 lines, 3,210 unique paths). Conventions:

* Prefixes: `VO_` voice-over (80%), `FX_` sound effects, `AM_` ambience,
  `MU_` music.
* VO character codes: `DAV`, `NRM2`/`NRM3` (normal bears), `PLC01`
  (police), `ASH`, `BRN`, `CHR`, `DNGR` (danger bear), `SWAT`, `VAMP`,
  `PIRT`, `FRD`, `HUD`, `EDD`, `NAR` (narrator), `GRG`.
* Localized dialogue carries a locale suffix (`_en_US`, `_de_DE`, `_fr_FR`,
  `_it_IT`, `_es_ES`), and cutscene manifests exist per locale
  (`ep1cutscene.en_us.cu` …).
* Duplicate lines are intentional: one entry per referencing sound event
  (e.g. `characters.cu`: 6,813 lines, 2,271 unique).

## 14. Animation system (initial findings)

The animation data **is** inside the `.lu` files (the `.cu` files contain
none — they are audio manifests, see `CU_FORMAT.md`). Three chunk types
cooperate:

* **Type `043x00xx` "animation" chunks are NOT keyframe data** — they are
  compiled **Lua scripts** (state machines, selectors, databases). Each
  embeds its source path, e.g.
  `z:\devilsnightdata\assets\3d\characters\teddybears\naughtybear\scripts\
  naughtybearbodystatemachine.lua` ("Devil's Night" was the game's
  codename). They reference clips by name (`animWalkRightFoot`,
  `animStateIdle1`, ...).
* **Type `04300000` chunks are the animation clips** (previously
  mislabeled `scene_node` by this tool set; now extracted as
  `anim_clip/`). Verified: 137/137 in naughtybear, 252/252 in
  characters_shared_resources, 77/77 in naughtyisland_shared_resources
  match the clip signature. This type accounts for ~120 MB of the game —
  by far the largest data class after textures.
* **`scene_anim_binding`** chunks (type `04300003`) bind a clip to its
  user: +0x10 holds the target clip's CRC32 name hash; a `1.0f` float
  (playback rate) sits nearby. All 137 naughtybear bindings resolve to
  clip chunks.

### 14.1 Clip chunk layout (decoded so far)

| offset | meaning |
|-------:|---------|
| +0x00 | type dword `04300000`, 0, name hash, type again |
| +0x10 | `float` **duration in seconds** |
| +0x20 | pointer to track table (always 0x60), **track count** (≈ bone count, e.g. 117) |
| +0x30 | 4 floats — motion bounds/extent |
| +0x40 | pointer + count of a footer block near EOF |
| +0x60 | **track table**: `{u32 data_offset, u32 format}` per track; `FFFFFFFF` = no data (bone untouched by this clip) |

Track payloads are tightly byte-packed (offsets are not even
word-aligned). Observed format codes and their data sizes: fmt 1 ≈ 4–6
bytes (single static key — consistent with a 48-bit
"smallest-three"-packed quaternion), fmt 3 ≈ hundreds of bytes
(keyframed curves), fmt 2/6/8 = larger streams, fmt 0 = empty. **The
per-key bit packing is not yet decoded** — that, plus the skeleton's
bone-hash↔track mapping, is what stands between the current state and
re-rigged, animated bears.

### 14.2 Skeleton chunks

Type `04800018` payloads (extracted under `skeleton/`): no embedded bone
name strings; a plausible bone count field (e.g. 0x82 = 130 for the bear,
matching the 117-track clips plus leaf bones) and float data consistent
with bind-pose transforms. Bone names are likely stored as CRC32 hashes
matching the global naming scheme.

## 15. Skeletons (type `04000001`) and skinning — SOLVED

The real skeleton is type `04000001` (one per character; the type
`04800018` chunks under `skeleton/` are physics/ragdoll definitions
referenced from the socket section, not the skeleton).

Header (offsets are absolute, all big-endian):

| offset | meaning |
|-------:|---------|
| +0x10 | `{u32 records_ptr, u32 bone_count}` (naughtybear: 0x68, 117) |
| +0x18 | `{u32 quats_ptr, u32 count}` — per-bone rest local rotations, float4 **xyzw** |
| +0x30 | socket/attachment section ptrs + count (e.g. 22 sockets; references the 04800018 physics chunks by hash) |
| +0x48 | `u32 hash_table_ptr` — bone_count × `{u32 name_hash, u32 bone_index}` sorted by hash. Bone **names are not shipped**, only hashes |

Bone records: `bone_count` × 16B `{u32 transform_ptr, i32 parent, i32
first_child, i32 next_sibling}` (−1 = none; parents always precede
children). Each transform block is 144 bytes: two row-major 4×4 matrices
(world bind transform; translation in row 3; rotations may carry
**scale**, 0.015–1.0 on attachment bones) plus 16 scratch bytes.
Convention is row-vector (D3D): `W_child = L · W_parent`.

### 15.1 Skin palette

Skinned vertices carry 4 blend-index bytes that are **not** skeleton
indices — they enumerate the skeleton's *deform-bone subset* in
ascending order (the shipped files do not contain the palette; the
engine evidently rebuilds it from data not present, likely bone flags
stripped at build). `lu_rig.py` recovers the mapping geometrically:
weighted vertex centroids per skin slot are aligned to bone bind
positions under a strict-monotonicity constraint (dynamic-programming
sequence alignment). Validated on naughtybear: paw vertices resolve to
the arm chain (bones 70–78), facial slots to the facial rig, bind-pose
round-trip error = 0, and posed renders deform correctly. Caveat: facial
bones come in co-located control/deform pairs; for those the choice
within a pair is heuristic and may be off by one (visually identical at
bind, may matter for animation retargeting).

Rigid accessory submeshes (stride 40/32) use a private index space;
`lu_rig.py` binds them to the nearest bone.

### 15.2 Skeleton anatomy (the bears)

117 bones: 0–9 root/spine/neck chain, 10–68 the **facial rig**
(eyebrows, eyelids, muzzle/jaw chain 34–41, cheeks, ears 67/68 — the
first game already has a full facial setup), 69–116 limbs plus
attachment/twist helpers.

## 16. Animation clips — format ~70% decoded

Updating §14 with deeper findings (chunk type `04300000`, `anim_clip/`):

* Clip header: +0x10 `float duration_seconds`; +0x20 `{table_ptr=0x60,
  u32 track_count}`; track_count == skeleton bone_count and tracks map
  to bones **by index** (record order).
* Track table at 0x60: `{u32 data_off, u32 fmt}` per bone;
  `FFFFFFFF` = bone untouched.
* **fmt 1** — single static key, 4 bytes: a packed quaternion ending in
  a 2-bit tag (observed trailing 0x04 pattern; `00 00 00 04` = identity).
  Consistent with 2-bit largest-component + 3×10-bit smallest-three
  packing; exact bit order not yet confirmed.
* **fmt 3** — keyframed: the payload is a list of 16-byte
  sub-records `{u32 data_ptr, u32 sub_fmt, f32 end_time, u32
  count|flags}` — one per channel (observed 3 channels with key counts
  35/36/37 ≈ one per rotation axis or T/R/S split). Each channel's data
  block = header `{u32, u32, u32 times_ptr, u32}` then an array of
  **u16 key times** (monotonic; ticks, scale TBD) followed by an array
  of **float32 values** (small magnitudes, curve samples — possibly
  with tangents).
* fmt 0/2/6/8 exist; not yet mapped.

Still open: exact fmt-1 bit order, the u16 tick rate, whether fmt-3
floats are raw values or Hermite value+tangent pairs, and the fmt 2/6/8
encodings. The skeleton's rest quaternions give a ground-truth oracle
for finishing this.

### 16.1 Animation format — FINAL (decoded, exportable)

Decoded and validated by re-rendering clips on the rigged bears
(`lu_anim.py` exports them into the .glb files):

* Tracks map **1:1 to skeleton bones by index** (track count == bone
  count; clips only fit skeletons with the same bone count).
* Track payload = `fmt` × 16-byte channel records
  `{u32 data_ptr, u32 sub_fmt, f32 end_time, u8 tag << 24}`.
* Channel data header: `{u32 0, u16 key_count, u16 enc, u32 data_ptr,
  u32 data_size}`. Two encodings, keyed by the high byte of `enc`:
  * **0x34 (keyed)**: `key_count` × u16 key times at **240 ticks/second**,
    immediately followed (unaligned) by `key_count` × 4 float32
    `{value, tan_in, tan_out, w}` — a Hermite-style curve for ONE
    component; `value` is a **delta from the skeleton's rest pose** for
    that component.
  * **0x08 (uniform)**: `key_count` raw float32 samples at **30 Hz**,
    one component per channel; absolute values.
* Channel `tag` selects the component: 0x04/0x05/0x06 = translation
  x/y/z (metres, local space), 0x23/0x24/0x25 = rotation quaternion
  x/y/z (w recomputed for unit norm; sign follows the rest pose),
  0x08/0x09/0x0a = scale x/y/z.
* **fmt 1** (single static key, 4 bytes): `00 00 00 xx` = exactly the
  rest pose (67% of all statics). Non-zero statics are small packed
  component deltas (3 data bytes + tag byte); currently exported as
  rest pose — the residual error is a sub-degree pose offset.
* Quaternions are standard xyzw and drop straight into glTF rotations.

### 16.2 The "animation" chunks are compiled Lua 5.1

Each chunk under `animation/` is a compiled Lua script with a 0x84-byte
engine wrapper followed by a **standard Lua bytecode image**
(`\x1bLua`, version 5.1, big-endian, Xbox 360). The embedded source
paths name the originals (`...\scripts\naughtybearbodystatemachine.lua`).
These can be decompiled to readable source with `unluac`
(java -jar unluac.jar file.luac) or luadec built for big-endian 5.1 —
they contain the state machines, AI selectors, and gameplay glue logic.

### 16.3 Script bytecode format (full spec)

Each script chunk = 0x84-byte engine wrapper, then a Lua 5.1 image,
then an engine footer. Deviations from stock luac 5.1 (all
little-endian; the 360 byte-swaps at load):

| difference | detail |
|---|---|
| header | 13 bytes — the number-size byte (0x08) appears twice; drop one for the standard 12-byte header |
| top-level proto | omits the `nups` byte (always 0); nested protos are standard 4-byte `{nups, nparams, is_vararg, maxstack}` |
| constant type 0xFE | 8-byte interned string hash: CRC32 of the lowercase string, zero-extended to u64. Replace with a string constant to obtain stock bytecode |

`lua_decompile.py` performs this transcode, resolves 0xFE hashes
through a CRC32 dictionary mined from every string in the game files,
and drives unluac for source output (587/589 scripts decompile).
Unresolved hashes appear as `__hash_0x........` string literals.

## 17. Unit notes

* `characters.lu` = the game-wide sound database (2935 cues/bindings,
  934 events, 665 waves) plus a 1-bone dummy rig. Not characters.
* `startmenu.lu` region variants (`startmenu_<lang>.lu`) hold 521
  localization/UI chunks of type 04d00002 each.
* 147 clips target a 116-bone skeleton that is not present in any
  shipped Xbox 360 unit examined so far.

## 18. Panic in Paradise
Panic in Paradise uses a different container (LUH) and partially
different chunk formats. All PiP documentation lives in PIP_FORMAT.md;
this file covers the original Naughty Bear (x36 containers) only.

## 16.4 Animation channel encodings — CORRECTED (all three families)

Earlier exports decoded only one of three channel encodings, dropping
~80% of channels (symptom: characters barely moved). The channel data
header is `{u32 0, u16 key_count, u16 enc, u32 key_ptr, u32 data_size}`;
the **enc** field selects the encoding, and `data_size` must match the
family exactly (this is also how to disambiguate them safely):

| enc (lo byte) | family | layout | dequant |
|---|---|---|---|
| 0x1e | raw uniform | `key_count` × f32, uniform in [0,end_time] | value as-is (absolute); `data_size == 4·kc` |
| 0xf0 | keyed Hermite | u16 times @240/s, then `key_count` × N f32 `{value,…}` where **N = enc hi-nibble** (1–4) | value = float[0] per key, **delta from rest**; `data_size == 2·kc + 4·N·kc` |
| 0x78 | u8-quantized uniform | `{f32 min, f32 max, u32 flags}` then `key_count` × u8 | value = min + (max−min)·u8/255, uniform in time; `data_size == 12 + kc` |

The u8-quantized family (hi bytes 0x48/0x58/0x74…) is the **most common**
encoding in the shipped clips, so omitting it is what flattened the
animations. Tags as before: 0x04/05/06 translation xyz, 0x23/24/25
rotation quat xyz. Decoder enforces exact `data_size` per family and
rejects any channel whose dequantised values are non-finite or
|value|>100, so not-yet-modelled encodings degrade to rest pose rather
than injecting garbage. Implemented in nb1_clipfix.py (used by
lu_anim.py). Validated by render: full-body dynamic motion, ~14 active
channels/clip vs ~5 before.
