#!/usr/bin/env python3
"""
make_sample.py — generate a synthetic, hand-authored Naughty Bear `.lu`
archive for testing and demonstrating the toolkit.

The output (`table_flat.lu`) contains a simple table mesh (a tabletop on
four legs) and a flat solid-colour texture, written directly to the
documented container format. It contains **no game data** — every byte
is generated here — so it is safe to ship in a public repo as a sample.

It is written in the container's *stored* (uncompressed) mode, so no LZX
compressor is required; the existing reader tools extract and convert it
exactly as they would a real file:

    python3 naughty_lu.py extract table_flat.lu -o out/
    python3 lu_convert.py  out/ -o converted/      # -> table OBJ + flat PNG

Format references: docs/LU_FORMAT.md (container, mesh, texture).
"""
import struct
import zlib
from pathlib import Path

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def crc32_lower(name: str) -> int:
    """NB1 names are hashed as CRC32 of the lowercased name."""
    return zlib.crc32(name.lower().encode("ascii")) & 0xFFFFFFFF


def f16x2(u, v) -> bytes:
    """two big-endian half-floats (UV), matching the reader's '>2e'."""
    return struct.pack(">2e", u, v)


def byteswap16(b: bytes) -> bytes:
    a = bytearray(b)
    a[0::2], a[1::2] = b[1::2], b[0::2]
    return bytes(a)


# ---------------------------------------------------------------------------
# geometry — a little table built from axis-aligned boxes
# ---------------------------------------------------------------------------

# unit-cube corners and the 12 triangles (as a list, CCW) referencing them
_CUBE = [(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1),
         (0, 1, 0), (1, 1, 0), (1, 1, 1), (0, 1, 1)]
_CUBE_TRIS = [
    (0, 2, 1), (0, 3, 2),   # bottom
    (4, 5, 6), (4, 6, 7),   # top
    (0, 1, 5), (0, 5, 4),   # -z
    (3, 6, 2), (3, 7, 6),   # +z
    (0, 4, 7), (0, 7, 3),   # -x
    (1, 2, 6), (1, 6, 5),   # +x
]
# simple per-corner UVs so the flat texture maps onto every face
_CUBE_UV = [(0, 0), (1, 0), (1, 1), (0, 1),
            (0, 1), (1, 1), (1, 0), (0, 0)]


def _box(cx, cy, cz, sx, sy, sz):
    """Return (verts, tris) for a box at corner (cx,cy,cz) of size (sx,sy,sz)."""
    verts = [((cx + x * sx), (cy + y * sy), (cz + z * sz), *_CUBE_UV[i])
             for i, (x, y, z) in enumerate(_CUBE)]
    return verts, list(_CUBE_TRIS)


def build_table():
    """A tabletop slab on four legs. Returns (verts, tris)."""
    verts, tris = [], []
    parts = [
        # tabletop: wide, thin, raised
        (-1.0, 0.9, -0.6, 2.0, 0.15, 1.2),
        # four legs
        (-0.95, 0.0, -0.55, 0.15, 0.9, 0.15),
        (0.80, 0.0, -0.55, 0.15, 0.9, 0.15),
        (-0.95, 0.0, 0.40, 0.15, 0.9, 0.15),
        (0.80, 0.0, 0.40, 0.15, 0.9, 0.15),
    ]
    for (cx, cy, cz, sx, sy, sz) in parts:
        v, t = _box(cx, cy, cz, sx, sy, sz)
        base = len(verts)
        verts.extend(v)
        tris.extend((a + base, b + base, c + base) for (a, b, c) in t)
    return verts, tris


# ---------------------------------------------------------------------------
# mesh chunk (record type 0x04000007; inner container tag 0x34200004)
# ---------------------------------------------------------------------------

VBUF_OFF = 0x1000               # vertex buffer (chunk-relative, 0x1000-aligned)
STRIDE = 16                     # pos float3 (12) + uv 2x half-float (4)


