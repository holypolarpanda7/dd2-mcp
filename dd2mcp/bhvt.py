"""Behavior tree reader: .bhvt and the BHVT embedded in .motfsm2 (DD2 motfsm2 version 45 = BHVT 42).

Record layout follows kagenocookie's RE-Engine-Lib BhvtFile.cs (MIT); verified on DD2 ch000000(_upperbody).motfsm2
2026-10-31 (every node record parses and the node table ends exactly at the action object table).

Header (after 'BHVT', hash, 4 null bytes): u64 offsets relative to the BHVT start: nodes, actions, selectors,
selectorCallers, conditions, transitionEvents, expressionTreeConditions, staticActions, staticSelectorCallers,
staticConditions, staticTransitionEvents, staticExpressionTreeConditions, strings, resourcePaths, userdataPaths,
variables, baseVariables, referencePrefabGameObjects.
Node: id u32, exId u32, nameOffset i32 (UTF-16 chars after the pool's u32 length), parent (id, exId), children
(count; ids[], exIds[], conditionIds[]), selectorId i32, selectorCallers (count; ids[]), selectorCallerConditionId,
actions (count; ids[], exIds[]), priority i32, attributes u16 (0x20 = FSM node: then workFlags u16, nameHash,
fullnameHash (murmur3 of the UTF-16 name / dotted path), tags (count; u32[]), isBranch u8, isEnd u8; else 2 null
bytes), states (count; per state eventCount + eventIds[]; then targetIds[], conditionIds[], transitionMapIds[],
targetExIds[], stateEx[]), transitions (count; per transition eventCount + events[]; then startNodeIds[],
conditionIds[], startNodeExIds[]), unless attributes & 4 (reference tree): allStates (count; targetIds[],
conditionIds[], transitionMapIds[], targetExIds[], attributes[]), referenceTreePathOffset i32.
Condition/event/caller ids are BHVTId: u16 index, u8, u8 type (64 = in the static block); 0xFFFF index = none.
Actions are matched by action ID (field v1_ID), consuming duplicates in order (motfsm2 reuses IDs)."""
import struct

from . import rszfile

ATTR_FSM, ATTR_REFTREE = 0x20, 0x4
SECTIONS = ["nodes", "actions", "selectors", "selectorCallers", "conditions", "transitionEvents", "exprConditions",
            "staticActions", "staticSelectorCallers", "staticConditions", "staticTransitionEvents",
            "staticExprConditions", "strings", "resourcePaths", "userdataPaths", "variables", "baseVariables",
            "prefabRefs"]


class Ref:
    """A BHVTId: (block, index) into the conditions/events/callers RSZ blocks."""
    def __init__(self, v):
        self.index, self.static = v & 0xFFFF, (v >> 24) == 64

    @property
    def none(self):
        return self.index == 0xFFFF


