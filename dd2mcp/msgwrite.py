"""Game text (.msg) writer: read a .msg into an editable model, add or change entries, write it back.

Layout (version 24, as msg.py reads it): 0x48-byte header; entry offset table (count x i64); one i64 (zero); language table
(nlang x i32, padded to 8); attribute type table (nattr x i32, padded) and attribute name pointers (nattr x i64); the entry
records (16-byte GUID, u32 'crc' (meaning unknown), u32 murmur3(name UTF-16LE, seed 0xFFFFFFFF), i64 name ptr, i64 attribute
block ptr, nlang x i64 text ptrs); each entry's attribute block (nattr x 8 bytes); then, from data_off, the UTF-16 string pool
(deduplicated, null-terminated). Everything from data_off on is XOR-chained with a 16-byte key.

Writing keeps the original string order (the pool order is not derivable) and appends new strings, so an unchanged file
round-trips byte for byte (tested on every game .msg: tests/test_msgwrite.py).
"""
import struct
import uuid

from . import msg
from .murmur import murmur3


def _encrypt(buf: bytearray, start: int) -> None:
    prev = 0
    for n, i in enumerate(range(start, len(buf))):
        cur = buf[i] ^ prev ^ msg._KEY[n & 15]
        buf[i] = cur
        prev = cur


class WEntry:
    __slots__ = ("guid", "crc", "name", "texts", "attrs")

    def __init__(self, guid: str, name: str, texts: list[str], attrs: list, crc: int = 0):
        self.guid, self.name, self.texts, self.attrs, self.crc = guid, name, texts, attrs, crc


