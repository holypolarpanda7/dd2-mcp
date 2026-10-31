"""User variable files (.uvar, and the ones embedded in behavior trees) with their expression graphs.

Layout follows kagenocookie's RE-Engine-Lib UVarFile.cs (MIT); verified on DD2 player motion FSMs 2026-10-31.
All offsets are relative to `base` (0 for a standalone .uvar, the BHVT start inside a .bhvt/.motfsm2).

Header: u32 version (3), u32 'uvar', u64 stringsOffset, dataOffset, embedsInfoOffset, hashInfoOffset, (u64 only when
version < 3), u32 name hash, u16 variableCount, u16 embedCount; variable records start at dataOffset, 16-aligned.
Variable (48 bytes): guid, u64 nameOffset (UTF-16), u64 valueOffset, u64 expressionOffset, u32 type | flags << 24,
u32 nameHash. Expression: u64 nodesOffset, u64 connectionsOffset, u16 nodeCount, u16 connectionCount, u16
outputNodeId. Node (32 bytes): u64 nameOffset (ASCII), u64 parametersOffset, 8 null, u16 id, u16 parameterCount.
Parameter: u32 nameHash, u32 type (6 u32, 7 i32, 10 f32, 20 guid), then the value (a guid is at a u64 offset that
points just after it), padded to 16. Connection: i16 target, targetInputSlot, source, sourceOutputSlot."""
import struct

TYPE_NAMES = {0: "Unknown", 1: "Enum", 2: "Boolean", 3: "Int8", 4: "Uint8", 5: "Int16", 6: "Uint16", 7: "Int32",
              8: "Uint32", 9: "Int64", 10: "Uint64", 11: "Single", 12: "Double", 13: "C8", 14: "C16", 15: "String",
              16: "Trigger", 17: "Vec2", 18: "Vec3", 19: "Vec4", 20: "Matrix", 21: "GUID", 22: "Num"}
_FMT = {1: "i", 2: "?", 3: "b", 4: "B", 5: "h", 6: "H", 7: "i", 8: "I", 9: "q", 10: "Q", 11: "f", 12: "d",
        17: "2f", 18: "3f", 19: "4f", 20: "16f"}
FLAG_CONST, FLAG_RANGE = 1 << 5, 1 << 6

# Parameter name hashes (murmur3 of the ASCII name; constants from RE-Engine-Lib).
PARAM = {3248822590: "Operation", 4253840158: "Value", 2243404683: "Count", 3595450026: "VariableID",
         2711667410: "CallbackGuid", 1916175859: "CallbackID", 2855057449: "CallbackFunctionID", 3730297036: "Min",
         3365636864: "Max"}
INPUTS = {"NotNode": ["Input"], "AbsNode": ["Input"], "CompareNode": ["Arg1", "Arg2"], "LogicNode": ["Arg1", "Arg2"],
          "CalculateNode": ["Arg1", "Arg2"], "ClampNode": ["Value"], "LerpNode": ["Value", "Start", "End"]}

# The Operation enums (via.userdata.CompareNode.Operation, ...) are native-only: absent from the managed type
# database, the community type caches and the editor sources, so the numbers are pinned from game data
# (2026-10-31, all 414 FSM/uvar files: 102 expressions). CONFIRMED means only one reading is possible; LIKELY means
# the usage fits one reading best (marked with ? in rendered text).
#   CompareNode 0: SitMotion op 2001 selects the "stand to the left" branch: equality.        CONFIRMED  ==
#   LogicNode 1:   (SitMotion op 2000) op (SitMotion op 2700) = StandFront, either motion:    CONFIRMED  ||
#   CompareNode 2: SpawnNum_Talos/Golem/Ruins = (velocity length op 10.5 / 8 / 3): thresholds  LIKELY     >
#   CompareNode 3: Large = (2 op size) -> size above 2; EmitRange uses (PointNum op 2)         LIKELY     <
#   CompareNode 4: SpawnNum = (velocity length op 0.15), a small threshold                     LIKELY     >=
#   CalculateNode 3: (ImpactForce op 10), (LengthX op 4): normalising before a lerp            LIKELY     /
OPERATIONS = {
    "CompareNode": {0: "==", 2: ">?", 3: "<?", 4: ">=?"},
    "LogicNode": {1: "||"},
    "CalculateNode": {3: "/?"},
}


def _guid(b):
    return f"{b[3::-1].hex()}-{b[5:3:-1].hex()}-{b[7:5:-1].hex()}-{b[8:10].hex()}-{b[10:16].hex()}"