class Tree:
    def __init__(self, data: bytes):
        b = data.find(b"BHVT")
        if b < 0:
            raise ValueError("no BHVT block")
        self.d, self.base = data, b
        offs = struct.unpack_from("<18Q", data, b + 12)
        self.off = {k: b + o for k, o in zip(SECTIONS, offs)}
        self.nodes = self._read_nodes()
        self.by_id = {}
        for n in self.nodes:
            self.by_id.setdefault(n["id"], n)
        self._blocks = {}
        for n in self.nodes:  # full names
            n["full"] = self._full(n)

    # -- raw reading --------------------------------------------------------------------------------------------
    def _read_nodes(self):
        d, p = self.d, self.off["nodes"]
        u32 = lambda: struct.unpack_from("<I", d, p)[0]
        count = struct.unpack_from("<I", d, p)[0]
        p += 4
        pool = self.off["strings"] + 4
        nodes = []

        def arr(n, k):
            nonlocal p
            cols = [list(struct.unpack_from(f"<{n}I", d, p + 4 * n * c)) for c in range(k)]
            p += 4 * n * k
            return cols

        def take(fmt):
            nonlocal p
            v = struct.unpack_from("<" + fmt, d, p)
            p += struct.calcsize("<" + fmt)
            return v if len(v) > 1 else v[0]

        for _ in range(count):
            n = {}
            n["id"], n["ex"], n["nameOffset"], n["parent"], n["parentEx"] = take("IIiII")
            c = take("i")
            ids, exs, conds = arr(c, 3) if c > 0 else ([], [], [])
            n["children"] = [{"id": a, "ex": e, "cond": Ref(k)} for a, e, k in zip(ids, exs, conds)]
            n["selector"] = take("i")
            c = take("i")
            n["callers"] = [Ref(x) for x in (arr(c, 1)[0] if c > 0 else [])]
            n["callerCond"] = Ref(take("I"))
            c = take("i")
            ids, exs = arr(c, 2) if c > 0 else ([], [])
            n["actions"] = list(zip(ids, exs))
            n["priority"] = take("i")
            n["attr"] = take("H")
            if n["attr"] & ATTR_FSM:
                n["workFlags"], n["nameHash"], n["fullHash"] = take("HII")
                c = take("i")
                n["tags"] = arr(c, 1)[0] if c > 0 else []
                n["isBranch"], n["isEnd"] = take("BB")
            else:
                take("H")
                n["tags"] = []
            c = take("i")
            events = []
            for _ in range(max(c, 0)):
                k = take("i")
                events.append([Ref(x) for x in (arr(k, 1)[0] if k > 0 else [])])
            cols = arr(c, 5) if c > 0 else [[]] * 5
            n["states"] = [{"events": events[i], "target": cols[0][i], "cond": Ref(cols[1][i]), "map": cols[2][i],
                            "targetEx": cols[3][i], "stateEx": cols[4][i]} for i in range(max(c, 0))]
            c = take("i")
            tev = []
            for _ in range(max(c, 0)):
                k = take("i")
                tev.append(arr(k, 1)[0] if k > 0 else [])
            cols = arr(c, 3) if c > 0 else [[]] * 3
            n["transitions"] = [{"events": tev[i], "start": cols[0][i], "cond": Ref(cols[1][i]), "startEx": cols[2][i]}
                                for i in range(max(c, 0))]
            if not n["attr"] & ATTR_REFTREE:
                c = take("i")
                cols = arr(c, 5) if c > 0 else [[]] * 5
                n["allStates"] = [{"target": cols[0][i], "cond": Ref(cols[1][i]), "map": cols[2][i],
                                   "targetEx": cols[3][i], "attr": cols[4][i]} for i in range(max(c, 0))]
            else:
                n["allStates"] = []
            n["refTree"] = take("i")
            e = d.index(b"\x00\x00", pool + 2 * n["nameOffset"])
            e += (e - (pool + 2 * n["nameOffset"])) % 2
            n["name"] = d[pool + 2 * n["nameOffset"]:e].decode("utf-16-le", "replace")
            nodes.append(n)
        self.nodes_end = p
        return nodes

    def _full(self, n, depth=0):
        if n["parent"] == 0xFFFFFFFF or depth > 64:
            return n["name"]
        par = self.by_id.get(n["parent"])
        if not par or par is n:
            return n["name"]
        pf = par.get("full") or self._full(par, depth + 1)
        return n["name"] if pf == "root" else f"{pf}.{n['name']}"

    # -- RSZ blocks -----------------------------------------------------------------------------------------------
    def block(self, section):
        if section not in self._blocks:
            start = self.off[section]
            nxt = min((o for o in self.off.values() if o > start), default=len(self.d))
            self._blocks[section] = rszfile.Block(self.d, start, nxt) if self.d[start:start + 4] == b"RSZ\x00" else None
        return self._blocks[section]

    def obj(self, section, static_section, ref):
        """The RSZ instance index (in its block) a BHVTId points to: (block, instance index), or None."""
        if ref is None or ref.none:
            return None
        blk = self.block(static_section if ref.static else section)
        if not blk or ref.index >= len(blk.roots):
            return None
        return blk, blk.roots[ref.index]

    def actions_of(self, node):
        """[(block, instance index)] for a node's actions, matched by ID like the game (duplicates in order)."""
        if not hasattr(self, "_action_index"):
            idx = {}
            for sec in ("actions", "staticActions"):
                blk = self.block(sec)
                for r in (blk.roots if blk else []):
                    f = blk.instances[r][1]
                    aid = f.get("v1_ID")
                    if aid is not None:
                        idx.setdefault(aid & 0xFFFFFFFF, []).append((blk, r))
            self._action_index = idx
            self._action_used = {}
        out = []
        for aid, ex in node["actions"]:
            lst = self._action_index.get(aid, [])
            if not lst:
                out.append(None)
                continue
            out.append(lst[min(ex, len(lst) - 1)])
        return out

    # -- expressions ----------------------------------------------------------------------------------------------
    @property
    def uvars(self):
        """The tree's variable files (own, then reference trees'), each with its embedded files."""
        if not hasattr(self, "_uvars"):
            from . import uvar
            self._uvars = uvar.read_uvars(self)
            self.variables = {}
            for root in self._uvars:
                for u in root.walk():
                    for v in u.variables:
                        self.variables.setdefault(v.guid, v)
        return self._uvars

    def var_name(self, guid):
        self.uvars
        v = self.variables.get(guid)
        return v.name if v else None

    def expr_text(self, guid_or_var):
        """A variable's expression graph as readable logic (or None when it has no expression)."""
        from . import uvar
        self.uvars
        v = guid_or_var if hasattr(guid_or_var, "expression") else self.variables.get(guid_or_var)
        if not v or not v.expression:
            return None
        return uvar.render(v.expression, lambda g: self.var_name(g) or f"var:{g[:8]}",
                           lambda g: self._callback_text(g))

    def _callback_text(self, guid):
        for sec in ("exprConditions", "staticExprConditions"):
            blk = self.block(sec)
            for r in (blk.roots if blk else []):
                name, f = blk.instances[r]
                if f.get("v1_GUID") == guid:
                    return describe_obj((blk, r), True, 120)
        return None

    def cond_text(self, ref, static_block=None):
        """A BHVTId condition as one line; ConditionUserVariable expands to its expression logic."""
        o = self.obj("conditions", "staticConditions", ref)
        if not o:
            return "-"
        blk, i = o
        name, f = blk.instances[i]
        if name == "via.behaviortree.ConditionUserVariable":
            self.uvars
            want = "" if f.get("v2_Condition", True) else "NOT "
            g1 = f.get("v1_GUID")
            g3 = f.get("v3_Expression")
            if g3 and g3 != "00000000-0000-0000-0000-000000000000":
                v = self.variables.get(g3)
                body = self.expr_text(v) if v else None
                return f"{want}[{v.name if v else g3[:8]}] {body or '(plain variable)'}"
            v = self.variables.get(g1)
            return f"{want}{v.name if v else 'var:' + str(g1)[:8]}"
        return describe_obj(o, True, 160)

    def _cond_refs(self):
        """Every (node, role, BHVTId) condition link in the tree."""
        for n in self.nodes:
            if not n["callerCond"].none:
                yield n, "selector caller", n["callerCond"]
            for c in n["children"]:
                if not c["cond"].none:
                    yield n, "child " + (self.by_id[c["id"]]["name"] if c["id"] in self.by_id else hex(c["id"])), c["cond"]
            for st in n["states"]:
                if not st["cond"].none:
                    tg = self.by_id.get(st["target"])
                    yield n, "state -> " + (tg["name"] if tg else hex(st["target"])), st["cond"]
            for t in n["transitions"]:
                if not t["cond"].none:
                    yield n, "start", t["cond"]
            for st in n["allStates"]:
                if not st["cond"].none:
                    tg = self.by_id.get(st["target"])
                    yield n, "any-state -> " + (tg["name"] if tg else hex(st["target"])), st["cond"]

    def var_users(self, guid):
        """Where a variable matters: (a) conditions that name it (ConditionUserVariable v1_GUID / v3_Expression),
        (b) variables whose expression references it, and (c) the conditions using those. -> list of text lines."""
        self.uvars
        direct = {}
        for n, role, ref in self._cond_refs():
            o = self.obj("conditions", "staticConditions", ref)
            if o and o[0].instances[o[1]][0] == "via.behaviortree.ConditionUserVariable":
                f = o[0].instances[o[1]][1]
                for g in (f.get("v1_GUID"), f.get("v3_Expression")):
                    if g:
                        direct.setdefault(g, []).append(f"{n['full']}  [{role}]")
        deps = {}
        for g, v in self.variables.items():
            if v.expression:
                for nd in v.expression.nodes.values():
                    if nd.params.get("VariableID"):
                        deps.setdefault(nd.params["VariableID"], []).append(g)
        out = [f"condition: {x}" for x in direct.get(guid, [])]
        for dg in deps.get(guid, []):
            dv = self.variables[dg]
            out.append(f"expression of variable {dv.name}: {self.expr_text(dv)}")
            out += [f"  condition via {dv.name}: {x}" for x in direct.get(dg, [])]
        return out

    def find(self, pattern):
        import re
        rx = re.compile(pattern, re.I)
        return [n for n in self.nodes if rx.search(n["full"])]


