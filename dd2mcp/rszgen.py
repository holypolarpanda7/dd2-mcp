"""Generate RSZ field layouts from the live game, for types the community dumps lack or have stale (2026-10-31:
the installed DD2 is newer than both dumps; e.g. app.SetHumanState gained IsPreventFallOffStep).

Rules (checked against template types): a type's RSZ fields are its native base's fields (via.behaviortree.Action:
v0_Enabled, v1_ID) followed by every instance field flagged 0x4000 in each managed class from the root down;
System.Nullable`1<T> becomes STRUCT_<name>__HasValue (Bool) + STRUCT_<name>__Value; arrays and List`1 become array
fields of their element; references become Object (UserData for via.UserData). Field encodings (type, size, align)
come from the template where it has the same CLR type."""
import json
import re

from . import game, murmur, rsz, rszfile

SERIALIZED = 0x4000
NATIVE_BASE = {  # fallbacks when the template knows no type with this native ancestor
    "via.behaviortree.Action": [("v0_Enabled", "Bool", 1, 1), ("v1_ID", "U32", 4, 4)],
}
_SCALAR = {"System.Boolean": ("Bool", 1, 1), "System.SByte": ("S8", 1, 1), "System.Byte": ("U8", 1, 1),
           "System.Int16": ("S16", 2, 2), "System.UInt16": ("U16", 2, 2), "System.Int32": ("S32", 4, 4),
           "System.UInt32": ("U32", 4, 4), "System.Int64": ("S64", 8, 8), "System.UInt64": ("U64", 8, 8),
           "System.Single": ("F32", 4, 4), "System.Double": ("F64", 8, 8), "System.String": ("String", 4, 4),
           "System.Guid": ("Guid", 16, 8)}
_ENUM = {1: ("U8", 1, 1), 2: ("U16", 2, 2), 4: ("S32", 4, 4), 8: ("S64", 8, 8)}

LUA_EXPORT = r'''
local out, seen = {}, {}
local function vsize(t) local ok, s = pcall(function() return t:get_valuetype_size() end) return ok and s or nil end
local function isenum(t) local ok, r = pcall(function() return t:is_a("System.Enum") end) return ok and r or false end
local function isud(t) local ok, r = pcall(function() return t:is_a("via.UserData") end) return ok and r or false end
local function dump(name)
    if seen[name] then return end
    seen[name] = true
    local td = sdk.find_type_definition(name)
    if not td then out[name] = { missing = true } return end
    local p = td:get_parent_type()
    local fl = {}
    for _, f in ipairs(td:get_fields()) do
        if not f:is_static() then
            local ft = f:get_type()
            local tn = ft:get_full_name()
            local e = { name = f:get_name(), type = tn, flags = f:get_flags(), vt = ft:is_value_type(), size = vsize(ft),
                enum = isenum(ft), ud = isud(ft) }
            local inner = tn:match("^(.+)%[%]$") or tn:match("^System.Collections.Generic.List`1<(.+)>$")
                or tn:match("^System.Nullable`1<(.+)>$")
            if inner then
                local et = sdk.find_type_definition(inner)
                if et then e.elem = inner e.elemvt = et:is_value_type() e.elemsize = vsize(et) e.elemenum = isenum(et) e.elemud = isud(et) end
            end
            fl[#fl + 1] = e
        end
    end
    out[name] = { parent = p and p:get_full_name() or nil, fields = fl }
    if p and p:get_full_name() ~= "System.Object" then dump(p:get_full_name()) end
end
for n in io.lines("rszgen_req.txt") do dump(n) end
fs.write("rszgen_out.json", json.dump_string(out))
local c = 0 for _ in pairs(out) do c = c + 1 end
return c
'''


def _template_encodings() -> dict:
    """CLR type name -> (rsz type, size, align), learned from the template's original_type fields."""
    enc = {}
    for v in rszfile.types().values():
        for f in v.get("fields", []):
            ot = f.get("original_type")
            if ot and f["type"] not in ("Object", "UserData") and not f.get("array"):
                enc.setdefault(ot, (f["type"], f["size"], f["align"]))
    return enc


