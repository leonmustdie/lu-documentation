#!/usr/bin/env python3
"""
naughty_lu.py — extractor for Naughty Bear (Gold Edition, Xbox 360) .lu
resource containers ("A2M engine loadable units").

The .lu format is NOT Lua bytecode. It is a big-endian resource container:
a self-describing header, a resource record table, and a data image that is
either stored raw or compressed as an XMemCompress LZX stream (X360 XCompress).
Full format documentation in the accompanying LU_FORMAT.md.

Commands:
  info        print header / record table details
  decompress  write the raw (decompressed) data image to a file
  extract     extract every resource chunk to <out>/<file-stem>/<type>/...

Extraction extras:
  * Resource hashes are CRC32 of the lower-cased resource name. The tool
    harvests strings embedded in chunk data (FX names, object names, ...)
    and resolves hashes to names automatically. Extra candidate names can
    be supplied with --names wordlist.txt (one name per line).
  * Records with offset == 0xFFFFFFFF are external references resolved by
    hash from a dependency .lu. Pass the dependency files on the command
    line and the tool resolves them across files.

No third-party dependencies; the LZX decoder (ported from libmspack's
lzxd.c) is embedded below.
"""

import argparse
import re
import struct
import sys
import zlib
from collections import Counter, defaultdict
from pathlib import Path

# ============================================================================
# LZX decompressor (port of libmspack lzxd.c, MSB bit order, no LZX-DELTA)
# ============================================================================

LZX_MIN_MATCH = 2
LZX_NUM_CHARS = 256
LZX_BLOCK_VERBATIM = 1
LZX_BLOCK_ALIGNED = 2
LZX_BLOCK_UNCOMPRESSED = 3
LZX_PRETREE_NUM_ELEMENTS = 20
LZX_ALIGNED_NUM_ELEMENTS = 8
LZX_NUM_PRIMARY_LENGTHS = 7
LZX_NUM_SECONDARY_LENGTHS = 249
LZX_FRAME_SIZE = 32768
HUFF_MAXBITS = 16

PRETREE_TABLEBITS, PRETREE_MAXSYMBOLS = 6, LZX_PRETREE_NUM_ELEMENTS
MAINTREE_TABLEBITS, MAINTREE_MAXSYMBOLS = 12, LZX_NUM_CHARS + 290 * 8
LENGTH_TABLEBITS, LENGTH_MAXSYMBOLS = 12, LZX_NUM_SECONDARY_LENGTHS + 1
ALIGNED_TABLEBITS, ALIGNED_MAXSYMBOLS = 7, LZX_ALIGNED_NUM_ELEMENTS

POSITION_SLOTS = {15: 30, 16: 32, 17: 34, 18: 36, 19: 38, 20: 42, 21: 50,
                  22: 66, 23: 98, 24: 162, 25: 290}
