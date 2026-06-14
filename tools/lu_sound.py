#!/usr/bin/env python3
"""
lu_sound.py — extract and (where the layout is confirmed) decode the audio
that lives inside Naughty Bear .lu containers.

Two audio sources exist in the game; this tool handles both:

  1. EMBEDDED SFX BANK  — `sound_wave` chunks (type 0x04C00008) stored in the
     .lu image (e.g. the 665 waves in characters.lu). These are the in-memory
     sound bank. Their internal header is the one piece LU_FORMAT.md never
     fully decoded, so this tool's FIRST duty is to *characterise* them
     (group by header shape, hexdump uniques, recover FX_* names) and only
     decode shapes that are positively identified. Unidentified shapes are
     dumped raw + reported — never blindly re-wrapped into garbage WAVs.

  2. STREAMED AUDIO     — loose `streams\\*.xma` files referenced by the .cu
     manifests (VO / music / ambience). These are standard Xbox 360 XMA and
     are converted directly with ffmpeg's xma2 decoder (`--streams DIR`).

Naming: `sound_binding` chunks (type 0x04C00010) carry the plaintext FX_*
name plus an embedded {hash, 0x04C00008} reference to their wave chunk, so
waves can be given real names. `sound_event` chunks add fx_l1_* names.

Usage:
  # characterise + extract the embedded bank (run this first):
  python3 lu_sound.py bank characters.lu -o sound_out/
  python3 lu_sound.py bank lu_extracted/characters/ -o sound_out/   # extracted dir

  # convert loose streamed XMA -> WAV (needs ffmpeg):
  python3 lu_sound.py streams path/to/streams/ -o sound_out/streams_wav/

The `bank` command writes:
  sound_out/wav/<name>.wav         decoded waves (identified shapes only)
  sound_out/raw/<name>.wave.bin    raw chunk for every wave (always)
  sound_out/wave_headers.txt       per-shape hexdump report  <-- calibration
  sound_out/sound_manifest.tsv     wave hash -> name, size, shape, status

Requires naughty_lu.py on the path (imported for .lu parsing). ffmpeg is
only needed for actual decoding.
"""

import argparse
import re
import struct
import subprocess
import sys
import zlib
from collections import Counter, defaultdict
from pathlib import Path

import naughty_lu as nl

WAVE_TYPE = 0x04C00008
BINDING_TYPE = 0x04C00010
EVENT_TYPE = 0x04C00000

NAME_RE = re.compile(rb"[A-Za-z0-9_]{3,64}")
# FX_/fx_ sound names and the VO/AM/MU families seen in CU_FORMAT.md
SOUND_NAME_HINT = re.compile(rb"(?:FX|fx|VO|AM|MU)_[A-Za-z0-9_]{2,}")

# trailing locale tag, e.g. ..._en_us / ..._de_de / ..._fr_fr, optionally
# followed by a take/variant index like _0, _1
LOCALE_RE = re.compile(r"_([a-z]{2})_([a-z]{2})(?:_\d+)?$", re.IGNORECASE)


def classify(stem):
    """Return (category, language) for a sound's filename stem.

    language is an UPPER_lower locale like 'en_US', or 'common' when the name
    carries no locale tag (SFX/music aren't localised). category groups by the
    VO/FX/MU/AM prefix, with VO split into Narrator / HUD / per-character.
    """
    s = stem
    m = LOCALE_RE.search(s)
    if m:
        lang = f"{m.group(1).lower()}_{m.group(2).upper()}"
        s = s[:m.start()]            # strip locale before reading category
    else:
        lang = "common"

    toks = s.lower().split("_")
    head = toks[0] if toks else ""
    if head == "vo":
        sub = toks[1] if len(toks) > 1 else ""
        if sub == "nar":
            cat = "VO_Narrator"
        elif sub == "hud":
            cat = "VO_HUD"
        elif sub:
            cat = f"VO_{sub.upper()}"      # VO_DAV, VO_SWAT, ...
        else:
            cat = "VO"
    elif head == "fx":
        cat = "SFX"
    elif head == "mu":
        cat = "Music"
    elif head == "am":
        cat = "Ambience"
    else:
        cat = "Other"
    return cat, lang


