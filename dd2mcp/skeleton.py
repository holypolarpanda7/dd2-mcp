"""DD2 .skeleton files ('skln', version 8): the skeleton resource that via.motion.DummySkeleton / CustomSkeleton turn into joints on a Transform.

Layout (reverse-engineered 2026-11-02 from the game's 27 .skeleton files; round-trips byte-exact):
  0x00 u32 version (8) | u32 magic 'skln' | u64 0 | u64 boneTableOffset (0x30) | u64 nameTableOffset | u64 boneCount | u64 0 | ...
  bone record, 64 bytes: u64 nameOffset | u32 murmur3(utf-16 name) | i16 parent | i16 symmetry | f32[4] rotation quaternion (x,y,z,w) |
                         f32[4] translation (x,y,z,w=1) | f32[4] scale (x,y,z, pad)   [verified: hips (0,1,0), spine (0,0.5,0) are in the 2nd vec4]
  name table: UTF-16 NUL-terminated names, in bone order.
Names hash like node names: murmur3 over the UTF-16LE bytes, seed 0xFFFFFFFF (murmur.py)."""
import struct

from . import murmur

HEAD = struct.Struct("<II QQQ QQ")  # version, magic, 0, boneTable, nameTable, boneCount, 0
MAGIC = 0x6E6C6B73  # 'skln'


class Bone:
    def __init__(self, name, parent=-1, symmetry=-1, t=(0, 0, 0, 1), q=(0, 0, 0, 1), s=(1, 1, 1, 0)):
        self.name, self.parent, self.symmetry, self.t, self.q, self.s = name, parent, symmetry, tuple(t), tuple(q), tuple(s)

    def __repr__(self):
        return f"Bone({self.name!r}, parent={self.parent}, t={self.t[:3]}, q={self.q})"


def hash_name(name: str) -> int:
    return murmur.murmur3(name.encode("utf-16-le"))


def read(data: bytes) -> list[Bone]:
    ver, magic, _z, table, names, count, _z2 = HEAD.unpack_from(data, 0)
    if magic != MAGIC:
        raise ValueError("not a skln file")
    bones = []
    for i in range(count):
        o = table + 64 * i
        name_off, h, parent, sym = struct.unpack_from("<QIhh", data, o)
        f = struct.unpack_from("<12f", data, o + 16)
        end = data.index(b"\x00\x00", name_off)
        end += (end - name_off) % 2
        name = data[name_off:end].decode("utf-16-le")
        if hash_name(name) != h:
            raise ValueError(f"bone {i} {name!r}: hash {h:08x} != murmur3 {hash_name(name):08x}")
        bones.append(Bone(name, parent, sym, f[4:8], f[0:4], f[8:12]))   # file order: rotation, translation, scale
    return bones


def write(bones: list[Bone], version: int = 8) -> bytes:
    table = 0x30
    table_end = table + 64 * len(bones)
    names_off = table_end + 8 * len(bones)
    name_blob, offs = bytearray(), []
    for b in bones:
        offs.append(names_off + len(name_blob))
        name_blob += b.name.encode("utf-16-le") + b"\x00\x00"
    head = bytearray(table)
    HEAD.pack_into(head, 0, version, MAGIC, 0, table, table_end, len(bones), 0)
    body = bytearray()
    for b, no in zip(bones, offs):
        body += struct.pack("<QIhh12f", no, hash_name(b.name), b.parent, b.symmetry, *b.q, *b.t, *b.s)
    signed = lambda h: h - (1 << 32) if h >= 1 << 31 else h
    lookup = [(signed(h), i) for h, i in sorted((hash_name(b.name), i) for i, b in enumerate(bones))]
    return bytes(head + body + b"".join(struct.pack("<ii", h, i) for h, i in lookup) + name_blob)
