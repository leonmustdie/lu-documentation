# Naughty Bear: Panic in Paradise — file format documentation

PiP ("b2" in embedded paths) ships two container formats: legacy x36
archives (byte-identical NB1 data, reused as-is) and the new LUH
container for PiP-native content. Both use the same compression codec
(§2). This document covers PiP-native formats.

## 1. LUH container (version 05)

Magic `05 'LUH'`. Auto-detected alongside x36 by `naughty_lu.py`.

Layout (big-endian):

| offset | content |
|-------:|---------|
| 0x00 | `05 'LUH'`, u32 0x0900, u32 0 |
| 0x0c | u32 record_count |
| 0x10 | records × 39 bytes: `{u32 hash, u32 type, u32 link (ffffffff), u32 size, u32 image_offset, u32 flags, 15B extra}` |
| after | `{u32 image_size, u32 1, u32 image_size, u32 segment_size, u32 segment_count, u32 compressed_sizes[n]}` |
| after | one XMemCompress stream per segment (§2), each segment up to `segment_size` bytes decompressed (observed: 0x100000); the final segment is trimmed to the image remainder |

Correction to earlier notes: the compression **window** is set by
`segment_size` (0x100000 → 20-bit window), not 32 KB. 32 KB is the
XMemCompress **frame** size, a distinct quantity (§2.2), the
compressed-data chunking granularity within one window, not the window
itself.

Hashes are CRC32 (lowercase). Chunk type ids carry the NB1 id with the
high nibble bumped where the format changed: texture `14200007 →
34200007` (header carries width/height at +0x08/+0x0c), mesh `04000007
→ 34000007` (§7). Skeleton `04000001` is byte-compatible; the NB1
parser works unchanged.

## 2. Compression — XMemCompress / LZX

Both container formats compress their record image the same way: one
independent XMemCompress stream per segment. A segment is not one LZX
block; it's a sequence of **frames**, each frame an independent LZX
block sequence that decompresses to up to 32 KB (`LZX_FRAME_SIZE`).
Window size is set once per segment (§1), typically 0x100000 → 20-bit
window (the main tree's symbol count and the number of position slots
both derive from this).

### 2.1 Bitstream

16-bit little-endian words, consumed MSB-first: within one word, the
high byte's bits go first (7→0), then the low byte's (7→0). A stream
opens with one bit: Intel E8 call-translation flag. Every retail
sample seen is 0 (translation off); the 32-bit filesize field that
would follow a set bit has not been observed and is not implemented.

### 2.2 Frames and the outer XMemCompress wrapper

Distinct from the LZX bitstream's own 32 KB internal frame handling:
each segment's compressed byte range is chunked into per-frame headers
before the LZX bitstream begins. Two header forms:

- **Plain** (2 bytes): u16 BE compressed size. Implies an uncompressed
  size of `min(32768, bytes_remaining_in_segment)`.
- **Escape** (5 bytes): `0xFF`, u16 BE uncompressed size, u16 BE
  compressed size. Used whenever the plain form's high byte would
  collide with the `0xFF` sentinel (compressed size ≥ 0xFF00), and
  always for the last frame of a segment, even a full 32 KB one,
  confirmed unanimous across every segment of two retail x36 files (10
  segments total).

After the last frame, every retail segment carries 5 trailing zero
bytes: a null frame header, read as an end-of-stream terminator by a
decoder that loops "read header, stop on 0x00 0x00" rather than
counting output bytes against a target.

### 2.3 LZX blocks

Three block types, 3-bit tag at the start of each block: `VERBATIM`
(1), `ALIGNED` (2), `UNCOMPRESSED` (3). Block header after the tag: a
24-bit length, split as a 16-bit read then an 8-bit read, combined
`(hi << 8) | lo`. Retail data mixes VERBATIM and ALIGNED in the same
segment; ALIGNED adds a 3-bit aligned-offset tree on top of VERBATIM's
plain extra-offset bits, otherwise identical.

`UNCOMPRESSED` blocks store R0/R1/R2 as three raw little-endian u32s
immediately after the block header, aligned to a 16-bit boundary, then
raw literal bytes, no Huffman coding. Xbox 360's XMemCompress omits the
pad byte stock CAB LZX inserts after an odd-length UNCOMPRESSED block,
confirmed by checksum continuity across the block boundary in real
UI-unit data. Assuming the CAB pad convention desyncs every block after
the first odd-length stored block in a segment.

This resolves the earlier "0x8010 / 0x8000 leading frame bytes"
mystery in front-end/logo units: those bytes aren't a distinct framing
variant. They're the raw R0/content bytes of an `UNCOMPRESSED` block,
misread as a corrupt frame-size header by a decoder that only handled
VERBATIM/ALIGNED. Decoding all three block types uniformly resolves
it; the zero-fill fallback described in earlier notes is not needed
for this cause.

### 2.4 Trees

Three Huffman trees per VERBATIM/ALIGNED block (ALIGNED adds a fourth,
the 8-symbol aligned-offset tree, 3 fixed bits each, no further
encoding):

