"""Wwise sound introspection for DD2 (verified 2026-11-03 on the Mage skill bank: event 724412793 -> media 821991822 / 945428007).

How DD2's audio is laid out (found while locating the Toll the Dead bell):
  * natives/stm/sound/wwise/<name>_es.sbnk.1.x64  BKHD + HIRC only: the event / sound / container structure, NO audio.
  * natives/stm/sound/wwise/<name>_m.sbnk.1.x64   BKHD + DIDX + DATA: the audio (WEM files), looked up by media (source) id.
    The `_m` companions are NOT in the community file list (pak.search_list never shows them) - they are found by NAME (pak.find by hash).
  * the game script side: a player container (app.WwiseContainerApp) lists soundlib.SoundTriggerInfo { _TriggerId, _EventId } per bank;
    a trigger id is what WwiseContainerApp:triggerLogLess plays, the event id is the HIRC event object.
  * HIRC (v140): u32 count, then objects { u8 type, u32 size, u32 id, payload }. type 2 = Sound SFX (plugin u32, streamType u8, sourceId u32,
    inMemorySize u32, bits u8), 3 = Action (u16 type, u32 target id; 0x0403 = play), 4 = Event (u8 count, u32 action ids), 5 = Random/Sequence
    container, 6 = Switch container, 7 = Actor-Mixer.
  * media: RIFF/WAVE, fmt tag 0xFFFF = Wwise Vorbis, 48 kHz mono for spell sounds.
Everything here works on bytes from the game paks (dd2mcp.pak); nothing needs the game running."""
import re
import struct

from . import pak

DIR = "natives/stm/sound/wwise/"
HIRC_TYPES = {1: "settings", 2: "sound", 3: "action", 4: "event", 5: "random/sequence container", 6: "switch container", 7: "actor-mixer",
              8: "music?", 9: "bus", 10: "bus?", 11: "?", 12: "?", 13: "?", 14: "fx/aux bus", 15: "?", 17: "fx custom", 18: "?", 19: "?"}


def bank_path(stem: str, kind: str = "es") -> str:
    """'mgc_skill_es' / 'mgc_skill' -> natives/stm/sound/wwise/mgc_skill_es.sbnk.1.x64"""
    stem = re.sub(r"\.sbnk.*$", "", stem.split("/")[-1])
    if kind == "m":
        stem = re.sub(r"_es$", "", stem) + "_m"
    elif kind == "es" and not stem.endswith("_es"):
        stem += "_es"
    return f"{DIR}{stem}.sbnk.1.x64"


def read_bank(path_or_stem: str) -> bytes | None:
    p = path_or_stem if path_or_stem.startswith("natives/") else bank_path(path_or_stem)
    r = pak.find(p)
    return r[1] if r else None


def chunks(data: bytes) -> dict:
    """Bank chunks: tag -> (payload offset, payload size)."""
    out, p = {}, 0
    while p + 8 <= len(data):
        tag, sz = data[p:p + 4], struct.unpack_from("<I", data, p + 4)[0]
        out[tag.decode("latin1")] = (p + 8, sz)
        p += 8 + sz
    return out


def bank_id(data: bytes) -> int:
    off, _ = chunks(data)["BKHD"]
    return struct.unpack_from("<II", data, off)[1]


def hirc(data: bytes) -> dict:
    """HIRC objects: id -> (type, payload bytes after the id)."""
    off, sz = chunks(data)["HIRC"]
    n = struct.unpack_from("<I", data, off)[0]
    p, objs = off + 4, {}
    for _ in range(n):
        t = data[p]
        size = struct.unpack_from("<I", data, p + 1)[0]
        oid = struct.unpack_from("<I", data, p + 5)[0]
        objs[oid] = (t, data[p + 9:p + 5 + size], p)   # p = absolute offset of the object (type byte), for patching
        p += 5 + size
    return objs