def _field(name, clr, info, enc):
    """RSZ field entries for one CLR field (Nullable expands to two)."""
    def one(fname, t, isvt, size, isenum, isud, array=False):
        if t in _SCALAR:
            ty, sz, al = _SCALAR[t]
        elif isenum:
            ty, sz, al = _ENUM.get(size or 4, ("S32", 4, 4))
        elif t in enc:
            ty, sz, al = enc[t]
        elif isvt:
            ty, sz, al = "Data", size or 0, min(8, size or 1) if size and size >= 4 else 1
        else:
            ty, sz, al = ("UserData" if isud else "Object"), 4, 4
        return {"name": fname, "type": ty, "size": sz, "align": al, "array": array, "native": False, "original_type": t}

    m = re.match(r"^System\.Nullable`1<(.+)>$", clr)
    if m:
        return [{"name": f"STRUCT_{name}__HasValue", "type": "Bool", "size": 1, "align": 1, "array": False,
                 "native": False, "original_type": "System.Boolean"},
                one(f"STRUCT_{name}__Value", m.group(1), True, info.get("elemsize"), info.get("elemenum"), False)]
    if info.get("elem") and (clr.endswith("[]") or clr.startswith("System.Collections.Generic.List`1<")):
        return [one(name, info["elem"], info.get("elemvt"), info.get("elemsize"), info.get("elemenum"),
                    info.get("elemud"), array=True)]
    return [one(name, clr, info.get("vt"), info.get("size"), info.get("enum"), info.get("ud"))]


_NATIVE_FIELD = re.compile(r"^v\d+_")


def _native_bases(live: dict) -> dict:
    """Native (via.*) ancestor -> its RSZ fields. From the template's own entry for that class when it has one,
    else from the leading vN_ fields of a template type with the same nearest native ancestor (app.Action1Motion's
    native parent carries v2_Motion..v11_PrevMotRefClear, not just Enabled/ID)."""
    T = {v["name"]: v for v in rszfile.types().values() if v.get("source") != "live"}
    bases = {"System.Object": []}
    for name, info in live.items():
        n = info.get("parent") if not info.get("missing") else None
        while n and n in live and not n.startswith("via."):
            n = live[n].get("parent")
        if not n or not n.startswith("via.") or n in bases:
            continue
        if n in T:
            bases[n] = T[n]["fields"]
        elif name in T:
            prefix = []
            for f in T[name]["fields"]:
                if not _NATIVE_FIELD.match(f["name"]):
                    break
                prefix.append(f)
            bases[n] = prefix
    for n, flds in NATIVE_BASE.items():
        bases.setdefault(n, [{"name": a, "type": t, "size": s, "align": al, "array": False, "native": True,
                              "original_type": ""} for a, t, s, al in flds])
    return bases


def build(live: dict) -> dict:
    """{hash hex: {"name", "crc", "fields"}} for every exported managed type whose native base is known."""
    enc = _template_encodings()
    bases = _native_bases(live)
    out = {}
    for name in live:
        if name.startswith("via."):  # native (engine) layouts: keep the template's
            continue
        chain, n = [], name
        while n and n in live and not live[n].get("missing") and not n.startswith("via."):
            chain.insert(0, n)
            n = live[n].get("parent")
        if n not in bases:
            continue
        fields = [dict(f) for f in bases[n]]
        for cls in chain:
            for f in live[cls].get("fields") or []:
                if f["flags"] & SERIALIZED:
                    clean = re.sub(r"^<(.+)>k__BackingField$", r"\1", f["name"])
                    fields += _field(clean, f["type"], f, enc)
        out[f"{murmur.type_hash(name):x}"] = {"name": name, "crc": "0", "fields": fields, "source": "live"}
    return out


def generate(names: list[str]) -> str:
    """Export the layouts of `names` (and their parents) from the running game into rsz_live_dd2.json."""
    (game.REF / "data").mkdir(parents=True, exist_ok=True)
    (game.REF / "data" / "rszgen_req.txt").write_text("\n".join(names))
    res = game.eval_lua(LUA_EXPORT, timeout=60)
    if not res.get("ok"):
        return game.format_result(res)
    live = json.loads((game.REF / "data" / "rszgen_out.json").read_text())
    layouts = build(live)
    old = json.loads(rszfile.LIVE.read_text()) if rszfile.LIVE.exists() else {}
    old.update(layouts)
    rszfile.LIVE.write_text(json.dumps(old, indent=0))
    rszfile._types = None
    return f"exported {len(live)} types, {len(layouts)} layouts written to {rszfile.LIVE} ({len(old)} total)"
