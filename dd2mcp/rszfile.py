"""Decode RE Engine RSZ blocks (serialized object graphs inside .user, .scn, .pfb, .motfsm2/.bhvt and friends).
Verified on DD2 build 6ace5933 motion FSMs, 2026-10-31.

Layout: header 'RSZ\\0', version, objectCount, instanceCount, userdataCount, reserved, then u64 instanceOffset,
dataOffset, userdataOffset (relative to the block). objectCount i32 root instance ids follow the header; instance
infos are (typeHash u32, crc u32) with instance 0 = null; userdata infos are (instanceId i32, typeHash u32,
pathOffset u64). Instance data is packed in instance order (userdata instances have none; children come before
their parents); each field is aligned to its own alignment, arrays are a 4-aligned u32 count followed by aligned
elements, strings and resource paths a 4-aligned u32 UTF-16 length (incl. NUL) and the characters, Object/UserData
fields an i32 instance id. Type hashes are murmur3(utf-8 name, 0xFFFFFFFF) (murmur.py).

Field layouts: no single source matches the installed game (2026-10-31). kagenocookie's REE-Lib-Resources template
(MIT, updated 2026) lacks fields added since (app.HumanUpperBodyCarryMotion.IsUseCaughtNearlyEscapeMotion), layouts
generated from the live game (rszgen.py) include some fields the files don't carry
(app.SetHumanState.IsPreventFallOffStep), alphaZomega's 2024 dump is older still. So every type keeps all candidate
layouts and the decoder searches the choices: decode with the current picks; when an instance stops being
plausible (forward reference, bool byte > 1, denormal float, absurd string/array), switch the most recent open
choice made before it to its next layout and decode again. Settled choices are cached in CHOICES."""
import json
import struct

from . import rsz

TEMPLATE = rsz.DUMP.parent / "rsz_template_dd2.json"
TEMPLATE_URL = "https://raw.githubusercontent.com/kagenocookie/REE-Lib-Resources/master/dd2/rsz_template.json"
LIVE = rsz.DUMP.parent / "rsz_live_dd2.json"
CHOICES = rsz.DUMP.parent / "rsz_choices_dd2.json"

_cands = None
_choice = None
last_partial = None  # instances decoded before the last failure (debugging layouts)


def candidates() -> dict:
    """type hash -> list of distinct layouts {"name", "fields", "source"} (template, live, old dump order)."""
    global _cands, _choice
    if _cands is None:
        _cands = {}
        for src, tag in ((TEMPLATE, "template"), (LIVE, "live"), (rsz.DUMP, "dump2024")):
            if not src.exists():
                continue
            for k, v in json.loads(src.read_text()).items():
                if not v.get("name"):
                    continue
                lst = _cands.setdefault(int(k, 16), [])
                sig = [(f["name"], f["type"], f.get("array")) for f in v.get("fields", [])]
                if all([(f["name"], f["type"], f.get("array")) for f in c["fields"]] != sig for c in lst):
                    lst.append({"name": v["name"], "fields": v.get("fields", []), "source": tag})
        _choice = {int(k, 16): v for k, v in json.loads(CHOICES.read_text()).items()} if CHOICES.exists() else {}
    return _cands


def types() -> dict:
    """type hash -> the chosen (else first) layout."""
    c = candidates()
    return {h: lst[_choice.get(h, 0)] if _choice.get(h, 0) < len(lst) else lst[0] for h, lst in c.items()}


def save_choices():
    if _choice is not None:
        CHOICES.write_text(json.dumps({f"{k:x}": v for k, v in sorted(_choice.items())}, indent=0))


class Ref(int):
    """An Object/UserData field: index of another instance in the same block."""
    def __repr__(self):
        return f"@{int(self)}"


class Bad(ValueError):
    pass


def _align(p, a):
    return (p + a - 1) & ~(a - 1) if a > 1 else p


_SCALARS = {"S8": "b", "U8": "B", "S16": "h", "U16": "H", "S32": "i", "U32": "I", "S64": "q",
            "U64": "Q", "F32": "f", "F64": "d"}


def _value(d, p, f):
    t, size = f["type"], f["size"]
    if t in ("String", "Resource"):
        p = _align(p, 4)
        n = struct.unpack_from("<I", d, p)[0]
        if n > 2048:
            raise Bad(f"{f['name']}: string of {n} chars")
        s = d[p + 4:p + 4 + n * 2].decode("utf-16-le", "replace").rstrip("\x00")
        return s, p + 4 + n * 2
    p = _align(p, f["align"])
    if t in ("Object", "UserData"):
        return Ref(struct.unpack_from("<i", d, p)[0]), p + 4
    if t == "Bool":
        b = d[p]
        if b > 1:
            raise Bad(f"{f['name']}: bool byte {b}")
        return bool(b), p + 1
    if t in _SCALARS:
        v = struct.unpack_from("<" + _SCALARS[t], d, p)[0]
        if t == "F32" and v != 0 and abs(v) < 1e-30:
            raise Bad(f"{f['name']}: denormal float")
        return v, p + size
    if t == "Guid":
        b = d[p:p + 16]
        return f"{b[3::-1].hex()}-{b[5:3:-1].hex()}-{b[7:5:-1].hex()}-{b[8:10].hex()}-{b[10:16].hex()}", p + 16
    if t in ("Vec2", "Float2", "Vec3", "Float3", "Position", "Vec4", "Float4", "Quaternion", "Range"):
        return tuple(round(x, 4) for x in struct.unpack_from(f"<{size // 4}f", d, p)), p + size
    if t == "Color":
        return "#" + d[p:p + 4].hex(), p + 4
    return d[p:p + size].hex(), p + size