def describe_obj(o, fields=True, limit=200):
    if not o:
        return "-"
    blk, i = o
    name, f = blk.instances[i]
    if not fields:
        return name
    keep = {k: v for k, v in f.items() if not k.startswith("v0_") and k not in ("v1_ID",)}
    return f"{name} {str(keep)[:limit]}"


def describe(tree, node, detail=True):
    out = [f"{node['full']}  [id {node['id']:#x}{'/' + str(node['ex']) if node['ex'] else ''}, attr {node['attr']:#x}"
           f"{', branch' if node.get('isBranch') else ''}{', end' if node.get('isEnd') else ''}]"]
    if node["selector"] >= 0:
        blk = tree.block("selectors")
        out.append(f"  selector: {blk.instances[blk.roots[node['selector']]][0] if blk else node['selector']}")
    if not node["callerCond"].none:
        out.append(f"  selector caller condition: {tree.cond_text(node['callerCond'])}")
    for a, o in zip(node["actions"], tree.actions_of(node)):
        out.append(f"  action {a[0]:#x}: {describe_obj(o, detail)}")
    for c in node["children"]:
        ch = tree.by_id.get(c["id"])
        cond = "" if c["cond"].none else "  if " + tree.cond_text(c["cond"])
        out.append(f"  child: {ch['name'] if ch else hex(c['id'])}{cond}")
    for s in node["states"]:
        tg = tree.by_id.get(s["target"])
        cond = "" if s["cond"].none else "  if " + tree.cond_text(s["cond"])
        evs = [describe_obj(tree.obj("transitionEvents", "staticTransitionEvents", e), False) for e in s["events"]]
        out.append(f"  state -> {tg['full'] if tg else hex(s['target'])}{cond}{'  events ' + ', '.join(evs) if evs else ''}")
    for t in node["transitions"]:
        st = tree.by_id.get(t["start"])
        cond = "" if t["cond"].none else "  if " + tree.cond_text(t["cond"])
        out.append(f"  start: {st['name'] if st else hex(t['start'])}{cond}")
    for s in node["allStates"]:
        tg = tree.by_id.get(s["target"])
        cond = "" if s["cond"].none else "  if " + tree.cond_text(s["cond"])
        out.append(f"  any-state -> {tg['full'] if tg else hex(s['target'])}{cond}")
    return "\n".join(out)