def media_index(data: bytes) -> dict:
    """DIDX of a `_m` bank: media id -> (offset into DATA, size)."""
    if "DIDX" not in chunks(data):
        return {}
    off, sz = chunks(data)["DIDX"]
    return {struct.unpack_from("<I", data, off + 12 * i)[0]: struct.unpack_from("<II", data, off + 12 * i + 4) for i in range(sz // 12)}


def media_bytes(data: bytes, mid: int) -> bytes | None:
    idx = media_index(data)
    if mid not in idx:
        return None
    o, s = idx[mid]
    return data[chunks(data)["DATA"][0] + o:chunks(data)["DATA"][0] + o + s]


def sound_info(payload: bytes) -> dict:
    plugin, stream, src, size = struct.unpack_from("<IBII", payload, 0)
    return {"plugin": hex(plugin), "stream": stream, "media": src, "size": size}


def _children(payload: bytes, objs: dict) -> list:
    """Child ids of a container / mixer: the u32 count followed by that many ids that all exist in the bank (heuristic scan; the node-base
    parameter block in front of it is long and version dependent)."""
    best = []
    for p in range(0, len(payload) - 8, 1):
        n = struct.unpack_from("<I", payload, p)[0]
        if 1 <= n <= 64 and p + 4 + 4 * n <= len(payload):
            ids = struct.unpack_from(f"<{n}I", payload, p + 4)
            if all(i in objs for i in ids) and len(ids) > len(best):
                best = list(ids)
    return best


def resolve(event_id: int, objs: dict) -> list:
    """Everything an event plays: [{'path': [ids...], 'type': ..., 'media': id | None}] following actions -> targets -> containers -> sounds."""
    if event_id not in objs:
        return []
    t, b, _ = objs[event_id]
    if t != 4:
        return [{"path": [event_id], "type": HIRC_TYPES.get(t, t), "media": None}]
    n = b[0]
    out = []

    def walk(oid, path):
        if oid not in objs:
            out.append({"path": path + [oid], "type": "missing (other bank)", "media": None})
            return
        t, b, _ = objs[oid]
        if t == 2:
            si = sound_info(b)
            out.append({"path": path + [oid], "type": "sound", "media": si["media"], "plugin": si["plugin"], "stream": si["stream"], "size": si["size"]})
        elif t == 3:
            at, tgt = struct.unpack_from("<HI", b, 0)
            walk(tgt, path + [oid])
        elif t in (5, 6, 7):
            for c in _children(b, objs):
                walk(c, path + [oid])
        else:
            out.append({"path": path + [oid], "type": HIRC_TYPES.get(t, t), "media": None})

    for a in struct.unpack_from(f"<{n}I", b, 1):
        walk(a, [event_id])
    return out


def locate_media(mid: int, stem: str | None = None) -> tuple | None:
    """Find which `_m` bank holds a media id: the companion of `stem` first, then every `_m` bank in the community list.
    Returns (bank path, bytes of the bank, offset in DATA, size)."""
    cands = []
    if stem:
        cands.append(bank_path(stem, "m"))
        cands.append(re.sub(r"_m\.sbnk", "_nrd_m.sbnk", bank_path(stem, "m")))
    cands += [n for n in pak.search_list(r"natives/stm/sound/wwise/[^/]*_m\.sbnk\.1\.x64$", 1000) if n not in cands]
    for c in cands:
        d = read_bank(c)
        if d and mid in media_index(d):
            o, s = media_index(d)[mid]
            return c, d, o, s
    return None


def wem_info(w: bytes) -> dict:
    if w[:4] != b"RIFF":
        return {"error": "not RIFF", "head": w[:8].hex()}
    tag, ch, rate, byterate, align, bits = struct.unpack_from("<HHIIHH", w, 20)
    codecs = {0xFFFF: "Wwise Vorbis", 0xFFFE: "PCM extensible", 0x0001: "PCM", 0x0002: "ADPCM", 0x0161: "WMA", 0x3039: "Wwise Opus", 0x3040: "Wwise Opus", 0x3041: "Wwise Opus"}
    return {"codec": codecs.get(tag, hex(tag)), "channels": ch, "rate": rate, "bytes": len(w), "approx_seconds": round(len(w) * 8 / (byterate * 8), 2) if byterate else None}


# ---- editing -------------------------------------------------------------------------------------------------------------------------------

def pcm_wem(samples_le16: bytes, rate: int = 48000, channels: int = 1) -> bytes:
    """A Wwise PCM media file: RIFF/WAVE with a plain PCM `fmt ` (tag 1) + `data`. Needs the sound object's plugin id to be 0x00010001 (PCM)."""
    fmt = struct.pack("<HHIIHH", 1, channels, rate, rate * channels * 2, channels * 2, 16)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(samples_le16)) + samples_le16
    return b"RIFF" + struct.pack("<I", len(body)) + body


def rebuild_media_bank(data: bytes, replace: dict | None = None, add: dict | None = None) -> bytes:
    """Return a new `_m` bank with media replaced (id -> wem bytes) and / or appended (id -> wem bytes); DIDX and DATA are rewritten, BKHD kept."""
    replace, add = replace or {}, add or {}
    c = chunks(data)
    idx = media_index(data)
    do = c["DATA"][0]
    items = {}
    for mid, (o, s) in idx.items():
        items[mid] = replace.get(mid, data[do + o:do + o + s])
    items.update(add)
    didx, blob = b"", b""
    for mid in sorted(items):
        w = items[mid]
        pad = (-len(blob)) % 16
        blob += b"\0" * pad
        didx += struct.pack("<III", mid, len(blob), len(w))
        blob += w
    bkhd = data[:c["BKHD"][0] + c["BKHD"][1]]
    return bkhd + b"DIDX" + struct.pack("<I", len(didx)) + didx + b"DATA" + struct.pack("<I", len(blob)) + blob


def patch_sound_source(es_data: bytes, sound_id: int, media_id: int | None = None, plugin: int | None = None, size: int | None = None) -> bytes:
    """Return a copy of an `_es` bank where HIRC sound object `sound_id` points at another media id and / or plugin (same byte length)."""
    objs = hirc(es_data)
    t, b, p = objs[sound_id]
    assert t == 2, "not a sound object"
    d = bytearray(es_data)
    base = p + 9   # payload starts after type(1) size(4) id(4)
    if plugin is not None:
        struct.pack_into("<I", d, base, plugin)
    if media_id is not None:
        struct.pack_into("<I", d, base + 5, media_id)
    if size is not None:
        struct.pack_into("<I", d, base + 9, size)
    return bytes(d)