def build_mesh_chunk(verts, tris, tex_hash):
    nv, nt = len(verts), len(tris)

    # vertex buffer: pos (>3f) + uv (>2e), big-endian
    vbuf = b"".join(struct.pack(">3f", x, y, z) + f16x2(u, v)
                    for (x, y, z, u, v) in verts)
    vs = len(vbuf)

    ibuf = b"".join(struct.pack(">3H", a, b, c) for (a, b, c) in tris)
    isz = len(ibuf)

    io = (VBUF_OFF + vs + 0xFFF) & ~0xFFF          # 0x1000-align index buffer
    trailer_off = (io + isz + 3) & ~3

    # vertex declaration: position implicit at offset 0 (no explicit
    # POSITION element => the reader uses pos_off 0 with no skinning-prefix
    # shift, which is unambiguous for this non-skinned prop), UV @12.
    DECL_OFF = 0x80
    decl = struct.pack(">I", 0)                    # optional leading zero dword
    decl += struct.pack(">3I", 0x00001A20, 1, 12)  # TEXCOORD @12
    decl += struct.pack(">I", 0xFFFFFFFF)          # end

    size = trailer_off + 24
    buf = bytearray(size)

    # --- header / descriptor area ---
    struct.pack_into(">I", buf, 0x00, 0x00000020)          # magic
    struct.pack_into(">I", buf, 0x04, 1)                   # entry count
    struct.pack_into(">I", buf, 0x08, size)               # total size
    struct.pack_into(">4s", buf, 0x0C, b"\x34\x20\x00\x04")  # MESH tag
    struct.pack_into(">I", buf, 0x10, tex_hash)           # (informational)
    # texture reference {hash, 0x04200007} — scanned in [0x20, quad_off)
    struct.pack_into(">2I", buf, 0x20, tex_hash, 0x04200007)
    # buffer quad {vertex_off, vertex_size, index_off, index_size}
    struct.pack_into(">4I", buf, 0x30, VBUF_OFF, vs, io, isz)
    # vertex declaration
    buf[DECL_OFF:DECL_OFF + len(decl)] = decl

    # --- buffers ---
    buf[VBUF_OFF:VBUF_OFF + vs] = vbuf
    buf[io:io + isz] = ibuf
    # --- trailer {stride, count, prim=4(list), 0, decl_ptr, n_elem} ---
    struct.pack_into(">6I", buf, trailer_off, STRIDE, nt, 4, 0, DECL_OFF, 2)
    return bytes(buf)


# ---------------------------------------------------------------------------
# texture chunk (tag 0x34200007) — flat solid colour, DXT1
# ---------------------------------------------------------------------------