def dest_subdir(stem, split):
    """Map a filename stem to an output subpath per the --split mode."""
    cat, lang = classify(stem)
    if split == "lang":
        return Path(lang)
    if split == "category":
        return Path(cat)
    if split == "both":
        return Path(cat) / lang
    return Path(".")                       # 'none' -> flat


# ---------------------------------------------------------------------------
# loading: accept .lu files OR a directory of already-extracted *.bin chunks
# ---------------------------------------------------------------------------

def load_chunks(inputs):
    """Yield (kind, hash, data) for every sound-related chunk found.

    `inputs` may mix .lu files, directories containing .lu files, and
    directories of extracted chunks (naughty_lu.py extract output, where
    sound chunks live under sound_wave/ , sound_binding/ , sound_event/).
    """
    lu_files, extracted = [], []
    for p in map(Path, inputs):
        if p.is_dir():
            lus = sorted(p.rglob("*.lu"))
            if lus:
                lu_files.extend(lus)
            else:
                extracted.append(p)
        elif p.suffix.lower() == ".lu":
            lu_files.append(p)
        else:
            extracted.append(p.parent)

    chunks = []  # (type_int, hash_or_None, data, label)

    for lu_path in lu_files:
        lu = nl.LuFile(lu_path)
        for r in lu.records:
            if r.type in (WAVE_TYPE, BINDING_TYPE, EVENT_TYPE):
                data = lu.chunk(r)
                if data is not None:
                    chunks.append((r.type, r.hash, data, lu_path.stem))

    # extracted dirs: type is encoded by the folder name; hash is the hex in
    # the filename tail (NNNN_<name-or-hex>.bin) when the name wasn't resolved
    type_by_dir = {"sound_wave": WAVE_TYPE, "sound_binding": BINDING_TYPE,
                   "sound_event": EVENT_TYPE}
    for d in extracted:
        for sub, tcode in type_by_dir.items():
            for f in sorted(d.rglob(f"{sub}/*.bin")):
                tail = f.stem.split("_", 1)[-1]
                try:
                    h = int(tail, 16) if len(tail) == 8 else zlib.crc32(
                        tail.lower().encode()) & 0xFFFFFFFF
                except ValueError:
                    h = zlib.crc32(tail.lower().encode()) & 0xFFFFFFFF
                chunks.append((tcode, h, f.read_bytes(), f.stem))
    return chunks


# ---------------------------------------------------------------------------
# name resolution from binding / event chunks
# ---------------------------------------------------------------------------

def resolve_names(chunks):
    """Return {wave_hash: name} mined from sound_binding (and event) chunks.

    A binding chunk holds an FX_* name in plaintext and an embedded
    {wave_hash, 0x04C00008} u32 pair. We pair the most prominent sound-style
    name in the chunk with each wave hash it references."""
    names = {}
    for tcode, h, data, label in chunks:
        if tcode not in (BINDING_TYPE, EVENT_TYPE):
            continue
        # candidate names: prefer FX_/VO_/... hits, else any ascii token
        hinted = [m.group().decode() for m in SOUND_NAME_HINT.finditer(data)]
        generic = [m.group().decode() for m in NAME_RE.finditer(data)]
        name = (hinted or generic or [None])[0]
        if not name:
            continue
        # wave references: {hash, 0x04C00008} big-endian pairs
        refs = []
        for o in range(0, len(data) - 8, 4):
            rh, rt = struct.unpack_from(">2I", data, o)
            if rt == WAVE_TYPE and rh not in (0, 0xFFFFFFFF):
                refs.append(rh)
        # the binding's own hash often equals its wave's; map both ways
        for rh in refs or [h]:
            names.setdefault(rh, name)
    return names


# ---------------------------------------------------------------------------
# wave header characterisation  (the calibration step)
# ---------------------------------------------------------------------------

def wave_signature(data):
    """A short, stable fingerprint of a wave chunk's header so identical
    layouts group together. Uses the first 4cc-ish word and a coarse sketch
    of the first 32 bytes (which bytes are zero / printable / other)."""
    head = data[:32]
    sketch = "".join("0" if b == 0 else
                     ("a" if 0x20 <= b < 0x7f else "x") for b in head)
    tag = data[:4].hex()
    return f"{tag}:{sketch}"