class MsgFile:
    """Editable .msg. texts per entry: one string per slot of the language table (same order)."""

    def __init__(self, data: bytes):
        buf = bytearray(data)
        (self.version, magic, self.hdr, count, nattr, nlang, self.pad, data_off, unk_off, lang_off, attr_off,
         attr_name_off) = struct.unpack_from("<IIqiiiIqqqqq", buf, 0)
        if magic != 0x47534D47:
            raise ValueError("not a MSG file")
        if self.version != 24:
            raise ValueError(f"msg version {self.version}: only 24 is supported")
        msg._decrypt(buf, data_off)
        b = bytes(buf)
        self.unk = b[unk_off:unk_off + 8]
        self.langs = list(struct.unpack_from(f"<{nlang}i", b, lang_off))
        self.attr_types = list(struct.unpack_from(f"<{nattr}i", b, attr_off))
        name_ptrs = struct.unpack_from(f"<{nattr}q", b, attr_name_off)
        self.attr_names = [msg._wstr(b, p) for p in name_ptrs]
        offs = struct.unpack_from(f"<{count}q", b, 0x48)
        self.entries: list[WEntry] = []
        refs = list(name_ptrs)
        for o in offs:
            gb = b[o:o + 16]
            crc, _h = struct.unpack_from("<II", b, o + 16)
            ename, aptr = struct.unpack_from("<qq", b, o + 24)
            tptrs = struct.unpack_from(f"<{nlang}q", b, o + 40)
            attrs = []
            for i, t in enumerate(self.attr_types):
                raw = b[aptr + 8 * i:aptr + 8 * i + 8]
                if t == msg._ATTR_LONG:
                    attrs.append(struct.unpack("<q", raw)[0])
                elif t == msg._ATTR_DOUBLE:
                    attrs.append(struct.unpack("<d", raw)[0])
                else:
                    p = struct.unpack("<q", raw)[0]
                    refs.append(p)
                    attrs.append(msg._wstr(b, p))
            refs.append(ename)
            refs += tptrs
            self.entries.append(WEntry(str(uuid.UUID(bytes_le=gb)), msg._wstr(b, ename), [msg._wstr(b, p) for p in tptrs], attrs, crc))
        # pool order: every distinct string in file-offset order
        self.pool = [msg._wstr(b, p) for p in sorted(set(refs))]
        self._layout = (unk_off, lang_off, attr_off, attr_name_off, data_off, offs[0] if offs else None)

    # ---- editing ------------------------------------------------------------------------------------------------------
    def find(self, key: str) -> WEntry | None:
        k = key.lower()
        return next((e for e in self.entries if e.guid == k or e.name.lower() == k), None)

    def add(self, name: str, text: str | dict, guid: str | None = None) -> WEntry:
        """Add an entry. text: one string for every language, or {language name: text} (others fall back to English)."""
        if self.find(name):
            raise ValueError(f"entry {name} exists")
        if isinstance(text, str):
            texts = [text] * len(self.langs)
        else:
            en = text.get("English", "")
            texts = [text.get(msg.LANGS[l] if l < len(msg.LANGS) else str(l), en) for l in self.langs]
        g = (guid or str(uuid.uuid4())).lower()
        attrs = [0 if t == msg._ATTR_LONG else 0.0 if t == msg._ATTR_DOUBLE else "" for t in self.attr_types]
        e = WEntry(g, name, texts, attrs, 0)
        self.entries.append(e)
        return e

    # ---- writing ------------------------------------------------------------------------------------------------------
    def to_bytes(self) -> bytes:
        nlang, nattr, count = len(self.langs), len(self.attr_types), len(self.entries)
        align = lambda x: (x + 7) & ~7
        off_table = 0x48
        unk_off = off_table + 8 * count
        lang_off = unk_off + 8
        attr_off = align(lang_off + 4 * nlang)
        attr_name_off = align(attr_off + 4 * nattr)
        entry0 = attr_name_off + 8 * nattr
        rec = 40 + 8 * nlang
        attr_blk = entry0 + rec * count
        pool_off = attr_blk + 8 * nattr * count
        data_off = pool_off                 # the attribute blocks sit before the encrypted region
        # string pool: original order first, then new strings in first-use order
        strings = list(self.pool)
        seen = set(strings)
        uses = list(self.attr_names)
        for e in self.entries:
            uses += [a for a, t in zip(e.attrs, self.attr_types) if t not in (msg._ATTR_LONG, msg._ATTR_DOUBLE)]
            uses.append(e.name)
            uses += e.texts
        for s in uses:
            if s not in seen:
                strings.append(s)
                seen.add(s)
        where, pool, pos = {}, bytearray(), pool_off
        for s in strings:
            if s in where:
                continue
            where[s] = pos
            enc = s.encode("utf-16-le") + b"\0\0"
            pool += enc
            pos += len(enc)
        out = bytearray(pool_off)
        struct.pack_into("<IIqiiiIqqqqq", out, 0, self.version, 0x47534D47, self.hdr, count, nattr, nlang, self.pad,
                         data_off, unk_off, lang_off, attr_off, attr_name_off)
        struct.pack_into(f"<{count}q", out, off_table, *[entry0 + rec * i for i in range(count)])
        out[unk_off:unk_off + 8] = self.unk
        struct.pack_into(f"<{nlang}i", out, lang_off, *self.langs)
        if nattr:
            struct.pack_into(f"<{nattr}i", out, attr_off, *self.attr_types)
            struct.pack_into(f"<{nattr}q", out, attr_name_off, *[where[n] for n in self.attr_names])
        for i, e in enumerate(self.entries):
            o = entry0 + rec * i
            out[o:o + 16] = uuid.UUID(e.guid).bytes_le
            struct.pack_into("<II", out, o + 16, e.crc, murmur3(e.name.encode("utf-16-le")))
            ab = attr_blk + 8 * nattr * i
            struct.pack_into("<qq", out, o + 24, where[e.name], ab)
            struct.pack_into(f"<{nlang}q", out, o + 40, *[where[t] for t in e.texts])
            for k, (a, t) in enumerate(zip(e.attrs, self.attr_types)):
                if t == msg._ATTR_LONG:
                    struct.pack_into("<q", out, ab + 8 * k, a)
                elif t == msg._ATTR_DOUBLE:
                    struct.pack_into("<d", out, ab + 8 * k, a)
                else:
                    struct.pack_into("<q", out, ab + 8 * k, where[a])
        out += pool
        _encrypt(out, data_off)
        return bytes(out)
