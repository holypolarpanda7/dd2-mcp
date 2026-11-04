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


def build_event_bank(template_es: bytes, template_sound: int, template_action: int, pcm: bytes, bank_id: int, event_id: int, action_id: int,
                     sound_id: int, media_id: int, rate: int = 48000, wem: bytes | None = None, pitch_cents: float | None = None) -> bytes:
    """A NEW self-contained bank (BKHD + DIDX + DATA + HIRC) holding one event -> action -> sound that plays `pcm` (16-bit mono little-endian) through
    the PCM source plugin. The action and sound objects are clones of `template_*` objects of an existing bank (so every field the engine expects is
    already well formed); only ids, the source and the (now dangling) parent id are patched. A path of our own (e.g. natives/stm/wizard/sound/x.sbnk.1.x64)
    avoids overriding a game bank, which crashed the game at start (2026-11-03: even a byte-identical override of mgc_skill_m / _es did)."""
    objs = hirc(template_es)
    bk_off, bk_sz = chunks(template_es)["BKHD"]
    bkhd = bytearray(template_es[bk_off:bk_off + bk_sz])
    struct.pack_into("<I", bkhd, 4, bank_id)
    plugin = 0x00010001
    if wem is None:
        wem = pcm_wem(pcm, rate)
    else:   # a ready-made WEM (e.g. the game's own Vorbis): keep the template's source plugin
        plugin = struct.unpack_from('<I', objs[template_sound][1], 0)[0]
    # sound clone: new id (hirc header is type u8, size u32, id u32), PCM plugin, our media, parent id zeroed (it was an actor-mixer of the template bank)
    _, sb, _ = objs[template_sound]
    sb = bytearray(sb)
    old_parent = None
    for i in range(0, len(sb) - 3):
        v = struct.unpack_from("<I", sb, i)[0]
        if v in objs and v != template_sound and objs[v][0] == 7:
            old_parent = i
    struct.pack_into("<IBII", sb, 0, plugin, 0, media_id, len(wem))
    if pitch_cents is not None and old_parent is not None:
        # property bundle follows the parent id: u8 bit vector, u8 cProps, cProps x u8 ids, cProps x f32 values. Add id 2 (Pitch, cents) (ids stay sorted).
        pb = old_parent + 4
        n = sb[pb + 1]
        ids = list(sb[pb + 2:pb + 2 + n])
        vals = list(struct.unpack_from("<%df" % n, sb, pb + 2 + n))
        d = dict(zip(ids, vals)); d[2] = pitch_cents
        ks = sorted(d)
        sb = sb[:pb + 1] + bytes([len(ks)]) + bytes(ks) + struct.pack("<%df" % len(ks), *[d[k] for k in ks]) + sb[pb + 2 + n + 4 * n:]
    # the parent actor-mixer is KEPT: it lives in a startup-loaded bank and supplies the output bus; zeroing it gave a post that was accepted but silent
    sound = bytes([2]) + struct.pack("<I", len(sb) + 4) + struct.pack("<I", sound_id) + bytes(sb)
    _, ab, _ = objs[template_action]
    ab = bytearray(ab)
    struct.pack_into("<I", ab, 2, sound_id)
    struct.pack_into("<I", ab, len(ab) - 4, bank_id)   # a Play action ends with fadeCurve u8 + the id of the BANK that holds its target: ours, not the template's
    action = bytes([3]) + struct.pack("<I", len(ab) + 4) + struct.pack("<I", action_id) + bytes(ab)
    eb = bytes([1]) + struct.pack("<I", action_id)
    event = bytes([4]) + struct.pack("<I", len(eb) + 4) + struct.pack("<I", event_id) + eb
    hirc_body = struct.pack("<I", 3) + sound + action + event
    # Wwise media must start on the bank's alignment (BKHD says 16) in ABSOLUTE terms: the DIDX offsets are relative to the DATA payload, whose own
    # position in the file is 8 + len(BKHD) + 8 + len(DIDX) + 8; the authoring tool pads the first media so that sum is aligned (the game's mgc_skill_m has
    # its DATA at 3312 = 16 * 207; ours was at 60 and crashed / loaded nothing)
    data_start = 8 + len(bkhd) + 8 + 12 + 8
    pad = (-data_start) % 16
    didx = struct.pack("<III", media_id, pad, len(wem))
    return (b"BKHD" + struct.pack("<I", len(bkhd)) + bytes(bkhd) + b"DIDX" + struct.pack("<I", len(didx)) + didx +
            b"DATA" + struct.pack("<I", pad + len(wem)) + b"\0" * pad + wem + b"HIRC" + struct.pack("<I", len(hirc_body)) + hirc_body)