# Decoders are added here ONLY once a shape is positively identified against
# real data. Each entry maps a predicate(data)->bool to a function
# (data)-> (wav_bytes | None). Empty by design: nothing is decoded until the
# header is confirmed, so the tool can never emit shattered audio.
# CONFIRMED against characters.lu (665/665 chunks): the sound_wave payload is
# a headerless, big-endian XMA2 bitstream packed in 2048-byte packets. Each
# packet begins with a standard XMA2 packet header; for the first packet of a
# chunk that header's bytes [1:4] are 00 01 00 (frame-offset 0, metadata 1,
# skip 0). The chunk carries NO sample rate or channel count — those are
# external. The bank is single-stream MONO; rate defaults to 44100 (the
# dominant value in this game) and is overridable with --rate.
#
# Caveats (see README/notes): single-packet SFX can decode to digital silence
# (ffmpeg discards the lone packet as a priming frame) and multi-stream/stereo
# music (packet header ending fc 01) truncates under a mono wrap. The loose
# `streams\\*.xma` files don't have these issues — they carry full headers.

XMA2_BLOCK = 2048


def build_riff_xma2(data, channels, rate):
    """Wrap a raw XMA2 packet stream in a RIFF (fmt tag 0x0166) container that
    ffmpeg's xma2 decoder accepts."""
    bcount = len(data) // XMA2_BLOCK
    samples = sum((struct.unpack_from(">I", data, o)[0] >> 26) * 512
                  for o in range(0, len(data), XMA2_BLOCK)) or bcount * 512
    fmt = struct.pack("<HHIIHHH", 0x0166, channels, rate,
                      rate * channels * 2, XMA2_BLOCK, 16, 34)
    ext = struct.pack("<HIIIIIIIBBH", 1, 0, samples, XMA2_BLOCK, 0, samples,
                      0, 0, 0, 4, bcount)
    fmtc = fmt + ext
    body = (b"WAVE" + b"fmt " + struct.pack("<I", len(fmtc)) + fmtc
            + b"data" + struct.pack("<I", len(data)) + data)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _is_xma2(data):
    return (len(data) >= XMA2_BLOCK and len(data) % XMA2_BLOCK == 0
            and data[1:4] == b"\x00\x01\x00")