- **Main tree**: 256 literal symbols (0–255) plus match symbols, laid
  out as `256 + slot*8 + min(length-2, 7)` for each position slot the
  window size implies. A match's length occupies 3 bits inline (values
  0–6); value 7 is a sentinel meaning "read the remainder from the
  length tree."
- **Length tree**: 249 symbols, the match-length overflow when the
  main tree's inline 3 bits aren't enough. Final match length =
  `2 + primary_or_(7 + length_tree_value)`, max 257 bytes.
- **Pretree**: 20 symbols, itself always sent as 20 fixed 4-bit code
  lengths (never further compressed). Used to transmit the code
  lengths of whichever tree follows (main, length, or aligned, which
  isn't pretree-coded). Codes 0–16 are literal delta values; 17 is a
  4–19-run of zero lengths (4 extra bits); 18 is a 20–51-run (5 extra
  bits); 19 repeats the previous symbol's value for 4 or 5 positions
  with one more pretree-coded delta.

Delta encoding: each tree's code lengths persist across blocks (the
decoder never clears the length array between blocks in one segment).
A transmitted value is `(previous_length - delta) mod 17`. The RLE
forms above must also update the running per-position "previous
length" state for every position they cover, not just the positions
coded individually, or later blocks' deltas silently diverge from what
the decoder holds.

Canonical code assignment: codes assigned in order of increasing
length, and for equal length, increasing symbol index. A tree decoder
built purely from code lengths (no explicit codes transmitted) only
works if the encoder assigns codes this way.

**Degenerate trees.** A Huffman code needs a Kraft sum of exactly 1 to
be decodable (every bit pattern maps to something). The length tree is
the one exception: real retail data (checked across every block of two
full files, 90 tree builds) never has fewer than 2 active main-tree or
pretree symbols, but frequently has a completely empty length tree
(zero active symbols, whenever no match in a block needs the overflow
range), and the format explicitly allows this, distinct from exactly
one active symbol, which is still invalid. Main tree, pretree, and the
aligned-offset tree never get this exception; if either would end up
with fewer than 2 active symbols, the code space must be padded to a
valid 2-symbol tree instead.

### 2.5 Repeat-offset cache

Match encoding uses an R0/R1/R2 cache of the three most recent match
offsets (initialized to 1 at the start of a segment, not reset between
frames within that segment). Position slots 0/1/2 mean "reuse Rn" (R1
and R2 hits promote that value to R0); slot 3 and above carries an
explicit offset via a position-slot base plus extra bits. The cache is
per-segment, not per-frame: a match early in frame 2 that reuses R0
from the end of frame 1 is common and must resolve against the
inherited value, not a fresh one.

## 3. PiP content survey (423 files decoded)

Per-item archives: every weapon (`katana.lu`, `quantumrifle.lu`, ...)
and every costume piece as `<name>_top/_mid/_bottom/_acc.lu`. Costume
meshes ship with partial skeletons (12–64 bones) that bind onto the
character rig.

Bear rig is 115 bones (NB1: 117); named NPCs use 122–123. No 116-bone
rig exists in PiP; NB1's 147 orphan clips are presumably cut content.

`anim_clip` (04300000): duration still f32 at +0x10, track count at
+0x20; the track/curve encoding is reworked from NB1 (§8).

## 4. Chunk format compatibility matrix (vs NB1)

| content | type id | status |
|---|---|---|
| skeleton | 04000001 | identical, NB1 parser works unchanged |
| texture | 34200007 (was 14200007) | identical chunk format, only the container type id changed |
| mesh | 34000007 (was 04000007) | shares NB1's inner container (§7) |
| anim_clip | 04300000 | header unchanged; track table + curve encoding reworked (§8) |
| scripts | 04b00000 | plaintext Lua source, not bytecode (§6) |
| sound | 04c000xx + .cu | .cu manifests are the same plain-text format |

## 5. Rigs

Player/NPC bear: 115 bones (NB1: 117). Named NPCs: 122–123. Costume
pieces (`*_top/_mid/_bottom/_acc.lu`) carry partial skeletons (12–64
bones) that bind onto the character rig; the costume system is
data-driven through these per-garment sub-rigs.

## 6. Scripts — plaintext Lua source

PiP ships scripts as plaintext Lua source, not compiled bytecode
(unlike NB1). They live in the same animation-chunk type (04b00000)
that holds bytecode in NB1; the payload is readable source text,
comments and doc-tags intact.

Chunk layout (big-endian):

| offset | field |
|-------:|-------|
| 0x00 | u32 name hash |
| 0x04 | u32 type = 0x04b00000 |
| 0x10 | u32 path_offset |
| 0x14 | u32 path_length (embedded `z:\nb2_data\...\X.lua` path) |
| 0x18 | u32 source_offset |
| 0x1c | u32 source_length |
| 0x20 | u32 source_end |
| 0x24 | u32 trailer_length |