def build_event_banks(template_es: bytes, template_m: bytes, template_sound: int, template_action: int, pcm: bytes, bank_id: int, media_bank_id: int,
                      event_id: int, action_id: int, sound_id: int, media_id: int, rate: int = 48000, wem: bytes | None = None, pitch_cents: float | None = None) -> tuple:
    """Like build_event_bank but SPLIT the way every game bank is: (`_es` = BKHD + HIRC, `_m` = BKHD + DIDX + DATA). The game never ships a bank with
    both (checked 2026-11-03 over its eqit / it / sm / ring / lantern / gm / harpy banks), so the loader may rely on it."""
    one = build_event_bank(template_es, template_sound, template_action, pcm, bank_id, event_id, action_id, sound_id, media_id, rate, wem, pitch_cents)
    c = chunks(one)
    hirc_chunk = one[c["HIRC"][0] - 8:c["HIRC"][0] + c["HIRC"][1]]
    bk = lambda bid, tmpl: (lambda off, sz: (lambda b: b"BKHD" + struct.pack("<I", len(b)) + bytes(b))(bytearray(tmpl[off:off + sz])))(*chunks(tmpl)["BKHD"])
    es_bk = bytearray(chunks(template_es)["BKHD"][1] and template_es[chunks(template_es)["BKHD"][0]:chunks(template_es)["BKHD"][0] + chunks(template_es)["BKHD"][1]])
    struct.pack_into("<I", es_bk, 4, bank_id)
    es = b"BKHD" + struct.pack("<I", len(es_bk)) + bytes(es_bk) + hirc_chunk
    m_bk = bytearray(template_m[chunks(template_m)["BKHD"][0]:chunks(template_m)["BKHD"][0] + chunks(template_m)["BKHD"][1]])
    struct.pack_into("<I", m_bk, 4, media_bank_id)
    wem = wem if wem is not None else pcm_wem(pcm, rate)
    data_start = 8 + len(m_bk) + 8 + 12 + 8
    pad = (-data_start) % 16
    didx = struct.pack("<III", media_id, pad, len(wem))
    mb = (b"BKHD" + struct.pack("<I", len(m_bk)) + bytes(m_bk) + b"DIDX" + struct.pack("<I", len(didx)) + didx +
          b"DATA" + struct.pack("<I", pad + len(wem)) + b"\0" * pad + wem)
    return es, mb


def fnv(name: str) -> int:
    """Wwise id of a string (bank name, event name...): 32-bit FNV-1 of the lower-cased text (verified: 'mgc_skill_es' = 0x90505041 = that bank's BKHD id)."""
    h = 2166136261
    for c in name.lower().encode():
        h = ((h * 16777619) & 0xFFFFFFFF) ^ c
    return h


def build_layered_banks(template_es: bytes, layers: list, bank_id: int, media_bank_id: int, event_id: int, template_m: bytes) -> tuple:
    """A NEW (`_es`, `_m`) bank pair whose event plays several layers at once, each a clone of a game sound + play action with its own (game) WEM and
    Pitch property. layers: [{'sound': template sound id, 'action': template action id, 'wem': bytes, 'pitch_cents': float|None,
    'sound_id': int, 'action_id': int, 'media_id': int}]. The sounds keep their parent actor-mixer (it supplies the output bus). Verified in game for one layer."""
    objs = hirc(template_es)
    hp = b""
    for L in layers:
        _, sb, _ = objs[L["sound"]]
        sb = bytearray(sb)
        parent = None
        for i in range(0, len(sb) - 3):
            v = struct.unpack_from("<I", sb, i)[0]
            if v in objs and v != L["sound"] and objs[v][0] == 7:
                parent = i
        plugin = struct.unpack_from("<I", sb, 0)[0]
        struct.pack_into("<IBII", sb, 0, plugin, 0, L["media_id"], len(L["wem"]))
        if L.get("pitch_cents") is not None and parent is not None:
            pb = parent + 4
            n = sb[pb + 1]
            d = dict(zip(sb[pb + 2:pb + 2 + n], struct.unpack_from("<%df" % n, sb, pb + 2 + n)))
            d[2] = L["pitch_cents"]
            ks = sorted(d)
            sb = sb[:pb + 1] + bytes([len(ks)]) + bytes(ks) + struct.pack("<%df" % len(ks), *[d[k] for k in ks]) + sb[pb + 2 + n + 4 * n:]
        hp += bytes([2]) + struct.pack("<I", len(sb) + 4) + struct.pack("<I", L["sound_id"]) + bytes(sb)
        _, ab, _ = objs[L["action"]]
        ab = bytearray(ab)
        struct.pack_into("<I", ab, 2, L["sound_id"])
        struct.pack_into("<I", ab, len(ab) - 4, bank_id)
        hp += bytes([3]) + struct.pack("<I", len(ab) + 4) + struct.pack("<I", L["action_id"]) + bytes(ab)
    eb = bytes([len(layers)]) + b"".join(struct.pack("<I", L["action_id"]) for L in layers)
    hp += bytes([4]) + struct.pack("<I", len(eb) + 4) + struct.pack("<I", event_id) + eb
    hirc_chunk = b"HIRC" + struct.pack("<I", 4 + len(hp) - 0) + struct.pack("<I", 2 * len(layers) + 1) + hp
    c = chunks(template_es)
    es_bk = bytearray(template_es[c["BKHD"][0]:c["BKHD"][0] + c["BKHD"][1]]); struct.pack_into("<I", es_bk, 4, bank_id)
    es = b"BKHD" + struct.pack("<I", len(es_bk)) + bytes(es_bk) + hirc_chunk
    cm = chunks(template_m)
    m_bk = bytearray(template_m[cm["BKHD"][0]:cm["BKHD"][0] + cm["BKHD"][1]]); struct.pack_into("<I", m_bk, 4, media_bank_id)
    data_start = 8 + len(m_bk) + 8 + 12 * len(layers) + 8
    pad = (-data_start) % 16
    didx, data, off = b"", b"\0" * pad, pad
    for L in layers:
        w = L["wem"] + b"\0" * ((-len(L["wem"])) % 16)   # keep every media 16-aligned
        didx += struct.pack("<III", L["media_id"], off, len(L["wem"]))
        data += w; off += len(w)
    mb = b"BKHD" + struct.pack("<I", len(m_bk)) + bytes(m_bk) + b"DIDX" + struct.pack("<I", len(didx)) + didx + b"DATA" + struct.pack("<I", len(data)) + data
    return es, mb