def _ffmpeg_wrap(data, channels, rate):
    """Decode one channel hypothesis. Returns (wav_bytes, pcm_len, has_signal)
    or (None, 0, False)."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        xi = Path(td) / "i.xma"
        wo = Path(td) / "o.wav"
        xi.write_bytes(build_riff_xma2(data, channels, rate))
        r = subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-i", str(xi), str(wo)], capture_output=True, text=True)
        if r.returncode != 0 or not wo.exists() or wo.stat().st_size <= 44:
            return None, 0, False
        wav = wo.read_bytes()
        di = wav.find(b"data")
        if di >= 0 and di + 8 <= len(wav):
            dsz = struct.unpack_from("<I", wav, di + 4)[0]
            pcm = wav[di + 8: di + 8 + dsz]
        else:
            pcm = wav[44:]
        return wav, len(pcm), any(pcm)


def decode_xma2_auto(data, rate=44100, channels=(1, 2)):
    """Decode a raw XMA2 stream, auto-detecting channel count.

    The wrong channel count makes ffmpeg either desync and truncate early or
    emit all-zero PCM, so among hypotheses that produce real signal we take
    the one yielding the most audio. Returns (wav|None, channels|None,
    silent_flag). silent_flag is True only when EVERY hypothesis decoded but
    none carried signal (the single-packet priming case)."""
    best = None            # (pcm_len, wav, ch)
    any_decoded = False
    for ch in channels:
        wav, pcm_len, signal = _ffmpeg_wrap(data, ch, rate)
        if wav is not None:
            any_decoded = True
        if wav is not None and signal:
            if best is None or pcm_len > best[0]:
                best = (pcm_len, wav, ch)
    if best:
        return best[1], best[2], False
    return None, None, any_decoded


# (name, predicate, decode_fn) — decode_fn(data, rate) -> (wav, ch, silent)
WAVE_DECODERS = [
    ("xma2", _is_xma2, decode_xma2_auto),
]


def try_decode(data, rate=44100):
    for name, pred, fn in WAVE_DECODERS:
        try:
            if pred(data):
                wav, ch, silent = fn(data, rate)
                if wav:
                    return f"{name}_{ch}ch", wav, silent
                if silent:
                    return f"{name}_silent", None, True
        except Exception:
            continue
    return None, None, False


# ---------------------------------------------------------------------------
# bank command
# ---------------------------------------------------------------------------

def cmd_bank(args):
    chunks = load_chunks(args.inputs)
    waves = [(h, d, lbl) for (t, h, d, lbl) in chunks if t == WAVE_TYPE]
    if not waves:
        print("no sound_wave (0x04C00008) chunks found in inputs.",
              file=sys.stderr)
        return
    names = resolve_names(chunks)

    out = Path(args.out)
    (out / "raw").mkdir(parents=True, exist_ok=True)
    (out / "wav").mkdir(parents=True, exist_ok=True)

    rate = getattr(args, "rate", 44100)
    shapes = defaultdict(list)      # signature -> [(hash, len)]
    shape_example = {}              # signature -> example header bytes
    manifest = []
    n_decoded = n_raw = n_silent = 0

    used = Counter()
    for h, data, lbl in waves:
        sig = wave_signature(data)
        shapes[sig].append((h, len(data)))
        shape_example.setdefault(sig, data[:64])

        name = names.get(h) or f"wave_{h:08x}"
        # de-dupe names
        used[name] += 1
        if used[name] > 1:
            name = f"{name}_{used[name]:03d}"
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name)

        (out / "raw" / f"{safe}.wave.bin").write_bytes(data)

        dec_name, wav, silent = try_decode(data, rate)
        if wav and not silent:
            (out / "wav" / f"{safe}.wav").write_bytes(wav)
            status = f"decoded:{dec_name}"
            n_decoded += 1
        elif silent:
            # decoded but no signal under any channel hypothesis (single-packet
            # priming case). Keep raw, flag it — never ship empty WAVs.
            status = "silent"
            n_silent += 1
        else:
            status = "raw-only"
            n_raw += 1
        manifest.append((h, safe, len(data), sig, status))

    # calibration report: one hexdump per unique header shape
    with open(out / "wave_headers.txt", "w") as f:
        f.write(f"# {len(waves)} wave chunks, {len(shapes)} unique header "
                f"shapes\n# shape = first4cc:zero/ascii/other-sketch of "
                f"bytes 0..31\n\n")
        for sig, members in sorted(shapes.items(),
                                   key=lambda kv: -len(kv[1])):
            ex = shape_example[sig]
            f.write(f"## shape {sig}  ({len(members)} chunks, "
                    f"sizes {min(m[1] for m in members)}.."
                    f"{max(m[1] for m in members)})\n")
            for i in range(0, len(ex), 16):
                row = ex[i:i + 16]
                hexs = " ".join(f"{b:02x}" for b in row)
                asci = "".join(chr(b) if 0x20 <= b < 0x7f else "." for b in row)
                f.write(f"  {i:04x}  {hexs:<47}  {asci}\n")
            f.write("\n")

    with open(out / "sound_manifest.tsv", "w") as f:
        f.write("wave_hash\tname\tsize\theader_shape\tstatus\n")
        for h, name, sz, sig, status in sorted(manifest):
            f.write(f"{h:08x}\t{name}\t{sz}\t{sig}\t{status}\n")

    named = sum(1 for m in manifest if not m[1].startswith("wave_"))
    print(f"{len(waves)} waves: {named} named, {n_decoded} decoded -> wav, "
          f"{n_silent} silent (kept raw), {n_raw} raw-only "
          f"({len(shapes)} header shapes).")
    print(f"  raw chunks   -> {out/'raw'}/")
    print(f"  decoded wav  -> {out/'wav'}/  (rate {rate} Hz, mono/stereo auto)")
    print(f"  manifest     -> {out/'sound_manifest.tsv'}")
    if n_silent:
        print(f"  note: {n_silent} are single-packet (2048B) one-shots that "
              f"ffmpeg's XMA2 decoder renders silent; flagged, kept raw.")


# ---------------------------------------------------------------------------
# fsb command  (FMOD FSB4 sound bank -> wav)  [Panic in Paradise]
# ---------------------------------------------------------------------------
# FSB4 layout: 0x30-byte header, then per-sample headers, then a concatenated
# data block. Base sample header (name fixed at 30 bytes) carries length,
# compressed size, mode (codec), rate, channels and loop points; samples in
# these banks are FSOUND_XMA (mode bit 0x01000000) = the same raw XMA2 packet
# stream handled above, but here the rate/channels are known, not guessed.

FSOUND_XMA = 0x01000000


def parse_fsb4(d):
    if d[:4] != b"FSB4":
        raise ValueError(f"not FSB4 (magic {d[:4]!r})")
    num, shdr_size, data_size = struct.unpack_from("<3I", d, 4)
    data_start = 0x30 + shdr_size
    out = []
    pos = 0x30
    off = data_start
    for _ in range(num):
        size = struct.unpack_from("<H", d, pos)[0]
        name = d[pos + 2:pos + 32].split(b"\x00")[0].decode("latin1")
        (n_samp, n_bytes, loop_s, loop_e, mode, freq) = \
            struct.unpack_from("<6I", d, pos + 0x20)
        nch = struct.unpack_from("<H", d, pos + 0x3E)[0] or 1
        out.append(dict(name=name, offset=off, size=n_bytes, samples=n_samp,
                        rate=freq, channels=nch, mode=mode,
                        loop=(loop_s, loop_e), is_xma=bool(mode & FSOUND_XMA)))
        pos += size
        off += n_bytes
    return out


def cmd_fsb(args):
    if subprocess.run(["ffmpeg", "-version"],
                      capture_output=True).returncode != 0:
        print("ffmpeg not found on PATH.", file=sys.stderr)
        return
    inputs = []
    for p in map(Path, args.inputs):
        inputs.extend(sorted(p.rglob("*.fsb")) if p.is_dir() else [p])
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    manifest = []
    tot_ok = tot_silent = tot_skip = 0
    for fsb in inputs:
        d = fsb.read_bytes()
        try:
            samples = parse_fsb4(d)
        except ValueError as e:
            print(f"  ! {fsb.name}: {e}", file=sys.stderr)
            continue
        bank = out / fsb.stem
        bank.mkdir(parents=True, exist_ok=True)
        ok = silent = skip = 0
        used = Counter()
        for s in samples:
            chunk = d[s["offset"]:s["offset"] + s["size"]]
            if not s["is_xma"] or not _is_xma2(chunk):
                skip += 1
                manifest.append((fsb.stem, s["name"], s["size"], s["rate"],
                                 s["channels"], "skip:not-xma2"))
                continue
            # known channel count first, the other as fallback
            order = (s["channels"], 1 if s["channels"] == 2 else 2)
            wav, ch, was_silent = decode_xma2_auto(chunk, s["rate"], order)
            safe = re.sub(r"[^A-Za-z0-9_.-]", "_", s["name"])
            used[safe] += 1
            if used[safe] > 1:
                safe = f"{safe}_{used[safe]:02d}"
            if wav:
                (bank / f"{safe}.wav").write_bytes(wav)
                ok += 1
                st = f"decoded:{ch}ch@{s['rate']}"
            elif was_silent:
                silent += 1
                st = "silent"
            else:
                skip += 1
                st = "fail"
            manifest.append((fsb.stem, s["name"], s["size"], s["rate"],
                             s["channels"], st))
        tot_ok += ok
        tot_silent += silent
        tot_skip += skip
        print(f"  {fsb.name}: {len(samples)} samples -> {ok} wav, "
              f"{silent} silent, {skip} skipped  ({bank}/)")

    with open(out / "fsb_manifest.tsv", "w") as f:
        f.write("bank\tname\tbytes\trate\tchannels\tstatus\n")
        for row in manifest:
            f.write("\t".join(str(x) for x in row) + "\n")
    print(f"fsb total: {tot_ok} decoded, {tot_silent} silent, "
          f"{tot_skip} skipped -> {out}/  (manifest: fsb_manifest.tsv)")


# ---------------------------------------------------------------------------
# streams command  (loose .xma -> wav)
# ---------------------------------------------------------------------------

def cmd_streams(args):
    if subprocess.run(["ffmpeg", "-version"],
                      capture_output=True).returncode != 0:
        print("ffmpeg not found on PATH.", file=sys.stderr)
        return
    src = Path(args.inputs[0])
    xmas = sorted(src.rglob("*.xma"))
    if not xmas:
        print(f"no .xma files under {src}", file=sys.stderr)
        return
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rate = getattr(args, "rate", 44100)
    split = getattr(args, "split", "none")

    ok = fail = silent = 0
    ch_counts = Counter()
    folder_counts = Counter()

    def dest_for(stem):
        sub = dest_subdir(stem, split)
        d = out / sub
        d.mkdir(parents=True, exist_ok=True)
        folder_counts[str(sub)] += 1
        return d / (stem + ".wav")

    for x in xmas:
        data = x.read_bytes()
        dest = dest_for(x.stem)

        # 1) genuine RIFF/container XMA: let ffmpeg read it directly.
        if data[:4] in (b"RIFF", b"RIFX") or not _is_xma2(data):
            r = subprocess.run(
                ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                 "-i", str(x), str(dest)], capture_output=True, text=True)
            if r.returncode == 0 and dest.exists() and dest.stat().st_size > 44:
                ok += 1
                continue
        # 2) raw XMA2 packet stream (these en_US VO files): wrap + auto-detect.
        if _is_xma2(data):
            wav, ch, was_silent = decode_xma2_auto(data, rate)
            if wav:
                dest.write_bytes(wav)
                ok += 1
                ch_counts[ch] += 1
                continue
            if was_silent:
                silent += 1
                folder_counts[str(dest_subdir(x.stem, split))] -= 1
                if args.verbose:
                    print(f"  · {x.name}: decoded to silence (skipped)",
                          file=sys.stderr)
                continue
        fail += 1
        folder_counts[str(dest_subdir(x.stem, split))] -= 1
        if args.verbose:
            print(f"  ! {x.name}: could not decode", file=sys.stderr)

    chsum = ", ".join(f"{n} x {c}ch" for c, n in sorted(ch_counts.items()))
    print(f"streams: {ok} converted ({chsum}), {silent} silent, "
          f"{fail} failed -> {out}/  (rate {rate} Hz)")
    if split != "none":
        print("  by folder:")
        for folder, n in sorted(folder_counts.items()):
            if n > 0:
                print(f"    {n:5d}  {folder}/")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[1],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("bank", help="characterise + extract embedded "
                                     "sound_wave chunks")
    b.add_argument("inputs", nargs="+", help=".lu files or extracted dirs")
    b.add_argument("-o", "--out", default="sound_out")
    b.add_argument("--rate", type=int, default=44100,
                   help="sample rate for the (headerless) mono XMA2 waves "
                        "(default 44100; affects pitch only)")
    b.set_defaults(func=cmd_bank)

    fb = sub.add_parser("fsb", help="extract+decode FMOD FSB4 sound banks "
                                    "(Panic in Paradise)")
    fb.add_argument("inputs", nargs="+", help=".fsb files or dirs")
    fb.add_argument("-o", "--out", default="fsb_out")
    fb.set_defaults(func=cmd_fsb)

    s = sub.add_parser("streams", help="convert loose streamed .xma -> wav")
    s.add_argument("inputs", nargs=1, help="directory of .xma files")
    s.add_argument("-o", "--out", default="streams_wav")
    s.add_argument("--split", choices=["none", "lang", "category", "both"],
                   default="none",
                   help="organise output: lang (en_US/, de_DE/...), "
                        "category (VO_Narrator/, SFX/, Music/...), or both "
                        "(VO_Narrator/en_US/...). default none (flat)")
    s.add_argument("--rate", type=int, default=44100,
                   help="sample rate for raw (headerless) XMA2 (default 44100)")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(func=cmd_streams)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