class Variable:
    def __init__(self, d, base, p):
        self.guid = _guid(d[p:p + 16])
        no, vo, eo, tb, self.name_hash = struct.unpack_from("<QQQII", d, p + 16)
        self.type, self.flags = tb & 0xFFFFFF, tb >> 24
        self.name = _wstr(d, base + no)
        self.value = _value(d, base, base + vo, self.type, self.flags) if vo else None
        self.expression = Expression(d, base, base + eo) if eo else None

    @property
    def type_name(self):
        return TYPE_NAMES.get(self.type, str(self.type)) + ("[3]" if self.flags & FLAG_RANGE else "")


class Node:
    def __init__(self, d, base, p):
        no, po, _, self.id, pc = struct.unpack_from("<QQQHH", d, p)
        self.type = _astr(d, base + no)
        self.params = {}
        q = base + po
        for _ in range(pc):
            h, t = struct.unpack_from("<II", d, q)
            if t == 20:
                off = struct.unpack_from("<Q", d, q + 8)[0]
                v, q = _guid(d[q + 16:q + 32]), q + 32
            else:
                v = struct.unpack_from({6: "<I", 7: "<i", 10: "<f"}.get(t, "<I"), d, q + 8)[0]
                q += 16
            self.params[PARAM.get(h, str(h))] = v


class Expression:
    def __init__(self, d, base, p):
        no, co, nc, cc, self.output = struct.unpack_from("<QQHHh", d, p)
        self.nodes = {}
        q = base + no
        for _ in range(nc):
            n = Node(d, base, q)
            self.nodes[n.id] = n
            q += 32
        self.connections = [struct.unpack_from("<4h", d, base + co + 8 * i) for i in range(cc)] if co else []
        # inputs[target][slot] = source node id
        self.inputs = {}
        for tgt, slot, src, _out in self.connections:
            self.inputs.setdefault(tgt, {})[slot] = src


class Uvar:
    def __init__(self, data: bytes, offset: int = 0, base: int | None = None):
        d = self.d = data
        self.base = offset if base is None else base
        ver, magic = struct.unpack_from("<II", d, offset)
        if magic != 0x72617675:
            raise ValueError(f"no uvar at {offset:#x}")
        so, do, eo, ho = struct.unpack_from("<QQQQ", d, offset + 8)
        p = offset + 40 + (8 if ver < 3 else 0)
        self.name_hash, nvars, nemb = struct.unpack_from("<IHH", d, p)
        self.name = _wstr(d, self.base + so) if so else ""
        self.variables = [Variable(d, self.base, self.base + do + 48 * i) for i in range(nvars)]
        self.by_guid = {v.guid: v for v in self.variables}
        self.embedded = []
        for i in range(nemb if eo else 0):
            o = struct.unpack_from("<Q", d, self.base + eo + 8 * i)[0]
            self.embedded.append(Uvar(d, self.base + o, self.base + o))

    def walk(self):
        """This file, then every embedded file (recursively)."""
        yield self
        for e in self.embedded:
            yield from e.walk()


def _wstr(d, p):
    e = p
    while d[e:e + 2] != b"\x00\x00":
        e += 2
    return d[p:e].decode("utf-16-le", "replace")


def _astr(d, p):
    return d[p:d.index(b"\x00", p)].decode("ascii", "replace")


def _value(d, base, p, t, flags):
    if t == 13:
        return _astr(d, base + struct.unpack_from("<Q", d, p)[0])
    if t == 14:
        return _wstr(d, base + struct.unpack_from("<Q", d, p)[0])
    if t == 15:
        return _wstr(d, p)
    if t == 21:
        return _guid(d[p:p + 16])
    if t not in _FMT:
        return None
    fmt = _FMT[t]
    if flags & FLAG_RANGE and fmt in "bBhHiIfd?":  # arrays of three
        v = struct.unpack_from("<3" + fmt, d, p)
    else:
        v = struct.unpack_from("<" + fmt, d, p)
    v = tuple(round(x, 5) if isinstance(x, float) else x for x in v)
    return v[0] if len(v) == 1 else v


def read_uvars(tree) -> list:
    """The variable files of a BHVT tree (bhvt.Tree): the tree's own user variables, then its reference trees'."""
    d, b = tree.d, tree.base
    out = [Uvar(d, tree.off["variables"], b)]
    n = struct.unpack_from("<I", d, tree.off["baseVariables"])[0]
    for i in range(n):
        o = struct.unpack_from("<Q", d, tree.off["baseVariables"] + 4 + 8 * i)[0]
        out.append(Uvar(d, b + o, b))
    return out