def _fields(d, p, layout, index, end):
    fields = {}
    for f in layout["fields"]:
        if f.get("array"):
            p = _align(p, 4)
            n = struct.unpack_from("<I", d, p)[0]
            if n > 100000:
                raise Bad(f"{f['name']}: array of {n}")
            p += 4
            vals = []
            for _ in range(n):
                v, p = _value(d, p, f)
                vals.append(v)
            fields[f["name"]] = vals
        else:
            fields[f["name"]], p = _value(d, p, f)
        if p > end:
            raise Bad("ran past the block")
    for k, v in fields.items():  # children are serialized before their parents
        for r in (v if isinstance(v, list) else [v]):
            if isinstance(r, Ref) and not 0 <= r < index:
                raise Bad(f"{k} references instance {int(r)} (must be < {index})")
    return fields, p


class Block:
    """One parsed RSZ block: .instances = [(type name, {field: value})] (index 0 = None), .roots, .userdata."""

    def __init__(self, data: bytes, start: int = 0, end: int | None = None):
        global last_partial
        d = self.d = data
        magic, ver, nobj, ninst, nud, _ = struct.unpack_from("<4sIiiii", d, start)
        if magic != b"RSZ\x00":
            raise ValueError(f"no RSZ block at {start:#x}")
        inst_off, data_off, ud_off = struct.unpack_from("<QQQ", d, start + 0x18)
        self.version = ver
        self.roots = list(struct.unpack_from(f"<{nobj}i", d, start + 0x30))
        self.hashes = [struct.unpack_from("<I", d, start + inst_off + 8 * i)[0] for i in range(ninst)]
        self.userdata = {}
        for i in range(nud):
            iid, th, po = struct.unpack_from("<iIQ", d, start + ud_off + 16 * i)
            e = d.index(b"\x00\x00", start + po)
            e += (e - (start + po)) % 2
            self.userdata[iid] = d[start + po:e].decode("utf-16-le", "replace")
        self.end_limit = end or len(d)
        C = candidates()
        missing = {f"{h:08x}" for h in self.hashes[1:] if h not in C}
        missing -= {f"{self.hashes[i]:08x}" for i in self.userdata}
        if missing:
            raise ValueError(f"types not in any layout source: {sorted(missing)} (rszgen.generate with their names)")
        self._start, self._data_off = start, data_off
        assign, tried, failures = {}, {}, 0
        while True:
            try:
                self._decode(assign)
                break
            except Bad as e:
                failures += 1
                i, first = e.args[1], e.args[2]
                # flip the most recent open choice made at or before the failing instance
                flip = next((h for h, at in sorted(first.items(), key=lambda x: -x[1])
                             if at <= i and len(tried.setdefault(h, {assign[h]})) < len(C[h])), None)
                if flip is None or failures > 400:
                    last_partial = self.instances
                    prev = self.instances[-1] if len(self.instances) > 1 else None
                    raise ValueError(f"instance {i} {C[self.hashes[i]][0]['name']}: {e.args[0]} (previous: "
                                     f"{prev[0] if prev else None}; {failures} layout retries)") from None
                nxt = next(k for k in range(len(C[flip])) if k not in tried[flip])
                tried[flip].add(nxt)
                cut = first[flip]
                for h, at in first.items():  # choices made after the flipped one start over
                    if at > cut:
                        assign.pop(h, None)
                        tried.pop(h, None)
                assign[flip] = nxt
        for h, k in assign.items():
            _choice[h] = k
        self.retries = failures
        save_choices()

    def _decode(self, assign):
        """Decode every instance with the layout choices in `assign` (open ones take the cached choice, else the
        first candidate). Raises Bad(message, failing index, {type: first index decided})."""
        C = candidates()
        self.instances = [None]
        first = {}
        p = self._start + self._data_off
        for i in range(1, len(self.hashes)):
            h = self.hashes[i]
            lst = C[h]
            if i in self.userdata:
                self.instances.append((lst[0]["name"], {"_path": self.userdata[i]}))
                continue
            if len(lst) > 1:
                if h not in assign:
                    assign[h] = _choice.get(h, 0) if _choice.get(h, 0) < len(lst) else 0
                first.setdefault(h, i)
            lay = lst[assign.get(h, 0)]
            try:
                f, p = _fields(self.d, p, lay, i, self.end_limit)
            except Bad as e:
                raise Bad(str(e), i, first) from None
            self.instances.append((lay["name"], f))
        self.end = p

    def show(self, i: int, depth: int = 2, indent: str = "") -> str:
        """Instance i and the objects it references, `depth` levels deep."""
        if i <= 0 or i >= len(self.instances):
            return f"{indent}null"
        name, fields = self.instances[i]
        out = [f"{indent}[{i}] {name}"]
        for k, v in fields.items():
            refs = [v] if isinstance(v, Ref) else [x for x in v if isinstance(x, Ref)] if isinstance(v, list) else []
            if refs and depth > 0:
                out.append(f"{indent}  {k}:")
                out += [self.show(r, depth - 1, indent + "    ") for r in refs]
            else:
                out.append(f"{indent}  {k} = {v!r}")
        return "\n".join(out)


def blocks(data: bytes) -> list[int]:
    """Offsets of every RSZ block in a file."""
    out, p = [], data.find(b"RSZ\x00")
    while p >= 0:
        out.append(p)
        p = data.find(b"RSZ\x00", p + 4)
    return out
