"""RE Engine .tex (DD2 version 251211553 / 0x0EF92F21) to and from PIL images.

Header as seen in DD2's GUI textures (2026-10-29): magic TEX\\0, u32 version, u16 width, u16 height, u16 depth,
u8 image count, u8 mip-entry size (0x10), u32 DXGI format, i32 swizzle (-1), 0x1c..0x27 flags/reserved, then one
16-byte entry per mip x image: u64 data offset, u32 row pitch, u32 byte size. Rows are padded to 256 bytes
(800 px BC7 -> pitch 3328). Decoding goes through Pillow's DDS reader (BC1-7, RGBA8); encoding writes
uncompressed R8G8B8A8 (sRGB if the source was), single mip, which the GUI accepts like the original."""
import io
import struct

from PIL import Image

BC_FORMATS = {71: "BC1", 72: "BC1", 74: "BC2", 75: "BC2", 77: "BC3", 78: "BC3", 80: "BC4", 83: "BC5", 98: "BC7", 99: "BC7"}
SRGB = {29, 72, 75, 78, 99}
RGBA8, RGBA8_SRGB = 28, 29


def header(data: bytes) -> dict:
    if data[:4] != b"TEX\0":
        raise ValueError("not a .tex file")
    version, w, h, depth, images, mip_size, fmt = struct.unpack_from("<IHHHBBI", data, 4)
    mips = []
    for i in range(images or 1):
        off, pitch, size = struct.unpack_from("<QII", data, 0x28 + 16 * i)
        mips.append((off, pitch, size))
    return {"version": version, "width": w, "height": h, "depth": depth, "images": images, "format": fmt, "mips": mips}


def _dds(width: int, height: int, data: bytes, dxgi: int) -> bytes:
    fourcc = int.from_bytes(b"DX10", "little")
    hdr = struct.pack("<4sIIIIIII44sIIIIIIIIIIIII", b"DDS ", 124, 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000, height, width,
                      len(data), 0, 1, b"\0" * 44, 32, 0x4, fourcc, 0, 0, 0, 0, 0, 0x1000, 0, 0, 0, 0)
    return hdr + struct.pack("<IIIII", dxgi, 3, 0, 1, 0) + data


def to_image(data: bytes) -> Image.Image:
    h = header(data)
    off, pitch, size = h["mips"][0]
    fmt = h["format"]
    if fmt in BC_FORMATS:
        bw = pitch // (8 if BC_FORMATS[fmt] in ("BC1", "BC4") else 16) * 4
        rows = size // pitch * 4
        im = Image.open(io.BytesIO(_dds(bw, rows, data[off:off + size], fmt)))
    elif fmt in (RGBA8, RGBA8_SRGB):
        im = Image.frombytes("RGBA", (pitch // 4, size // pitch), data[off:off + size])
    else:
        raise ValueError(f"unsupported tex format {fmt}")
    return im.convert("RGBA").crop((0, 0, h["width"], h["height"]))


def from_image(im: Image.Image, template: bytes) -> bytes:
    """A .tex holding `im` as uncompressed RGBA8, keeping the template's version, flags and sRGB-ness."""
    im = im.convert("RGBA")
    w, h = im.size
    src = header(template)
    fmt = RGBA8_SRGB if src["format"] in SRGB else RGBA8
    pitch = (w * 4 + 255) // 256 * 256
    rows = bytearray()
    raw = im.tobytes()
    for y in range(h):
        rows += raw[y * w * 4:(y + 1) * w * 4].ljust(pitch, b"\0")
    head = bytearray(template[:0x28])
    struct.pack_into("<HHHBB", head, 8, w, h, 1, 1, 0x10)
    struct.pack_into("<I", head, 0x10, fmt)
    head += struct.pack("<QII", 0x38, pitch, len(rows))
    return bytes(head) + bytes(rows)
