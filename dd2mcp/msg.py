"""Game text (.msg) reader: localized strings keyed by GUID / entry name.

Format ported from kagenocookie/RE-Engine-Lib MsgFile.cs (MIT): 0x48-byte header, everything from dataOffset on is
XOR-chained with a 16-byte key (version > 12), entries hold a GUID, murmur hash, name and one string offset per language.
"""
import functools
import pathlib
import re
import struct
import uuid

from . import pak

_KEY = bytes([207, 206, 251, 248, 236, 10, 51, 102, 147, 169, 29, 147, 80, 57, 95, 9])
LANGS = ["Japanese", "English", "French", "Italian", "German", "Spanish", "Russian", "Polish", "Dutch", "Portuguese",
         "PortugueseBr", "Korean", "TraditionalChinese", "SimplifiedChinese", "Finnish", "Swedish", "Danish",
         "Norwegian", "Czech", "Hungarian", "Slovak", "Arabic", "Turkish", "Bulgarian", "Greek", "Romanian", "Thai",
         "Ukrainian", "Vietnamese", "Indonesian", "Fiction", "Hindi", "LatinAmericanSpanish"]
_ATTR_LONG, _ATTR_DOUBLE = 0, 1


def _decrypt(buf: bytearray, start: int) -> None:
    prev = 0
    for n, i in enumerate(range(start, len(buf))):
        cur = buf[i]
        buf[i] = prev ^ cur ^ _KEY[n & 15]
        prev = cur


def _wstr(buf: bytes, off: int) -> str:
    end = off
    while end + 1 < len(buf) and (buf[end] or buf[end + 1]):
        end += 2
    return buf[off:end].decode("utf-16-le", "replace")


class Entry:
    __slots__ = ("guid", "name", "texts", "attrs")

    def __init__(self, guid, name, texts, attrs):
        self.guid, self.name, self.texts, self.attrs = guid, name, texts, attrs

    def text(self, lang: str = "English") -> str:
        return self.texts.get(lang) or self.texts.get("English") or next(iter(self.texts.values()), "")


class Msg:
    def __init__(self, data: bytes):
        buf = bytearray(data)
        (self.version, magic, _hdr, count, nattr, nlang, _pad, data_off, _unk, lang_off, attr_off,
         attr_name_off) = struct.unpack_from("<IIqiiiIqqqqq", buf, 0)
        if magic != 0x47534D47:
            raise ValueError("not a MSG file")
        self.entries: list[Entry] = []
        if data_off >= len(buf):
            return
        if self.version > 12:
            _decrypt(buf, data_off)
        b = bytes(buf)
        offs = struct.unpack_from(f"<{count}q", b, 0x48)
        langs = struct.unpack_from(f"<{nlang}i", b, lang_off)
        types = struct.unpack_from(f"<{nattr}i", b, attr_off)
        names = [_wstr(b, struct.unpack_from("<q", b, attr_name_off + 8 * i)[0]) for i in range(nattr)]
        self.attr_names = names
        for o in offs:
            guid = uuid.UUID(bytes_le=b[o:o + 16])
            ename_off, attr_ptr = struct.unpack_from("<qq", b, o + 24)
            lo = struct.unpack_from(f"<{nlang}q", b, o + 40)
            texts = {LANGS[l] if l < len(LANGS) else str(l): _wstr(b, p) for l, p in zip(langs, lo)}
            attrs = {}
            for i, t in enumerate(types):
                v = b[attr_ptr + 8 * i:attr_ptr + 8 * i + 8]
                if len(v) < 8:
                    break
                attrs[names[i]] = (struct.unpack("<q", v)[0] if t == _ATTR_LONG else
                                   struct.unpack("<d", v)[0] if t == _ATTR_DOUBLE else
                                   _wstr(b, struct.unpack("<q", v)[0]))
            self.entries.append(Entry(str(guid), _wstr(b, ename_off), texts, attrs))


@functools.lru_cache(maxsize=64)
def load(path: str) -> Msg | None:
    p = pathlib.Path(path)
    data = p.read_bytes() if p.exists() else (pak.find(path) or (None, None))[1]
    return Msg(data) if data else None


def files() -> list[str]:
    return pak.search_list(r"\.msg\.\d+$", 5000)


_INDEX = pathlib.Path.home() / ".cache" / "dd2-mcp" / "msg_index.json"


def _stamp() -> str:
    return ",".join(f"{p.name}:{p.stat().st_mtime_ns}" for p in pak.game_paks())


def index(rebuild: bool = False) -> list[list[str]]:
    """[[file, name, guid, english text], ...] for every .msg; cached on disk keyed by the pak mtimes."""
    import json
    stamp = _stamp()
    if not rebuild and _INDEX.exists():
        d = json.loads(_INDEX.read_text())
        if d.get("stamp") == stamp:
            return d["rows"]
    rows = []
    for f in files():
        try:
            m = load(f)
        except Exception:
            continue
        short = f.split("natives/stm/")[-1]
        rows += [[short, e.name, e.guid, e.text("English")] for e in (m.entries if m else [])]
    load.cache_clear()
    _INDEX.parent.mkdir(parents=True, exist_ok=True)
    _INDEX.write_text(json.dumps({"stamp": stamp, "rows": rows}))
    return rows


def search(pattern: str, lang: str = "English", scope: str = "", limit: int = 40) -> str:
    """Regex over entry names, GUIDs and English text of every .msg (scope: regex on the file path)."""
    rx, sx = re.compile(pattern, re.I), re.compile(scope, re.I) if scope else None
    rows = index()
    out = []
    for f, name, guid, text in rows:
        if sx and not sx.search(f):
            continue
        if rx.search(text) or rx.search(name) or rx.search(guid):
            out.append(f"{f} | {name} | {guid} | {text[:160]!r}")
            if len(out) >= limit:
                return "\n".join(out + [f"... (limit {limit}; {len(rows)} entries indexed)"])
    return "\n".join(out) or f"no match ({len(rows)} entries indexed)"


def lookup(guid: str, lang: str = "English") -> str:
    """Full text of one entry (GUID or exact entry name) in every language it has."""
    g = guid.lower()
    hit = [r for r in index() if r[2].lower() == g or r[1].lower() == g]
    out = []
    for f, name, gd, _ in hit[:5]:
        m = load("natives/stm/" + f)
        e = next(x for x in m.entries if x.guid == gd)
        out.append(f"{f} | {name} | {gd}")
        out += [f"  {k}: {v!r}" for k, v in e.texts.items() if v and (lang == "all" or k == lang)]
        out += [f"  attr {k} = {v!r}" for k, v in e.attrs.items()]
    return "\n".join(out) or "not found"
