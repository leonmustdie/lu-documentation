# Naughty Bear `.cu` Companion Files

**Verdict up front:** `.cu` files contain no model, animation, or binary
data of any kind. They are **plain ASCII text files** — streamed-audio
manifests. Each one lists the Xbox 360 XMA audio streams that the
same-named `.lu` resource unit needs available when it is loaded. You can
open any of them in Notepad.

Verified against the complete game set: **162/162 files** are pure ASCII
with CRLF (`\r\n`) line endings and a trailing newline; every single
non-empty line (18,642 of them, 3,210 unique) has the exact form:

```
streams\NAME.xma
```

`.xma` is XMA2, the Xbox 360's native compressed audio format. The
`streams\` prefix is a path inside the game's audio package; the engine
evidently registers/preloads this list when the matching `.lu` unit is
mounted, so level units list their ambience and scripted dialogue, and
character units list that character's voice bank.

## 1. Relationship to `.lu` files

The pairing is by filename: `piratebear.lu` ↔ `piratebear.cu`,
`naughtyisland.lu` ↔ `naughtyisland.cu`, etc. The `.lu` holds the unit's
meshes/textures/animations/sound-event metadata; the `.cu` is the flat
list of streamed audio the unit's sound events reference. Units with no
streamed audio simply have a tiny or absent `.cu`.

Cutscene units have one `.cu` **per dialogue locale**
(`ep1cutscene.en_us.cu`, `.de_de`, `.es_es`, `.fr_fr`, `.it_it` — 13
units × 5 locales in the set), because cutscene voice-over is localized
while gameplay barks are not.

## 2. Line conventions

Stream names are structured `PREFIX_[CODE_]DESCRIPTION[N].xma`:

| prefix | meaning | share of lines |
|--------|---------|---------------:|
| `VO_`  | voice-over / dialogue | ~78% |
| `FX_`  | sound effects | ~22% |
| `AM_`  | ambience loops (e.g. `AM_WindStorm`) | <1% |
| `MU_`  | music | <1% |

`VO_` lines carry a character/source code:

| code | speaker | code | speaker |
|------|---------|------|---------|
| `HUD` | HUD/announcer | `VAMP` | vampire bear |
| `NAR` | narrator | `DNGR` | danger bear |
| `DAV` | Daddles | `SWAT` | SWAT bears |
| `NRM2`/`NRM3` | normal bears | `PIRT` | pirate bear |
| `FRD` | Fluffy/friend bears | `EDD` | Eddison |
| `PLC01` | police | `GRG` | Giggles |
| `ASH`, `BRN`, `CHR` | named bears | `ANNOYING` | (self-explanatory) |

Localized dialogue streams additionally carry a locale suffix in the
stream name itself (`..._en_US.xma` style) inside the per-locale cutscene
manifests.

## 3. Duplicate lines are intentional

Lines repeat within a file (e.g. `piratebear.cu` lists
`VO_PIRT_WHIMPER1.xma` three times; `characters.cu` has 6,813 lines but
only 2,271 unique). The count corresponds to the number of *sound events*
in the `.lu` that reference that stream — the manifest appears to be
emitted once per referencing event by the build pipeline, not
deduplicated. For extraction purposes you can safely `sort -u` them.

## 4. Practical uses

* **Audio ripping**: union of all `.cu` files = the complete list of
  streamed audio assets in the game (3,210 streams). Pair with an XMA
  extractor pointed at the game's `streams` package.
* **Cross-referencing**: a quick way to see which characters/sounds appear
  in which level or cutscene without parsing the `.lu` at all.
* **Modding**: if you add a sound event to a unit that references a new
  stream, the stream name almost certainly has to be appended to the
  matching `.cu` for the engine to find it.

## 5. What `.cu` files are *not*

They contain no geometry, no "model directions," no transforms, no
animation curves, and no binary payload whatsoever. All of that lives in
the `.lu` containers (see `LU_FORMAT.md`): meshes are type `04000007`
chunks, textures `14200007`, and animation/skeleton data are separate
chunk types inside the same `.lu` archives.