EXTRA_BITS = [0, 0, 0, 0] + [i // 2 - 1 for i in range(4, 36)]
POSITION_BASE = []
_b = 0
for _i in range(290):
    POSITION_BASE.append(_b)
    _b += 1 << (17 if _i >= 36 else EXTRA_BITS[_i])


class LZXError(Exception):
    pass


def _make_decode_table(nsyms, nbits, length):
    table = [0] * ((1 << nbits) + (nsyms << 1))
    pos = 0
    table_mask = 1 << nbits
    bit_mask = table_mask >> 1
    for bit_num in range(1, nbits + 1):
        for sym in range(nsyms):
            if length[sym] != bit_num:
                continue
            leaf = pos
            pos += bit_mask
            if pos > table_mask:
                return None
            table[leaf:leaf + bit_mask] = [sym] * bit_mask
        bit_mask >>= 1
    if pos == table_mask:
        return table
    for sym in range(pos, table_mask):
        table[sym] = 0xFFFF
    next_symbol = nsyms if (table_mask >> 1) < nsyms else (table_mask >> 1)
    pos <<= 16
    table_mask <<= 16
    bit_mask = 1 << 15
    for bit_num in range(nbits + 1, HUFF_MAXBITS + 1):
        for sym in range(nsyms):
            if length[sym] != bit_num:
                continue
            if pos >= table_mask:
                return None
            leaf = pos >> 16
            for fill in range(bit_num - nbits):
                if table[leaf] == 0xFFFF:
                    table[next_symbol << 1] = 0xFFFF
                    table[(next_symbol << 1) + 1] = 0xFFFF
                    table[leaf] = next_symbol
                    next_symbol += 1
                leaf = table[leaf] << 1
                if (pos >> (15 - fill)) & 1:
                    leaf += 1
            table[leaf] = sym
            pos += bit_mask
        bit_mask >>= 1
    return table if pos == table_mask else None


class LZXDecoder:
    """Decompresses one raw LZX bitstream of known output length."""

    def __init__(self, window_bits):
        if window_bits not in POSITION_SLOTS:
            raise LZXError(f"unsupported LZX window bits {window_bits}")
        self.window_bits = window_bits
        self.window_size = 1 << window_bits
        self.num_offsets = POSITION_SLOTS[window_bits] << 3

    # bitstream helpers (16-bit LE words injected MSB-first into 32-bit buffer)
    def _ensure(self, n):
        while self._bitsleft < n:
            d, p = self._data, self._pos
            if p + 1 < len(d):
                w = d[p] | (d[p + 1] << 8)
                self._pos = p + 2
            elif p < len(d):
                w = d[p]
                self._pos = p + 1
            else:
                w = 0  # zero-pad at EOF (stream is frame-exact)
            self._bitbuf |= w << (16 - self._bitsleft)
            self._bitsleft += 16

    def _readbits(self, n):
        self._ensure(n)
        v = self._bitbuf >> (32 - n)
        self._bitbuf = (self._bitbuf << n) & 0xFFFFFFFF
        self._bitsleft -= n
        return v

    def _readhuff(self, table, lens, tablebits, maxsyms):
        self._ensure(HUFF_MAXBITS)
        sym = table[self._bitbuf >> (32 - tablebits)]
        if sym >= maxsyms:
            mask = 1 << (32 - tablebits)
            while True:
                mask >>= 1
                if mask == 0:
                    raise LZXError("huffman traverse overflow")
                sym = table[(sym << 1) | (1 if self._bitbuf & mask else 0)]
                if sym < maxsyms:
                    break
        n = lens[sym]
        self._bitbuf = (self._bitbuf << n) & 0xFFFFFFFF
        self._bitsleft -= n
        return sym

    def _read_lens(self, lens, first, last):
        pre_len = [self._readbits(4) for _ in range(LZX_PRETREE_NUM_ELEMENTS)]
        pre_tbl = _make_decode_table(PRETREE_MAXSYMBOLS, PRETREE_TABLEBITS, pre_len)
        if pre_tbl is None:
            raise LZXError("bad pretree")
        x = first
        while x < last:
            z = self._readhuff(pre_tbl, pre_len, PRETREE_TABLEBITS, PRETREE_MAXSYMBOLS)
            if z == 17:
                for _ in range(self._readbits(4) + 4):
                    lens[x] = 0
                    x += 1
            elif z == 18:
                for _ in range(self._readbits(5) + 20):
                    lens[x] = 0
                    x += 1
            elif z == 19:
                y = self._readbits(1) + 4
                z = self._readhuff(pre_tbl, pre_len, PRETREE_TABLEBITS,
                                   PRETREE_MAXSYMBOLS)
                z = lens[x] - z
                if z < 0:
                    z += 17
                for _ in range(y):
                    lens[x] = z
                    x += 1
            else:
                z = lens[x] - z
                if z < 0:
                    z += 17
                lens[x] = z
                x += 1

    def decompress(self, data, out_len):
        self._data = data
        self._pos = 0
        self._bitbuf = 0
        self._bitsleft = 0
        window = bytearray(self.window_size)
        wsize = self.window_size
        window_posn = frame_posn = frame = offset = 0
        R0 = R1 = R2 = 1
        header_read = False
        intel_filesize = 0
        intel_started = False
        block_type = block_length = block_remaining = 0
        MAINTREE_len = [0] * (MAINTREE_MAXSYMBOLS + 64)
        LENGTH_len = [0] * (LENGTH_MAXSYMBOLS + 64)
        LENGTH_empty = False
        mt_tbl = lt_tbl = al_tbl = al_len = None
        out = bytearray()

        end_frame = out_len // LZX_FRAME_SIZE + 1
        while frame < end_frame:
            if not header_read:
                if self._readbits(1):
                    i = self._readbits(16)
                    j = self._readbits(16)
                    intel_filesize = (i << 16) | j
                header_read = True

            frame_size = LZX_FRAME_SIZE
            if out_len - offset < frame_size:
                frame_size = out_len - offset

            bytes_todo = frame_posn + frame_size - window_posn
            while bytes_todo > 0:
                if block_remaining == 0:
                    if block_type == LZX_BLOCK_UNCOMPRESSED and (block_length & 1):
                        self._pos += 1
                    block_type = self._readbits(3)
                    i = self._readbits(16)
                    j = self._readbits(8)
                    block_remaining = block_length = (i << 8) | j

                    if block_type == LZX_BLOCK_ALIGNED:
                        al_len = [self._readbits(3) for _ in range(8)]
                        al_tbl = _make_decode_table(ALIGNED_MAXSYMBOLS,
                                                    ALIGNED_TABLEBITS, al_len)
                        if al_tbl is None:
                            raise LZXError("bad aligned tree")

                    if block_type in (LZX_BLOCK_ALIGNED, LZX_BLOCK_VERBATIM):
                        self._read_lens(MAINTREE_len, 0, 256)
                        self._read_lens(MAINTREE_len, 256,
                                        LZX_NUM_CHARS + self.num_offsets)
                        mt_tbl = _make_decode_table(MAINTREE_MAXSYMBOLS,
                                                    MAINTREE_TABLEBITS, MAINTREE_len)
                        if mt_tbl is None:
                            raise LZXError("bad maintree")
                        if MAINTREE_len[0xE8] != 0:
                            intel_started = True
                        self._read_lens(LENGTH_len, 0, LZX_NUM_SECONDARY_LENGTHS)
                        lt_tbl = _make_decode_table(LENGTH_MAXSYMBOLS,
                                                    LENGTH_TABLEBITS, LENGTH_len)
                        if lt_tbl is None:
                            if any(LENGTH_len[:LZX_NUM_SECONDARY_LENGTHS]):
                                raise LZXError("bad length tree")
                            LENGTH_empty = True
                        else:
                            LENGTH_empty = False
                    elif block_type == LZX_BLOCK_UNCOMPRESSED:
                        intel_started = True
                        if self._bitsleft == 0:
                            self._ensure(16)
                        self._bitsleft = 0
                        self._bitbuf = 0
                        d, p = self._data, self._pos
                        if p + 12 > len(d):
                            raise LZXError("EOF in uncompressed block header")
                        R0 = int.from_bytes(d[p:p + 4], "little")
                        R1 = int.from_bytes(d[p + 4:p + 8], "little")
                        R2 = int.from_bytes(d[p + 8:p + 12], "little")
                        self._pos = p + 12
                    else:
                        raise LZXError(f"bad block type {block_type}")

                this_run = min(block_remaining, bytes_todo)
                bytes_todo -= this_run
                block_remaining -= this_run

                if block_type in (LZX_BLOCK_ALIGNED, LZX_BLOCK_VERBATIM):
                    aligned = block_type == LZX_BLOCK_ALIGNED
                    while this_run > 0:
                        sym = self._readhuff(mt_tbl, MAINTREE_len,
                                             MAINTREE_TABLEBITS, MAINTREE_MAXSYMBOLS)
                        if sym < LZX_NUM_CHARS:
                            window[window_posn] = sym
                            window_posn += 1
                            this_run -= 1
                            continue
                        sym -= LZX_NUM_CHARS
                        match_length = sym & LZX_NUM_PRIMARY_LENGTHS
                        if match_length == LZX_NUM_PRIMARY_LENGTHS:
                            if LENGTH_empty:
                                raise LZXError("LENGTH symbol needed but tree empty")
                            match_length += self._readhuff(
                                lt_tbl, LENGTH_len, LENGTH_TABLEBITS, LENGTH_MAXSYMBOLS)
                        match_length += LZX_MIN_MATCH
                        slot = sym >> 3
                        if slot == 0:
                            match_offset = R0
                        elif slot == 1:
                            match_offset = R1
                            R1 = R0
                            R0 = match_offset
                        elif slot == 2:
                            match_offset = R2
                            R2 = R0
                            R0 = match_offset
                        else:
                            extra = 17 if slot >= 36 else EXTRA_BITS[slot]
                            match_offset = POSITION_BASE[slot] - 2
                            if extra >= 3 and aligned:
                                if extra > 3:
                                    match_offset += self._readbits(extra - 3) << 3
                                match_offset += self._readhuff(
                                    al_tbl, al_len, ALIGNED_TABLEBITS, ALIGNED_MAXSYMBOLS)
                            elif extra:
                                match_offset += self._readbits(extra)
                            R2 = R1
                            R1 = R0
                            R0 = match_offset
                        if window_posn + match_length > wsize:
                            raise LZXError("match ran over window wrap")
                        if match_offset > window_posn:
                            j = match_offset - window_posn
                            if j > wsize:
                                raise LZXError("match offset beyond window")
                            src = wsize - j
                            i = match_length
                            if j < i:
                                i -= j
                                while j > 0:
                                    window[window_posn] = window[src]
                                    window_posn += 1
                                    src += 1
                                    j -= 1
                                src = 0
                            while i > 0:
                                window[window_posn] = window[src]
                                window_posn += 1
                                src += 1
                                i -= 1
                        else:
                            src = window_posn - match_offset
                            if match_offset >= match_length:
                                window[window_posn:window_posn + match_length] = \
                                    window[src:src + match_length]
                                window_posn += match_length
                            else:
                                for _ in range(match_length):
                                    window[window_posn] = window[src]
                                    window_posn += 1
                                    src += 1
                        this_run -= match_length
                else:  # uncompressed
                    d, p = self._data, self._pos
                    if p + this_run > len(d):
                        raise LZXError("EOF in uncompressed block")
                    window[window_posn:window_posn + this_run] = d[p:p + this_run]
                    self._pos = p + this_run
                    window_posn += this_run
                    this_run = 0

                if this_run < 0:
                    if -this_run > block_remaining:
                        raise LZXError("overrun past end of block")
                    block_remaining -= -this_run

            if window_posn - frame_posn != frame_size:
                raise LZXError("decode beyond output frame limits")

            # re-align bitstream to a 16-bit boundary
            if self._bitsleft > 0:
                self._ensure(16)
            if self._bitsleft & 15:
                n = self._bitsleft & 15
                self._bitbuf = (self._bitbuf << n) & 0xFFFFFFFF
                self._bitsleft -= n

            frame_data = window[frame_posn:frame_posn + frame_size]
            if intel_started and intel_filesize and frame < 32768 and frame_size > 10:
                frame_data = bytearray(frame_data)
                curpos = offset
                dpos, dend = 0, frame_size - 10
                while dpos < dend:
                    if frame_data[dpos] != 0xE8:
                        dpos += 1
                        curpos += 1
                        continue
                    dpos += 1
                    a = int.from_bytes(frame_data[dpos:dpos + 4], "little", signed=True)
                    if -curpos <= a < intel_filesize:
                        rel = a - curpos if a >= 0 else a + intel_filesize
                        frame_data[dpos:dpos + 4] = (rel & 0xFFFFFFFF).to_bytes(4, "little")
                    dpos += 4
                    curpos += 5
            out += frame_data
            offset += frame_size
            frame_posn += frame_size
            frame += 1
            if window_posn == wsize:
                window_posn = 0
            if frame_posn == wsize:
                frame_posn = 0
        return bytes(out[:out_len])


# ============================================================================
# XMemCompress stream framing
# ============================================================================

def parse_xmem_frames(data, image_size, stop_at_end=False):
    """Split an XMemCompress stream into (uncompressed_size, payload) frames.
    Frame header: u16 BE compressed size (uncompressed = 0x8000), or the
    escape 0xFF, u16 BE uncompressed size, u16 BE compressed size.

    If stop_at_end is set, decoding stops when the input is consumed even
    if fewer than image_size bytes were produced (used for LUH segments
    whose true output is smaller than the nominal segment granularity)."""
    frames = []
    pos = 0
    total = 0
    while total < image_size:
        if pos >= len(data):
            if stop_at_end and frames:
                break
            raise ValueError("XMemCompress chain ran past end of data")
        if data[pos] == 0xFF:
            if pos + 5 > len(data):
                raise ValueError("truncated escape frame header")
            un = struct.unpack_from(">H", data, pos + 1)[0]
            cm = struct.unpack_from(">H", data, pos + 3)[0]
            pos += 5
        else:
            cm = struct.unpack_from(">H", data, pos)[0]
            un = min(LZX_FRAME_SIZE, image_size - total)
            pos += 2
        if cm == 0 or pos + cm > len(data):
            if stop_at_end and frames:
                break
            raise ValueError(f"bad frame at {pos:#x} (csize {cm:#x})")
        frames.append((un, data[pos:pos + cm]))
        pos += cm
        total += un
    if not stop_at_end and total != image_size:
        raise ValueError(f"frame chain total {total:#x} != image size "
                         f"{image_size:#x}")
    return frames, pos


def xmem_lzx_decompress(data, image_size, window_size, stop_at_end=False):
    frames, _ = parse_xmem_frames(data, image_size, stop_at_end)
    stream = b"".join(payload for _, payload in frames)
    out_len = sum(un for un, _ in frames) if stop_at_end else image_size
    wbits = window_size.bit_length() - 1
    candidates = [wbits] if (1 << wbits) == window_size and wbits in POSITION_SLOTS \
        else list(range(15, 22))
    last_err = None
    for wb in candidates + [w for w in range(15, 22) if w not in candidates]:
        try:
            return LZXDecoder(wb).decompress(stream, out_len)
        except LZXError as e:
            last_err = e
    raise LZXError(f"could not decompress with any window size: {last_err}")


# ============================================================================
# .lu container parsing
# ============================================================================

LU_PLATFORM_TAGS = {b"x36": "Xbox 360"}

TYPE_NAMES = {
    0x14200007: "texture",
    0x04000007: "mesh_buffers",      # GPU vertex/index data
    0x04800018: "skeleton",
    0x04B00000: "animation",         # named clips: <obj>_call, _repair, ...
    0x04300012: "scene_dictionary",  # one per file, first record
    0x0430000E: "scene_root",
    0x04300000: "anim_clip",
    0x04300003: "scene_anim_binding",
    0x04300009: "scene_link",
    0x04C00000: "sound_event",
    0x04C0000F: "sound_cue",
    0x04C00008: "sound_wave",
    0x04C00010: "sound_binding",     # contains the FX_* name + wave xref
    0x04C0000A: "sound_params",
    0x00000102: "game_object",       # contains the *nb object name
    0x04000001: "unk_04000001",
    0x0420002F: "unk_0420002F",      # possibly collision/physics
}

POOL_NAMES = {0x04: "cpu", 0x0C: "gpu", 0x0B: "audio"}
FLAG_EXTERNAL = 0x0100  # in the low half of the flags word


def u32(d, o):
    return struct.unpack_from(">I", d, o)[0]


class LuRecord:
    __slots__ = ("index", "hash", "type", "size", "offset", "flags")

    def __init__(self, index, h, t, size, offset, flags):
        self.index = index
        self.hash = h
        self.type = t
        self.size = size
        self.offset = offset
        self.flags = flags

    @property
    def external(self):
        return self.offset == 0xFFFFFFFF or (self.flags >> 8) & 0x01

    @property
    def pool(self):
        return POOL_NAMES.get(self.flags >> 24, f"pool_{self.flags >> 24:02x}")

    @property
    def type_name(self):
        return TYPE_NAMES.get(self.type, f"type_{self.type:08x}")


class LuFile:
    def __init__(self, path):
        self.path = Path(path)
        d = self.path.read_bytes()
        self.raw = d
        if d[:4] == b"\x05LUH":
            self._init_luh(d)
            return
        if d[0] != 0x03:
            raise ValueError(f"{path}: bad version byte {d[0]:#x}")
        self.platform = LU_PLATFORM_TAGS.get(d[1:4], f"unknown ({d[1:4]!r})")
        if d[1:4] not in LU_PLATFORM_TAGS:
            print(f"warning: {path}: unrecognised platform tag {d[1:4]!r}; "
                  f"this tool was validated on Xbox 360 files", file=sys.stderr)
        if d[0x17:0x20] != b"--\nITCRAP":
            print(f"warning: {path}: missing '--\\nITCRAP' marker", file=sys.stderr)
        self.version = u32(d, 4)
        self.build_hash = u32(d, 8)
        self.dep_count = d[0x14]

        # dependency names
        self.deps = []
        dep_rel = u32(d, 0x24)
        if self.dep_count and dep_rel != 0xFFFFFFFF:
            p = 0x20 + dep_rel
            for _ in range(self.dep_count):
                e = d.index(b"\0", p)
                self.deps.append(d[p:e].decode("ascii", "replace"))
                p = e + 1

        # pool info
        pi = 0x20 + u32(d, 0x34)
        self.codec = u32(d, pi)
        self.image_size = u32(d, pi + 4)
        self.lzx_window = u32(d, pi + 8)
        self.segment_count = u32(d, pi + 0xC)
        sizes_rel = u32(d, pi + 0x10)
        self.compressed = self.codec == 2 and sizes_rel != 0xFFFFFFFF
        if self.compressed:
            count2 = u32(d, pi + 0x14)
            if count2 != self.segment_count:
                print(f"warning: {path}: segment count fields disagree "
                      f"({self.segment_count} vs {count2})", file=sys.stderr)
            so = 0x20 + sizes_rel
            self.segment_sizes = [u32(d, so + 4 * i)
                                  for i in range(self.segment_count)]
            self.stored_size = sum(self.segment_sizes)
        else:
            self.segment_sizes = []
            self.stored_size = self.image_size

        # footer: record table offset + count
        fo = 0x20 + u32(d, 0x38)
        table = 0x20 + u32(d, fo)
        count = u32(d, fo + 4)

        # data region
        self.data_base = 0x20 + u32(d, 0x40)
        actual = len(d) - self.data_base
        if actual != self.stored_size:
            print(f"warning: {path}: stored data {actual:#x} != header stored size "
                  f"{self.stored_size:#x}", file=sys.stderr)

        self.records = []
        for i in range(count):
            o = table + i * 0x18
            h, t, marker, sz, off, flags = struct.unpack_from(">6I", d, o)
            if marker != 0xFFFFFFFF:
                print(f"warning: {path}: record {i} pointer placeholder is "
                      f"{marker:#010x}, expected 0xffffffff", file=sys.stderr)
            self.records.append(LuRecord(i, h, t, sz, off, flags))

        self.by_hash = {}
        for r in self.records:
            if not r.external:
                self.by_hash.setdefault(r.hash, r)

        self._image = None

    @property
    def image(self):
        """The decompressed data image (lazy). Compressed images are split
        into window-size slices, each an independent XMemCompress stream."""
        if self._image is None:
            data = self.raw[self.data_base:]
            if self.compressed:
                window = self.lzx_window or 0x100000
                seg_out = getattr(self, "luh_segment_out", window)
                stop = getattr(self, "luh_pad_last", False)
                parts = []
                self.valid_ranges = []   # decoded byte spans in the image
                pos = 0
                produced = 0
                remaining = self.image_size
                for i, sz in enumerate(self.segment_sizes):
                    target = min(seg_out, remaining) if not stop else seg_out
                    span_start = produced
                    if sz == min(seg_out, remaining):  # stored raw
                        chunk = data[pos:pos + sz]
                    else:
                        try:
                            chunk = xmem_lzx_decompress(
                                data[pos:pos + sz], target, window,
                                stop_at_end=stop)
                        except Exception:
                            # undecodable segment (e.g. PiP front-end
                            # framing variant): emit zero filler so the
                            # rest of the image still lines up, and record
                            # this span as invalid
                            fill = min(seg_out, remaining)
                            chunk = b"\x00" * fill
                            pos += sz
                            parts.append(chunk)
                            produced += fill
                            remaining -= fill
                            if remaining <= 0:
                                break
                            continue
                    if len(chunk) > remaining:
                        chunk = chunk[:remaining]
                    parts.append(chunk)
                    self.valid_ranges.append((span_start, span_start + len(chunk)))
                    pos += sz
                    produced += len(chunk)
                    remaining -= len(chunk)
                    if remaining <= 0:
                        break
                # pad to full size if a tail segment was skipped
                if produced < self.image_size:
                    parts.append(b"\x00" * (self.image_size - produced))
                self._image = b"".join(parts)[:self.image_size]
            else:
                self._image = data[:self.image_size]
                self.valid_ranges = [(0, self.image_size)]
        return self._image

    def record_decoded(self, rec):
        """True if the record's bytes fall entirely within decoded spans."""
        if rec.external:
            return False
        if not hasattr(self, "valid_ranges"):
            self.image  # populate
        a, b = rec.offset, rec.offset + rec.size
        return any(s <= a and b <= e for s, e in self.valid_ranges)

    def _init_luh(self, d):
        """Panic in Paradise container (version 05, magic 'LUH').

        layout (big-endian):
          0x00  05 'LUH', u32 0x0900, u32 0
          0x0c  u32 record_count
          0x10  records, 39 bytes each:
                {u32 hash, u32 type, u32 link (ffffffff), u32 size,
                 u32 image_offset, u32 flags, 15 bytes extra}
          then  {u32 image_size, u32 1, u32 image_size,
                 u32 segment_size (0x100000), u32 segment_count,
                 u32 compressed_sizes[segment_count]}
          then  XMemCompress LZX streams, one per segment.
        """
        self.platform = "LUH v5 (Panic in Paradise)"
        self.deps = []
        cnt = u32(d, 0x0c)
        self.records = []
        for i in range(cnt):
            o = 0x10 + i * 39
            self.records.append(LuRecord(i, u32(d, o), u32(d, o + 4),
                                         u32(d, o + 12), u32(d, o + 16),
                                         u32(d, o + 20)))
        self.by_hash = {r.hash: r for r in self.records}
        p = 0x10 + cnt * 39
        self.image_size = u32(d, p)
        seg_window = u32(d, p + 12)
        nseg = u32(d, p + 16)
        self.segment_sizes = [u32(d, p + 20 + 4 * i) for i in range(nseg)]
        # LUH streams use a fixed 32 KB LZX window regardless of the
        # 0x100000 segment size stored in the header
        self.lzx_window = 0x8000
        self.luh_segment_out = seg_window or 0x100000
        self.luh_pad_last = True
        self.compressed = True
        self.codec = 2
        self.data_base = p + 20 + 4 * nseg
        self._image = None

    def chunk(self, rec):
        if rec.external:
            return None
        return self.image[rec.offset:rec.offset + rec.size]


# ============================================================================
# name resolution: hash = CRC32(lowercase(name))
# ============================================================================

def crc32_name(name):
    return zlib.crc32(name.lower().encode("ascii", "ignore")) & 0xFFFFFFFF


def harvest_names(lu_files, extra_words=()):
    """Build {crc32: name} from strings inside the data images plus
    any extra candidate words."""
    cands = set(extra_words)
    for lu in lu_files:
        cands.add(lu.path.stem)
        try:
            img = lu.image
        except Exception:
            continue
        for m in re.finditer(rb"[\x20-\x7e]{3,64}", img):
            s = m.group().decode()
            cands.add(s)
            for part in re.split(r"[^\w\-.]+", s):
                if len(part) >= 3:
                    cands.add(part)
    table = {}
    for c in cands:
        table[crc32_name(c)] = c.lower()
    return table


# ============================================================================
# commands
# ============================================================================

def cmd_info(args):
    for path in args.files:
        lu = LuFile(path)
        print(f"== {lu.path.name} ==")
        print(f"  platform     : {lu.platform}")
        print(f"  version      : {lu.version:#010x}   build hash: {lu.build_hash:#010x}")
        print(f"  dependencies : {lu.deps if lu.deps else '(none)'}")
        print(f"  data region  : file offset {lu.data_base:#x}, "
              f"{lu.stored_size:#x} bytes stored")
        if lu.compressed:
            print(f"  compression  : XMemCompress LZX, window {lu.lzx_window:#x}, "
                  f"{lu.segment_count} segment(s), image {lu.image_size:#x} bytes "
                  f"({lu.stored_size * 100 // lu.image_size}% of original)")
        else:
            print(f"  compression  : none (raw image, {lu.image_size:#x} bytes)")
        print(f"  records      : {len(lu.records)}")
        tc = Counter(r.type_name for r in lu.records)
        for t, n in tc.most_common():
            print(f"      {n:4d} x {t}")
        if args.verbose:
            print(f"  {'idx':>4} {'hash':8} {'type':>20} {'size':>9} "
                  f"{'offset':>10} {'pool':>6} ext")
            for r in lu.records:
                off = "external" if r.external else f"{r.offset:#x}"
                print(f"  {r.index:>4} {r.hash:08x} {r.type_name:>20} "
                      f"{r.size:>9,} {off:>10} {r.pool:>6} "
                      f"{'*' if r.external else ''}")
        print()


def cmd_decompress(args):
    lu = LuFile(args.file)
    out = Path(args.out) if args.out else lu.path.with_suffix(".image.bin")
    out.write_bytes(lu.image)
    state = "decompressed" if lu.compressed else "copied raw"
    print(f"{state} {len(lu.image):#x} bytes -> {out}")


def cmd_extract(args):
    lus = [LuFile(p) for p in args.files]
    extra = []
    if args.names:
        extra = [w.strip() for w in Path(args.names).read_text().splitlines()
                 if w.strip()]
    names = harvest_names(lus, extra)

    out_root = Path(args.out) if args.out else Path("lu_extracted")
    grand = Counter()

    for lu in lus:
        out_dir = out_root / lu.path.stem
        out_dir.mkdir(parents=True, exist_ok=True)
        counts = Counter()
        manifest = []

        for r in lu.records:
            name = names.get(r.hash, "")
            base = f"{r.index:04d}_{name or r.hash.to_bytes(4, 'big').hex()}"
            source = "local"
            data = lu.chunk(r)
            # LUH: don't write records that fell in an undecoded segment
            if data is not None and hasattr(lu, "valid_ranges") \
                    and not r.external and not lu.record_decoded(r):
                counts["undecoded"] += 1
                manifest.append((r, name, "undecoded-segment"))
                continue
            if data is None:
                # external: resolve by hash in the other files
                src = next((o for o in lus if o is not lu and r.hash in o.by_hash),
                           None)
                if src is not None:
                    data = src.chunk(src.by_hash[r.hash])
                    source = f"external:{src.path.name}"
                else:
                    source = "external:unresolved"
            if data is not None:
                dest = out_dir / r.type_name
                dest.mkdir(exist_ok=True)
                (dest / (base + ".bin")).write_bytes(data)
                counts[source.split(":")[0]] += 1
            else:
                counts["unresolved"] += 1
            manifest.append((r, name, source))

        with open(out_dir / "manifest.tsv", "w") as f:
            f.write(f"# source: {lu.path.name}  platform: {lu.platform}  "
                    f"deps: {','.join(lu.deps) or '-'}\n")
            f.write("index\thash\tname\ttype\ttype_name\tsize\timage_offset\t"
                    "flags\tpool\tsource\n")
            for r, name, source in manifest:
                off = "" if r.external else f"{r.offset:#x}"
                f.write(f"{r.index}\t{r.hash:08x}\t{name}\t{r.type:08x}\t"
                        f"{r.type_name}\t{r.size}\t{off}\t{r.flags:08x}\t"
                        f"{r.pool}\t{source}\n")

        named = sum(1 for _, name, _ in manifest if name)
        print(f"{lu.path.name}: {len(lu.records)} records -> {out_dir}/ "
              f"(local {counts['local']}, external {counts['external']}, "
              f"unresolved {counts['unresolved']}; {named} names resolved)")
        grand.update(counts)

    print(f"total: {sum(grand.values())} records "
          f"({grand['local']} local, {grand['external']} external, "
          f"{grand['unresolved']} unresolved)")


def main():
    ap = argparse.ArgumentParser(
        description="Naughty Bear (X360) .lu resource container tool")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("info", help="show container details")
    p.add_argument("files", nargs="+")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="list every record")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("decompress", help="dump the raw decompressed data image")
    p.add_argument("file")
    p.add_argument("-o", "--out")
    p.set_defaults(func=cmd_decompress)

    p = sub.add_parser("extract", help="extract all resource chunks")
    p.add_argument("files", nargs="+",
                   help=".lu files (list dependencies too so external "
                        "references resolve)")
    p.add_argument("-o", "--out", help="output directory (default lu_extracted)")
    p.add_argument("--names", help="wordlist of candidate resource names "
                                   "for hash resolution")
    p.set_defaults(func=cmd_extract)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