381 unique scripts (838 chunks including per-unit duplicates) cover
the gameplay layer: AI/scare state machines, destructibles,
interactives (jukebox, fridge, arcade, defluffication machine),
cutscene manager, faction utilities, the wardrobe/costume system, and
the engine-binding libraries. Engine functions are exposed to Lua as an
`engine.*` table (e.g. `engine.ScriptUtil_StringToHash`); string-hash
constants are inlined with a self-documenting comment, e.g.
`--[[HASH:"ST_CKDeflufficationMachine":0x7061d03b]]1885458491`.

`pip_scripts.py` slices scripts out and rebuilds the original path
tree. Injection (writing edited source back) fits an edit into its
original chunk slot, whitespace-squeezing if needed, and refuses
rather than silently relocating or mangling an edit too large to fit.

## 7. Mesh format 34000007

Shares NB1's inner container: header `{0x20, entry_count, total_size}`,
then a `34200004` sub-chunk holding the GPU vertex/index buffers with
the same trailer-described submesh layout as 04000007. The NB1
decoders work directly, once meshes are also looked for under
`type_34000007/` in addition to `mesh_buffers/`:

- rigid props (weapons, hats): `lu_convert.convert_mesh`, auto-detected
  vertex layouts (mostly stride-36, pos@0 uv@32; some 28/40/44/52).
- skinned characters: `lu_rig.extract_skinned_mesh` plus palette solve,
  exported as rigged GLB.

Batch result: 157 rigged character GLBs, 153 rigid prop OBJs, covering
the full PiP cast and complete arsenal/wardrobe. Validated by render:
weapons read as their real shapes, characters as correctly-formed
bears, costume pieces as the right garment geometry.

## 8. Animation clips 04300000

PiP reuses NB1's channel/keyframe encoding verbatim; only the outer
wrapper changed, tracks map to bones by name hash instead of index.

Clip header (big-endian):

| offset | field |
|-------:|-------|
| 0x10 | f32 duration |
| 0x1c | u32 section1_ptr (bone-name hashes) |
| 0x20 | u32 track_count |
| 0x30 | u32 section2_ptr (track table) |
| 0x34 | u32 track_count |

Section 1: `track_count` u32 bone-name hashes, one per track, resolved
to skeleton bone indices via the skeleton's sorted `{name_hash, index}`
table at +0x48. Section 2: `track_count` × `{u32 channel_list_ptr, u32
channel_count}`; ptr 0 or 0xffffffff means an untouched bone. Channels:
`channel_count` × 16 bytes, `{u32 data_ptr, u32 sub_fmt, f32 end_time,
u8 tag<<24}`, identical to NB1. Channel data: `{u32 0, u16 key_count,
u16 enc, u32 key_ptr, u32 data_size}`; encoding high byte 0x08 is
uniform 30 Hz f32 samples (absolute), 0x34 is keyed u16 times at 240/s
plus 4 f32 per key (delta from rest). Tags 0x04/05/06 are translation
xyz, 0x23/24/25 are rotation quat xyz.

`pip_anim.py` decodes and bakes these into the rigged GLBs. Validated
by render: NPC clips play correct full-body motion with clean skin
deformation. Result: roughly 170 animations per full character.

## 9. Engine API and executable

`engine.ini`: project codename nbear2, A2M/Behaviour's in-house engine,
FMOD audio, Scaleform UI (the front-end/menu units' Scaleform payload
is why their LZX segments lean on UNCOMPRESSED blocks, §2.3), a Lua VM
with its own GC thread. Retail heap 390 MB, storage 210 MB.

`default.xex` ships retail-encrypted (XEX2, encryption type 1,
compression none); decryption requires the published Xbox 360 retail
key, a step this toolkit does not perform. Once decrypted (e.g. via
xextool), the PE body is readable: kernel imports, embedded
`engine.ini`, FMOD/Scaleform strings, and 40 embedded compiled-Lua
chunks (Lua 5.1, little-endian, 4-byte `lua_Number`, non-stock trailing
flag 0x08, same header family as NB1's bytecode). 11 parsed cleanly:
`base`, `table`, `simple`, `objectcache`, `orderedset`, `priorityqueue`,
`unorderedarrayset`, `introspection`, `wrapper`, `socketscheduler`,
`timer`, the framework layer beneath the 381 data-driven gameplay
scripts (§6).

`engine.*` native API surface: 872 distinct functions harvested from
the gameplay scripts (see PIP_ENGINE_API.md). Largest subsystems by
call volume: GSLoading, ScriptUtil, HudCombatComboMeter, ModeledObject,
FearEvent, HazingScoreMgr/HazingPlayerManager (the scare/torment
scoring core), MainCameraParameters (55 setters), DamageFXConfigHelper,
the grade-tier enums (eBRONZE/SILVER/GOLD/PLATINE_GRADE).

## 10. Toolchain

`pip_dump.py` for PiP files, `lu_dump.py` for NB1 files. Both
auto-detect container format and refuse to run the wrong game's
decoder; nothing is written into the extraction input tree.

Format coverage: container, compression, textures, meshes, skeletons,
scripts, engine API, and animation are solved for PiP. Open: the
in-executable framework bytecode (§9), decompiling needs the same
header-fixup transcode as NB1, adapted for little-endian and float
numbers.