# --- expression rendering -------------------------------------------------------------------------------------------
def render(expr: Expression, resolve_var=None, resolve_callback=None, node_id=None, depth=0) -> str:
    """The expression graph as one infix-ish string. resolve_var(guid) -> name; resolve_callback(guid) -> text."""
    nid = expr.output if node_id is None else node_id
    n = expr.nodes.get(nid)
    if n is None:
        return "?"
    if depth > 30:
        return "..."
    arg = lambda slot: render(expr, resolve_var, resolve_callback, expr.inputs.get(nid, {}).get(slot), depth + 1) \
        if slot in expr.inputs.get(nid, {}) else "_"
    P = n.params
    if n.type == "ValueNode":
        v = P.get("Value", 0.0)
        return str(int(v)) if float(v).is_integer() else f"{v:g}"
    if n.type == "VariableReferenceNode":
        g = P.get("VariableID")
        return resolve_var(g) if resolve_var else f"var:{g}"
    if n.type == "CallbackNode":
        g = P.get("CallbackGuid")
        text = resolve_callback(g) if resolve_callback and g else None
        return text or f"callback#{P.get('CallbackID')}.{P.get('CallbackFunctionID')}" + (f"[{g}]" if g else "")
    if n.type == "NotNode":
        return f"!({arg(0)})"
    if n.type == "AbsNode":
        return f"|{arg(0)}|"
    if n.type in ("CompareNode", "LogicNode", "CalculateNode"):
        op = OPERATIONS.get(n.type, {}).get(P.get("Operation"), f"op{P.get('Operation')}")
        return f"({arg(0)} {op} {arg(1)})"
    if n.type == "MultiLogicNode":
        op = OPERATIONS.get(n.type, {}).get(P.get("Operation"), f"op{P.get('Operation')}")
        cnt = P.get("Count", 0)
        ins = expr.inputs.get(nid, {})
        parts = [render(expr, resolve_var, resolve_callback, ins[i], depth + 1) if i in ins else "_" for i in range(cnt)]
        return "(" + f" {op} ".join(parts) + ")"
    if n.type == "ClampNode":
        return f"clamp({arg(0)}, {P.get('Min', 0):g}, {P.get('Max', 0):g})"
    if n.type == "LerpNode":
        return f"lerp({arg(0)}, {arg(1)}, {arg(2)})"
    return f"{n.type}({', '.join(f'{k}={v}' for k, v in P.items())})"


# --- evaluation -----------------------------------------------------------------------------------------------------
class Unknown(ValueError):
    """An operation (or node type) whose meaning is not established."""


# The only operations whose meaning the engine itself confirmed (live sweep of SitMotion 2026-10-31); LIKELY ones
# are evaluated too but reported in `notes`.
_CONFIRMED = {("CompareNode", 0), ("LogicNode", 1)}
_COMPARE = {0: lambda a, b: a == b, 2: lambda a, b: a > b, 3: lambda a, b: a < b, 4: lambda a, b: a >= b}
_LOGIC = {1: lambda a, b: bool(a) or bool(b)}
_CALC = {3: lambda a, b: a / b if b else 0.0}


def evaluate(expr: Expression, values: dict, name_of=None, notes=None, node_id=None):
    """Evaluate an expression graph. values: {variable name or guid: number/bool}; name_of(guid) -> name for the
    graph's variable references. Booleans are 1/0. `notes` (a list) receives a line for every inferred operation
    used; raises Unknown for operations/nodes with no known meaning and KeyError for a missing variable value."""
    notes = [] if notes is None else notes
    nid = expr.output if node_id is None else node_id
    n = expr.nodes[nid]
    P, ins = n.params, expr.inputs.get(nid, {})
    arg = lambda slot: evaluate(expr, values, name_of, notes, ins[slot])
    t = n.type
    if t == "ValueNode":
        return P.get("Value", 0.0)
    if t == "VariableReferenceNode":
        g = P["VariableID"]
        nm = name_of(g) if name_of else None
        for k in (nm, g):
            if k in values:
                return values[k]
        raise KeyError(nm or g)
    if t in ("CompareNode", "LogicNode", "CalculateNode"):
        table = {"CompareNode": _COMPARE, "LogicNode": _LOGIC, "CalculateNode": _CALC}[t]
        op = P.get("Operation")
        if op not in table:
            raise Unknown(f"{t} operation {op}")
        if (t, op) not in _CONFIRMED:
            notes.append(f"{t} operation {op} is inferred ({OPERATIONS[t].get(op, '?')})")
        r = table[op](arg(0), arg(1))
        return float(r) if isinstance(r, float) else (1 if r is True else 0 if r is False else r)
    if t == "NotNode":
        return 0 if arg(0) else 1
    if t == "AbsNode":
        return abs(arg(0))
    if t == "ClampNode":
        return max(P.get("Min", 0.0), min(P.get("Max", 0.0), arg(0)))
    raise Unknown(f"{t} node")
