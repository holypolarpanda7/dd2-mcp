"""Does a Wwise trigger loop forever? trigger id -> event (the player's SoundTriggerInfoListData files) -> the event's sounds/containers,
walked downward only (event -> actions -> target -> direct children), checked for HIRC property 0x3A (Loop; value 0 = infinite).
DnD_Classes 2026-11-14: Magick Bolt's flight hum (368110313) played on the caster never stopped, not even with SoundManagerApp.stopAll."""
import re
import struct

from . import pak, rszfile, wwise

PLAYER_LISTS = """HM/PL/pl_se PM/PL/PM_PL WEAR/PL_Foot WEAR/PL_Wear HM/HM_COMON/hm_com_nrd DAMAGE/MEAT_DMG/MEAT_DMG SKILL/JOB01_SKILL/JOB01_SKILL
SKILL/JOB02_SKILL/JOB02_SKILL SKILL/JOB_SKILL_COM/JOB_SKILL_COM SKILL/JOB04_SKILL/JOB04_SKILL SKILL/JOB05_SKILL/JOB05_SKILL SKILL/JOB06_SKILL/JOB06_SKILL
SKILL/JOB07_SKILL/JOB07_SKILL SKILL/JOB08_SKILL/JOB08_SKILL SKILL/JOB09_SKILL/JOB09_SKILL SKILL/JOB10_SKILL/JOB10_SKILL SKILL/MGC_SKILL/MGC_SKILL
COMMON/ABILITY/ABILITY WEP/wp_com/wp_com COMMON/CONDITION/CONDITION DAMAGE/COMMON_DAMAGE""".split()
CONTAINERS = (5, 6, 9, 10)
PARENTS = (5, 6, 7, 9, 10, 11)
_trig, _banks, _parent = {}, {}, {}


def _list_path(l):
    return f"natives/stm/sound/resource/{l.lower()}_triggerinfolistdata.user.3"


def _load(lists):
    for l in lists:
        if any(v[1] == l for vs in _trig.values() for v in vs[:1]) and l in _banks.get("_loaded", set()):
            continue
        f = pak.find(_list_path(l))
        if not f:
            continue
        data = f[1]
        stems = re.findall(r"([A-Za-z0-9_]+)\.sbnk", data.decode("utf-16-le", "ignore"))
        stem = stems[0].lower().replace("_es", "").replace("_m", "") if stems else None
        offs = rszfile.blocks(data)
        for k, off in enumerate(offs):
            b = rszfile.Block(data, off, offs[k + 1] if k + 1 < len(offs) else None)
            for inst in b.instances:
                if inst and inst[0] == "soundlib.SoundTriggerInfo":
                    _trig.setdefault(inst[1]["_TriggerId"] & 0xFFFFFFFF, []).append((inst[1]["_EventId"] & 0xFFFFFFFF, l, stem))
        _banks.setdefault("_loaded", set()).add(l)


def _hirc(stem):
    if stem not in _banks:
        f = pak.find(f"natives/stm/sound/wwise/{stem}_es.sbnk.1.x64")
        _banks[stem] = wwise.hirc(f[1]) if f else None
    return _banks[stem]


def _props_loop(objs, oid):
    """(has parent, loop value or None) from the property bundle that follows the parent id."""
    b = objs[oid][1]
    for i in range(0, min(len(b) - 3, 80)):
        v = struct.unpack_from("<I", b, i)[0]
        if v in objs and v != oid and objs[v][0] in PARENTS:
            pb = i + 4
            n = b[pb + 1]
            ids = list(b[pb + 2:pb + 2 + n])
            return v, (struct.unpack_from("<I", b, pb + 2 + n + 4 * ids.index(0x3A))[0] if 0x3A in ids else None)
    return None, None


def _loops(objs, oid, depth=0):
    if oid not in objs or depth > 6:
        return []
    t, b, _ = objs[oid]
    if t == 2:
        lp = _props_loop(objs, oid)[1]
        return [(oid, lp)] if lp is not None else []
    out, kids = [], []
    for i in range(0, len(b) - 3):
        v = struct.unpack_from("<I", b, i)[0]
        if v in objs and v != oid:
            vt = objs[v][0]
            if (t == 4 and vt == 3) or (t == 3 and vt in (2,) + CONTAINERS) or (t in CONTAINERS and _props_loop(objs, v)[0] == oid):
                kids.append(v)
    for k in dict.fromkeys(kids):
        out += _loops(objs, k, depth + 1)
    if t in CONTAINERS:
        lp = _props_loop(objs, oid)[1]
        if lp is not None:
            out.append((oid, lp))
    return out


def audit(ids, lists=None) -> str:
    _load(lists or PLAYER_LISTS)
    rows = []
    for tid in ids:
        ents = _trig.get(tid & 0xFFFFFFFF)
        if not ents:
            rows.append(f"{tid}: not in the trigger lists")
            continue
        for ev, l, stem in ents[:2]:
            objs = _hirc(stem) if stem else None
            if ev == 0xFFFFFFFF:
                rows.append(f"{tid}: {l}: no event")
            elif not objs or ev not in objs:
                rows.append(f"{tid}: event {ev} not in bank {stem}")
            else:
                lp = _loops(objs, ev)
                tag = "LOOP FOREVER" if any(v == 0 for _, v in lp) else ("loops x" + ",".join(str(v) for _, v in lp) if lp else "one-shot")
                rows.append(f"{tid}: event {ev} [{stem}] {l} -> {tag}")
    return "\n".join(rows)