def build_texture_chunk(rgb, w=64, h=64):
    r, g, b = rgb
    c565 = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
    # solid DXT1 block: color0 == color1, all indices 0 (little-endian DXT1)
    block_le = struct.pack("<HH", c565, c565) + b"\x00\x00\x00\x00"

    wb, hb, bpb = max(1, w // 4), max(1, h // 4), 8
    aw, ah = (wb + 31) & ~31, (hb + 31) & ~31       # padded to 32x32 blocks
    nblocks = aw * ah                                # fill the whole tiled area
    tiled = block_le * nblocks                       # solid => tiling is a no-op
    stored = byteswap16(tiled)                       # reader byteswaps back
    dsz = len(stored)

    DOFF = 0x40
    size = DOFF + dsz
    buf = bytearray(size)
    struct.pack_into(">4s", buf, 0x00, b"\x34\x20\x00\x07")  # TEX tag
    struct.pack_into(">2I", buf, 0x08, h, w)                 # height, width
    struct.pack_into(">I", buf, 0x24, 1)                     # mip count
    struct.pack_into(">I", buf, 0x28, 0x12)                  # format 0x12 = DXT1
    struct.pack_into(">2I", buf, 0x38, DOFF, dsz)            # data offset, size
    buf[DOFF:DOFF + dsz] = stored
    return bytes(buf)


# ---------------------------------------------------------------------------
# container (stored / uncompressed `x36`)
# ---------------------------------------------------------------------------

def build_container(chunks):
    """chunks: list of (name, record_type, data). Returns the .lu bytes."""
    # ---- assemble the data image (chunks, each 0x40-aligned) ----
    image = bytearray()
    records = []
    for name, rtype, data in chunks:
        off = len(image)
        image.extend(data)
        if len(image) % 0x40:
            image.extend(b"\x00" * (0x40 - len(image) % 0x40))
        records.append((crc32_lower(name), rtype, len(data), off))
    image_size = len(image)

    POOL = 0x64                  # pool-info  (= 0x20 + 0x44)
    FOOT = 0xA4                  # footer     (= 0x20 + 0x84)
    TABLE = 0xB0                 # record table
    table_bytes = len(records) * 0x18
    data_base = (TABLE + table_bytes + 0x3F) & ~0x3F      # 0x40-align

    out = bytearray(data_base)
    # ---- fixed header ----
    out[0x00:0x04] = b"\x03\x78\x33\x36"                  # ver 3 + 'x36'
    struct.pack_into(">I", out, 0x04, 0x04100000)         # format version
    struct.pack_into(">I", out, 0x08, 0x00000000)         # build hash (n/a)
    struct.pack_into(">I", out, 0x0C, 4)
    struct.pack_into(">I", out, 0x10, 4)
    out[0x14] = 0                                         # dependency count = 0
    out[0x17:0x20] = b"--\nITCRAP"                        # marker
    struct.pack_into(">I", out, 0x24, 0xFFFFFFFF)         # dep_rel: none
    struct.pack_into(">I", out, 0x34, POOL - 0x20)        # pool_info_rel
    struct.pack_into(">I", out, 0x38, FOOT - 0x20)        # footer_rel
    struct.pack_into(">I", out, 0x40, data_base - 0x20)   # data_base_rel
    # ---- pool info: stored mode (codec != 2 and sizes_rel = ffffffff) ----
    struct.pack_into(">I", out, POOL + 0x00, 0)           # codec 0 = stored
    struct.pack_into(">I", out, POOL + 0x04, image_size)  # image size
    struct.pack_into(">I", out, POOL + 0x08, 0)           # lzx window (n/a)
    struct.pack_into(">I", out, POOL + 0x0C, 0)           # segment count
    struct.pack_into(">I", out, POOL + 0x10, 0xFFFFFFFF)  # sizes_rel: none
    struct.pack_into(">I", out, POOL + 0x14, 0)           # count2
    # ---- footer: record table offset + count ----
    struct.pack_into(">I", out, FOOT + 0x00, TABLE - 0x20)
    struct.pack_into(">I", out, FOOT + 0x04, len(records))
    # ---- record table ----
    for i, (h, rtype, sz, off) in enumerate(records):
        o = TABLE + i * 0x18
        struct.pack_into(">6I", out, o, h, rtype, 0xFFFFFFFF, sz, off, 0)
    # ---- data image ----
    out.extend(image)
    return bytes(out)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("-o", "--out", default="table_flat.lu")
    ap.add_argument("--color", default="166,124,90",
                    help="flat texture colour as R,G,B (0-255)")
    args = ap.parse_args()
    rgb = tuple(int(c) for c in args.color.split(","))

    verts, tris = build_table()
    tex_name = "sample_flat_texture"
    tex_hash = crc32_lower(tex_name)
    mesh = build_mesh_chunk(verts, tris, tex_hash)
    tex = build_texture_chunk(rgb)

    lu = build_container([
        ("sample_table_mesh", 0x04000007, mesh),
        (tex_name,            0x04200007, tex),
    ])
    Path(args.out).write_bytes(lu)
    print(f"wrote {args.out}: {len(lu)} bytes "
          f"({len(verts)} verts, {len(tris)} tris, "
          f"flat texture rgb{rgb})")


if __name__ == "__main__":
    main()
