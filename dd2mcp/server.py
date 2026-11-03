"""MCP server: Dragon's Dogma 2 modding through REFramework (live Lua eval, introspection, deploy, saves)."""
import json
import pathlib
import re
import sys

from mcp.server.mcpserver import MCPServer

from . import game, saves

mcp = MCPServer(
    "dd2",
    instructions=(
        "Dragon's Dogma 2 modding via REFramework. dd2_eval runs Lua in the live game through a file bridge; "
        "use phase='update' for anything that changes game state (spawning prefabs, giving items, gold): "
        "the same call made from the frame phase silently does nothing. Helpers on the Lua global D: "
        "D.methods(type, pat), D.fields(type, pat), D.player(), D.player_pos(), D.find_items(name), "
        "D.item_count(id), D.peek/D.poke32(addr, v), D.fnaddr(type, method), D.module_base(). Mod globals (e.g. Bastion's H) are reachable too. Snapshot saves (dd2_saves) "
        "before testing anything that writes game state: autosaves overwrite the play-through."
    ),
)


@mcp.tool()
def dd2_status() -> str:
    """Game process, REFramework revision, bridge state, and the player's position if a save is loaded."""
    pid = game.running()
    lines = [f"game: {'running pid ' + str(pid) if pid else 'not running'}",
             f"install: {game.GAME}",
             f"REFramework: {game.refw_revision() or 'NOT installed'}",
             f"bridge script: {'installed' if game.bridge_installed() else 'missing (dd2_install_bridge)'}"]
    crash = game.crash_info()
    if crash:
        lines.append("CRASH in the last run's log:\n" + crash)
    if pid:
        res = game.eval_lua("return D.version, D.player_pos()", timeout=5)
        lines.append("bridge: " + (game.format_result(res) if res.get("ok") else res.get("err", "?")))
    return "\n".join(lines)


@mcp.tool()
def dd2_eval(code: str, phase: str = "frame", timeout: float = 15) -> str:
    """Run a Lua chunk in the running game; print() output and returned values come back.
    phase='update' runs it inside the game's UpdateBehavior (required for state changes)."""
    return game.format_result(game.eval_lua(code, phase=phase, timeout=timeout))


@mcp.tool()
def dd2_types(type_name: str, kind: str = "methods", filter: str | None = None) -> str:
    """Introspect a managed type: kind 'methods' (signatures) or 'fields', optionally filtered by a substring."""
    fn = "D.methods" if kind == "methods" else "D.fields"
    pat = json.dumps(filter) if filter else "nil"
    res = game.eval_lua(f"local r = {fn}({json.dumps(type_name)}, {pat})\n"
                        "if type(r) == 'table' then for _, l in ipairs(r) do print(l) end else print(r) end")
    return game.format_result(res)


_RESOLVE_LUA = r"""
local function R(expr)
  if expr:match("^[%w_.]+$") then
    if rawget(_G, expr) ~= nil then return rawget(_G, expr) end   -- a Lua global wins over a singleton of that name
    local o = sdk.get_managed_singleton(expr)
    if not o then error("no managed singleton " .. expr .. " (use a Lua expression for anything else)") end
    return o
  end
  local fn, e = load("return " .. expr, "=expr", "t")
  if not fn then error("bad expression: " .. tostring(e)) end
  return fn()
end
"""


def _render_dump(d, indent: str = "") -> list[str]:
    out = [f"{indent}{d['type']}"] if not indent else []
    for k, v in (d.get("fields") or {}).items():
        if isinstance(v, dict):
            out.append(f"{indent}  {k}:")
            out.append(f"{indent}    <{v['type']}>")
            out += _render_dump(v, indent + "    ")
        else:
            out.append(f"{indent}  {k} = {v}")
    return out


@mcp.tool()
def dd2_inspect(expr: str, depth: int = 1, filter: str = "", max_items: int = 4, statics: bool = False,
                max_lines: int = 150) -> str:
    """Dump a live managed object field by field (names, declared types, values; nested objects `depth` levels, collections
    as count + first `max_items`). expr: a manager singleton name ('app.ItemManager') or any Lua expression that yields
    an object ('D.player()', 'D.player():get_GameObject()'). filter: substring of field names. Output is capped at max_lines. The fastest way to learn
    what a manager or component actually stores. Use dd2_types for methods, dd2_catalog to find which manager owns a feature."""
    code = (_RESOLVE_LUA + f"local o = R({json.dumps(expr)})\n"
            f"print(json.dump_string(D.dump(o, {int(depth)}, {{pat = {json.dumps(filter or None)}, max = {int(max_items)}, "
            f"statics = {str(bool(statics)).lower()}}})))")
    res = game.eval_lua(code, timeout=30)
    if not res.get("ok"):
        return game.format_result(res)
    try:
        lines = _render_dump(json.loads("".join(res["out"])))
        if len(lines) > max_lines:  # deep dumps of big managers can run to 100k characters
            lines = lines[:max_lines] + [f"... {len(lines) - max_lines} more lines (lower depth, use filter=, or raise max_lines)"]
        return "\n".join(lines)
    except (ValueError, KeyError):
        return game.format_result(res)


@mcp.tool()
def dd2_find(type_name: str, limit: int = 25, name: str = "", inspect_nearest: bool = False) -> str:
    """Live components of a type in the current scene, nearest to the player first: 'distance  GameObject  x y z'.
    type_name: e.g. 'app.Gm80_055', 'app.Enemy', 'app.NPCController'. name: substring filter on the GameObject name.
    inspect_nearest=True also dumps the nearest component's fields. Answers 'what is that thing next to me and what
    component drives it'."""
    code = (f"local r, total = D.find({json.dumps(type_name)}, {int(limit)}, {json.dumps(name or None)})\n"
            "if type(r) == 'string' then print(r) return end\n"
            "print('total ' .. total)\n"
            "for _, e in ipairs(r) do print(string.format('%8.1f  %s  %.1f %.1f %.1f', e.dist, e.go, e.x, e.y, e.z)) end\n"
            + ("if r[1] then print(json.dump_string(D.dump(r[1].comp, 1, {max = 4}))) end" if inspect_nearest else ""))
    res = game.eval_lua(code, timeout=30)
    lines = list(res.get("out") or [])
    if inspect_nearest and lines and lines[-1].startswith("{"):
        try:
            lines = lines[:-1] + ["", "nearest:"] + _render_dump(json.loads(lines[-1]))
        except ValueError:
            pass
    if not res.get("ok"):
        lines.append(f"ERROR: {res.get('err')}")
    return "\n".join(lines) or "(no output)"


@mcp.tool()
def dd2_wwise(action: str = "banks", bank: str = "", id: int = 0, out: str = "") -> str:
    """Wwise sound introspection (no game needed; see dd2mcp/wwise.py for the DD2 layout: `<name>_es.sbnk` = event structure only, `<name>_m.sbnk` =
    the audio, and the `_m` companions are not in the community file list). Actions:
      'banks'   list the `_es` banks of the community list with whether their `_m` companion exists and its media count (bank = regex filter).
      'event'   bank = stem (e.g. 'mgc_skill'), id = HIRC event id (the `_EventId` of a soundlib.SoundTriggerInfo; dd2_eval can read it):
                everything the event plays (actions -> containers -> sounds), each media id, codec, size and the `_m` bank + offset it lives in,
                and which other events share those media.
      'extract' bank = stem, id = media id, out = directory: writes <id>.wem (Wwise Vorbis / PCM; convert with vgmstream to hear or edit it).
    Editing helpers for scripts: wwise.build_layered_banks (new es+m pair whose event plays game sounds with a Pitch property) / rebuild_media_bank /
    patch_sound_source. PCM WEM sources are SILENTLY DROPPED by the game's engine: reuse game Vorbis media and edit with sound properties.
    Getting a custom bank to play in game (the engine loads banks only from deserialized .user data, not sdk.create_resource): docs/wwise-custom-banks.md."""
    from . import pak, wwise
    import pathlib as _p
    if action == "banks":
        rows = []
        for n in pak.search_list(r"natives/stm/sound/wwise/[^/]*_es\.sbnk\.1\.x64$", 2000):
            if bank and not re.search(bank, n):
                continue
            m = wwise.read_bank(wwise.bank_path(n, "m"))
            rows.append(f"{n.split('/')[-1]}: " + (f"_m companion {len(m) // 1024} KB, {len(wwise.media_index(m))} media" if m else "no _m companion"))
        return "\n".join(rows[:200]) or "no match"
    if action == "event":
        es = wwise.read_bank(bank)
        if es is None:
            return f"bank {bank!r} not found ({wwise.bank_path(bank)})"
        objs = wwise.hirc(es)
        rows = wwise.resolve(id, objs)
        if not rows:
            return f"event {id} is not in {bank} ({len(objs)} objects)"
        lines = [f"event {id} in {wwise.bank_path(bank)} (bank id {wwise.bank_id(es)}):"]
        for r in rows:
            line = "  " + " -> ".join(str(x) for x in r["path"]) + f": {r['type']}"
            if r.get("media"):
                loc = wwise.locate_media(r["media"], bank)
                if loc:
                    wem = wwise.media_bytes(loc[1], r["media"])
                    info = wwise.wem_info(wem)
                    line += f" media {r['media']} plugin {r.get('plugin')} stream {r.get('stream')}: {info['codec']} {info['channels']}ch {info['rate']} Hz {info['bytes']} B in {loc[0].split('/')[-1]} @ DATA+{loc[2]}"
                else:
                    line += f" media {r['media']} NOT FOUND in any `_m` bank"
            lines.append(line)
        media = {r["media"] for r in rows if r.get("media")}
        others = [e for e, (t, b, _) in objs.items() if t == 4 and e != id and media & {r2["media"] for r2 in wwise.resolve(e, objs) if r2.get("media")}]
        lines.append(f"  other events using these media: {others or 'none'}")
        return "\n".join(lines)
    if action == "extract":
        loc = wwise.locate_media(id, bank or None)
        if not loc:
            return f"media {id} not found"
        wem = wwise.media_bytes(loc[1], id)
        d = _p.Path(out or "research/extract/wwise")
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{id}.wem").write_bytes(wem)
        return f"wrote {d / f'{id}.wem'} ({len(wem)} bytes, {wwise.wem_info(wem)['codec']}) from {loc[0]}"
    return "actions: banks | event | extract"


@mcp.tool()
def dd2_trace(type_name: str, method: str, seconds: float = 5.0, samples: int = 12) -> str:
    """Count and sample calls to a game method over `seconds` of play (every overload of that name, read-only hook).
    Returns call counts and the first `samples` calls (this-type and arguments). Use it to learn WHICH functions the
    game calls when you do something in game ('what runs when I cast / open the menu / pick up an item') - then
    follow up with dd2_inspect on the objects involved. The game must be focused and running (pauses when unfocused)."""
    import time as _t
    start = game.eval_lua(f"return D.trace_start({json.dumps(type_name)}, {json.dumps(method)}, {int(samples)})", timeout=20)
    if not start.get("ok"):
        return game.format_result(start)
    game.eval_lua("D.trace_read(true)")  # reset counters
    _t.sleep(seconds)
    res = game.eval_lua("local r = D.trace_read(false); D.trace_stop(); print(json.dump_string(r))", timeout=20)
    if not res.get("ok"):
        return game.format_result(res)
    try:
        rows = json.loads("".join(res["out"]))
    except ValueError:
        return game.format_result(res)
    out = []
    for r in rows:
        if r["count"]:
            out.append(f"{r['key']}: {r['count']} calls in {seconds}s")
            out += [f"  {x}" for x in r["samples"]]
    return "\n".join(out) or f"no calls to {type_name}::{method} in {seconds}s (is the game focused and the action performed?)"


@mcp.tool()
def dd2_singletons(filter: str = "", refresh: bool = False) -> str:
    """Live managed singletons (the game's managers): which `*Manager` types exist right now. REFramework cannot enumerate
    them, so every manager-like type name from the offline method map is probed with get_managed_singleton (once, cached;
    refresh=True re-probes). Output: name, number of serialized fields, method count - then dd2_inspect any of them.
    filter: regex on the name."""
    cache = game.SCRATCH / "singletons.json"
    names = None
    if cache.exists() and not refresh:
        try:
            names = json.loads(cache.read_text())
        except ValueError:
            names = None
    if names is None:
        from . import catalog
        cands = sorted(t for t in catalog.manager_candidates())
        live, step = [], 400
        for i in range(0, len(cands), step):
            chunk = cands[i:i + step]
            res = game.eval_lua("local live = {}\nfor _, n in ipairs({" + ",".join(json.dumps(c) for c in chunk) + "}) do\n"
                                "  local ok, o = pcall(sdk.get_managed_singleton, n)\n"
                                "  if ok and o then live[#live + 1] = n end\nend\nprint(json.dump_string(live))", timeout=60)
            if not res.get("ok"):
                return game.format_result(res)
            live += json.loads("".join(res["out"]))
        names = live
        cache.write_text(json.dumps(names))
    rx = re.compile(filter, re.I) if filter else None
    shown = [n for n in names if not rx or rx.search(n)]
    return f"{len(shown)}/{len(names)} live singletons\n" + "\n".join(shown)


@mcp.tool()
def dd2_where(type_name: str = "", path: str = "", action: str = "search", limit: int = 40) -> str:
    """Which data file holds which serialized class (no game needed). 'search': regex `type_name` on class names (e.g.
    'ItemDropParam', 'JobInformation', 'Shell') and/or regex `path` on file paths -> files with their top classes and
    instance counts, biggest first. Then open a hit with dd2_table / dd2_rsz_file. Covers ~12k gameplay .user files
    (not the bulk grid/sound/AI-situation folders; find those with dd2_pak list). 'build' starts the one-time background
    index (~6 min); 'status' shows progress."""
    import subprocess
    from . import dataindex
    if action == "build":
        subprocess.Popen([sys.executable, "-m", "dd2mcp.dataindex", "build"], cwd=str(pathlib.Path(__file__).parents[1]),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return "index build started in the background; dd2_where action='status' shows progress"
    if action == "status":
        return (dataindex.PROGRESS.read_text() if dataindex.PROGRESS.exists() else "never built") + \
               ("\nindex current" if dataindex.load() is not None else "\nindex missing or stale (paks changed)")
    return dataindex.search(type_name, path, limit)


@mcp.tool()
def dd2_skeleton(action: str = "info", path: str = "", out: str = "", bones: str = "") -> str:
    """DD2 .skeleton files ('skln' v8, the skeleton resource for via.motion.DummySkeleton / CustomSkeleton; 27 in the game: ragdoll push bodies,
    look-at rigs, gimmick props). Format decoded 2026-11-02, round-trips byte-exact on all 27. 'info': print the bone tree of a pak path or local
    file (name, parent, translation, rotation). 'author': write a new .skeleton to `out` from `bones` = JSON list of
    {"name","parent"(index, -1 root),"t":[x,y,z],"q":[x,y,z,w]} (parents must come before children). 'roundtrip': read + rewrite `path`, report if
    identical. Bone names must match the mesh's bone names (hash = murmur3 of the UTF-16 name)."""
    import json as _json
    import pathlib as _p
    from . import pak, skeleton
    def load():
        if _p.Path(path).exists():
            return _p.Path(path).read_bytes()
        f = pak.find(path)
        return f[1] if f else None
    if action in ("info", "roundtrip"):
        data = load()
        if data is None:
            return f"{path}: not a local file and in no pak"
        bs = skeleton.read(data)
        if action == "roundtrip":
            return "identical" if skeleton.write(bs) == data else "DIFFERENT"
        depth = {}
        lines = []
        for i, b in enumerate(bs):
            depth[i] = 0 if b.parent < 0 else depth[b.parent] + 1
            lines.append(f"{'  ' * depth[i]}[{i}] {b.name}  t=({b.t[0]:.3f},{b.t[1]:.3f},{b.t[2]:.3f}) q=({b.q[0]:.3f},{b.q[1]:.3f},{b.q[2]:.3f},{b.q[3]:.3f})")
        return f"{len(bs)} bones\n" + "\n".join(lines)
    if action == "author":
        if not (bones and out):
            return "author needs bones (JSON) and out (file path)"
        spec = _json.loads(bones)
        bs = []
        for i, d in enumerate(spec):
            par = d.get("parent", -1)
            if par >= i:
                return f"bone {i} {d['name']!r}: parent {par} must come before it"
            bs.append(skeleton.Bone(d["name"], par, -1, tuple(d.get("t", (0, 0, 0))) + (1.0,), tuple(d.get("q", (0, 0, 0, 1)))))
        data = skeleton.write(bs)
        _p.Path(out).parent.mkdir(parents=True, exist_ok=True)
        _p.Path(out).write_bytes(data)
        return f"wrote {out} ({len(data)} bytes, {len(bs)} bones); pak it as natives/stm/<path>.skeleton.8"
    return "actions: info, author, roundtrip"


@mcp.tool()
def dd2_enum(type_name: str) -> str:
    """Every named value of an enum type, sorted by value (e.g. 'app.Character.JobEnum', 'app.WeaponID')."""
    res = game.eval_lua(f"for _, e in ipairs(D.enum({json.dumps(type_name)})) do print(e.value, e.name) end", timeout=30)
    return game.format_result(res)


@mcp.tool()
def dd2_rsz(pattern: str = "", fields: bool = False, limit: int = 200, action: str = "search") -> str:
    """Offline type dump search (alphaZomega RE_RSZ rszdd2.json, all serializable types + fields; no game needed).
    pattern is a case-insensitive regex on type names (and field names/types when fields=True).
    action='download' fetches/refreshes the dump. The dump can lag game patches: confirm live with dd2_types."""
    from . import rsz
    if action == "download":
        return rsz.download()
    return rsz.search(pattern, fields, limit) if pattern else "search needs pattern"


@mcp.tool()
def dd2_rsz_file(path: str, find: str = "", show: str = "", depth: int = 2, limit: int = 40) -> str:
    """Decode the RSZ blocks (serialized objects) of a game file: a pak path ('natives/stm/...motfsm2.45', newest
    copy across base, DLC and patch paks) or a local file. No args beyond path: per-block summary (instances, top
    types). find: regex on type names and field values -> 'block:index type fields' lines. show='block:index':
    that instance and the objects it references, `depth` levels. Layouts come from the 2026 community template,
    the 2024 dump and live-generated ones (dd2_rszgen), chosen per type by decode plausibility. Unknown types are
    reported by hash: name them with dd2_rszgen."""
    import collections
    import re as _re
    from . import pak, rszfile
    if pathlib.Path(path).exists():
        data = pathlib.Path(path).read_bytes()
    else:
        found = pak.find(path)
        if not found:
            return f"{path}: not a local file and in no pak"
        data = found[1]
    offs = rszfile.blocks(data)
    blocks, out = [], []
    for k, off in enumerate(offs):
        try:
            blocks.append(rszfile.Block(data, off, offs[k + 1] if k + 1 < len(offs) else None))
        except ValueError as e:
            blocks.append(None)
            out.append(f"block {k} @{off:#x}: {e}")
    if show:
        b, i = (int(x) for x in show.split(":"))
        return blocks[b].show(i, depth) if blocks[b] else "\n".join(out)
    if find:
        rx = _re.compile(find, _re.I)
        n = 0
        for k, b in enumerate(blocks):
            for i, inst in enumerate(b.instances if b else []):
                if inst and (rx.search(inst[0]) or rx.search(str(inst[1]))):
                    out.append(f"{k}:{i} {inst[0]} {str(inst[1])[:300]}")
                    n += 1
                    if n >= limit:
                        return "\n".join(out + [f"... (limit {limit})"])
        return "\n".join(out) or "no match"
    for k, b in enumerate(blocks):
        if b and len(b.instances) > 1:
            top = collections.Counter(x[0] for x in b.instances[1:]).most_common(5)
            out.append(f"block {k} @{offs[k]:#x}: {len(b.instances) - 1} instances, {len(b.roots)} roots, "
                       f"{b.retries} layout retries; top: " + ", ".join(f"{t} x{c}" for t, c in top))
    return "\n".join(out) or "no RSZ data"


@mcp.tool()
def dd2_bhvt(path: str, node: str = "", action: str = "", tree: bool = False, depth: int = 3, detail: bool = True,
             limit: int = 30) -> str:
    """Read a behavior tree (.bhvt, or the tree inside a .motfsm2: player = natives/stm/appsystem/ch/ch000/motionfsm/
    ch000000.motfsm2.45 (full body, layer 0) and ch000000_upperbody.motfsm2.45 (layer 1)) from the paks or a local
    file. node: regex on full node paths (e.g. 'PsychoShoot$') -> each match with its selector, actions (decoded RSZ
    objects), children with conditions, FSM states/start transitions with conditions and events. action: regex on
    action type names -> nodes running them. tree=True: the matching node's subtree as an indented outline, `depth`
    levels. No node/action: summary (node count, top-level states, condition/state counts)."""
    import re as _re
    from . import bhvt, pak
    if pathlib.Path(path).exists():
        data = pathlib.Path(path).read_bytes()
    else:
        found = pak.find(path)
        if not found:
            return f"{path}: not a local file and in no pak"
        data = found[1]
    try:
        T = bhvt.Tree(data)
    except ValueError as e:
        return f"{path}: {e}"
    if action:
        rx = _re.compile(action, _re.I)
        out = []
        for n in T.nodes:
            hits = [o for o in T.actions_of(n) if o and rx.search(o[0].instances[o[1]][0])]
            if hits:
                out.append(f"{n['full']}: " + ", ".join(bhvt.describe_obj(o, detail, 160) for o in hits))
                if len(out) >= limit:
                    out.append(f"... (limit {limit})")
                    break
        return "\n".join(out) or "no node runs a matching action"
    if node:
        matches = T.find(node)
        if not matches:
            return "no matching node"
        if tree:
            def outline(n, lvl, out):
                acts = [o[0].instances[o[1]][0].split(".")[-1] for o in T.actions_of(n) if o]
                out.append("  " * lvl + n["name"] + (f"  [{', '.join(acts)}]" if acts else ""))
                if lvl < depth:
                    for c in n["children"]:
                        ch = T.by_id.get(c["id"])
                        if ch:
                            outline(ch, lvl + 1, out)
                return out
            out = []
            for m in matches[:3]:
                outline(m, 0, out)
            return "\n".join(out)
        out = [bhvt.describe(T, n, detail) for n in matches[:limit]]
        if len(matches) > limit:
            out.append(f"... {len(matches) - limit} more")
        return "\n\n".join(out)
    root = next((n for n in T.nodes if n["parent"] == 0xFFFFFFFF), T.nodes[0])
    top = [T.by_id[c["id"]]["name"] for c in root["children"] if c["id"] in T.by_id]
    nc = sum(1 for n in T.nodes for x in n["states"] + n["transitions"] + n["children"] + n["allStates"] if not x["cond"].none)
    return (f"{len(T.nodes)} nodes, {sum(len(n['actions']) for n in T.nodes)} action refs, {nc} conditional links, "
            f"{sum(len(n['states']) for n in T.nodes)} states\ntop level: {', '.join(top)}")


@mcp.tool()
def dd2_uvar(path: str = "", name: str = "", users: bool = True, scan: bool = False, limit: int = 40,
             live: bool = False, set: str = "", who: str = "player", eval: str = "") -> str:
    """User variables and their expression graphs: from a .uvar or the tree inside a .bhvt/.motfsm2 (pak path or
    local file; player: natives/stm/appsystem/ch/ch000/motionfsm/ch000000.motfsm2.45). Variables are listed per
    file (own, reference trees', embedded). name: regex on variable names -> the variable's type, value, flags,
    expression as readable logic (e.g. '((SitMotion == 2000) || (SitMotion == 2700))') and, for trees, which
    nodes' conditions use it, directly or through other variables' expressions. scan=True (needs name, no path):
    search every FSM/uvar file in the game (~10 s). Operation numbers whose meaning is inferred, not confirmed,
    carry a '?' (see uvar.OPERATIONS for the evidence).
    eval='A=1,B=2.5': what-if values -> the result of every matched variable that has an expression (offline).
    live=True: the running game's variables for `who` (player, pawn1..) with their current values (expressions
    already evaluated by the engine); with a path, expression logic is shown next to them. set='Name=value'
    writes a (writable) variable first, e.g. set='SitMotion=2700', then lists, so derived values show the effect."""
    import json as _json
    import re as _re
    from . import bhvt, pak, uvar

    def parse_value(t):
        t = t.strip()
        if t.lower() in ("true", "false"):
            return t.lower() == "true"
        try:
            return int(t)
        except ValueError:
            try:
                return float(t)
            except ValueError:
                return t

    def load(p):
        if pathlib.Path(p).exists():
            return pathlib.Path(p).read_bytes()
        f = pak.find(p)
        return f[1] if f else None

    def describe_var(v, tree=None):
        val = "" if v.value is None else f" = {v.value}"
        line = f"{v.name} : {v.type_name}{val}" + (" (const)" if v.flags & uvar.FLAG_CONST else "")
        if v.expression:
            txt = tree.expr_text(v) if tree else uvar.render(v.expression, lambda g: names.get(g, "var:" + g[:8]))
            line += f"\n    expression: {txt}"
        return line

    if live or set:
        out = []
        if set:
            k, _, v = set.partition("=")
            r = game.eval_lua(f"return D.uvar_set({_json.dumps(who)}, {_json.dumps(k.strip())}, "
                              f"{_json.dumps(parse_value(v))})", phase="update")
            if not r.get("ok"):
                return game.format_result(r) + "\n(bridge older than 0.9.0? dd2_install_bridge)"
            out.append(f"set {k.strip()} = {parse_value(v)}: {(r.get('ret') or ['?'])[0]} variable(s) written")
        r = game.eval_lua(f"return json.dump_string({{vars = D.uvars({_json.dumps(who)})}})")
        if not r.get("ok"):
            return game.format_result(r) + "\n(bridge older than 0.9.0? dd2_install_bridge)"
        rows = _json.loads(r["ret"][0]).get("vars") or []
        text = {}
        if path and ".uvar" not in path:
            data = load(path)
            if data:
                t = bhvt.Tree(data)
                t.uvars
                text = {v.name: t.expr_text(v) for v in t.variables.values() if v.expression}
        rx = _re.compile(name, _re.I) if name else None
        for v in rows:
            if rx and not rx.search(v["name"]):
                continue
            flags = ", ".join(x for x, on in (("expression", v["expr"]), ("read-only", v["readOnly"]),
                                              ("const", v["const"])) if on)
            out.append(f"L{v['layer']} {v['name']} = {v.get('value')!r}" + (f"   [{flags}]" if flags else "")
                       + (f"\n      {text[v['name']]}" if v["name"] in text else ""))
        return "\n".join(out) or "no variables"
    if scan:
        if not name:
            return "scan needs name"
        rx, out = _re.compile(name, _re.I), []
        for p in pak.search_list(r"\.(uvar|motfsm2|tmlfsm2)\.\d+$", 200000):
            f = pak.find(p)
            if not f:
                continue
            try:
                tree = None if ".uvar." in p else bhvt.Tree(f[1])
                roots = uvar.read_uvars(tree) if tree else [uvar.Uvar(f[1], 0, 0)]
                allv = {}
                for r in roots:
                    for u in r.walk():
                        for v in u.variables:
                            allv[v.guid] = v
                for r in roots:
                    for u in r.walk():
                        for v in u.variables:
                            if rx.search(v.name):
                                txt = ""
                                if v.expression:
                                    txt = "  " + uvar.render(v.expression, lambda g: allv[g].name if g in allv else "var:" + g[:8])
                                out.append(f"{p.split('/')[-1]}: {v.name} [{v.type_name}]{txt}")
            except Exception as e:  # noqa: BLE001 - a bad file must not stop the scan
                out.append(f"{p}: {e}")
            if len(out) >= limit:
                out.append(f"... (limit {limit})")
                break
        return "\n".join(out) or "no match"
    if not path:
        return "path needed (or scan=True with name)"
    data = load(path)
    if data is None:
        return f"{path}: not a local file and in no pak"
    tree = None
    if ".uvar" in path:
        roots = [uvar.Uvar(data, 0, 0)]
    else:
        tree = bhvt.Tree(data)
        roots = tree.uvars
    rx = _re.compile(name, _re.I) if name else None
    names = {v.guid: v.name for r in roots for u in r.walk() for v in u.variables}
    out, n = [], 0
    for ri, root in enumerate(roots):
        for u in root.walk():
            sel = [v for v in u.variables if not rx or rx.search(v.name)]
            if not sel:
                continue
            out.append(f"-- {'variable set ' + str(ri) if tree else 'variables'}{' / ' + u.name if u.name else ''}"
                       f" ({len(u.variables)} variables)")
            for v in sel:
                out.append("  " + describe_var(v, tree).replace("\n", "\n  "))
                if eval and v.expression:
                    vals = {a.split("=")[0].strip(): parse_value(a.split("=")[1]) for a in eval.split(",") if "=" in a}
                    notes = []
                    try:
                        res = uvar.evaluate(v.expression, vals, lambda g: names.get(g), notes)
                        out.append(f"    with {eval}: {res!r}" + (f"   ({'; '.join(sorted(set(notes)))})" if notes else ""))
                    except KeyError as e:
                        out.append(f"    with {eval}: needs a value for {e.args[0]}")
                    except uvar.Unknown as e:
                        out.append(f"    with {eval}: cannot evaluate ({e})")
                if tree and rx and users:
                    out += ["    " + x for x in tree.var_users(v.guid)]
                n += 1
                if n >= limit:
                    out.append(f"... (limit {limit})")
                    return "\n".join(out)
    return "\n".join(out) or "no variables"


@mcp.tool()
def dd2_table(path: str, type: str = "", cols: str = "", where: str = "", sort: str = "", limit: int = 30,
              depth: int = 2, names: bool = True, width: int = 28, listcols: bool = False) -> str:
    """Spreadsheet view of a game data file (.user/.scn/.pfb; pak path or local file; no game needed): one row per
    instance of `type` (regex; default = the most common type), flattened columns (nested objects as `_Param._Hp`,
    `depth` levels). GUIDs that name a game text show the English string, enum fields show `3:Equip`, item rows get a
    Name column. cols: comma list of exact names or regexes; listcols=True lists all columns. where: 'col~regex',
    'col=val', 'col>n', 'col!=v' joined with ' & ' (matches raw or displayed value). sort: column, '-col' descending.
    Data tables live in natives/stm/appsystem/item/itemdata, item/itemdropdata, item/itemshopdata, gui/jobmenu,
    quest/userdata, gimmick/userdata, shell/userdata, camp/userdata, ch/<chNNN> (find more with dd2_pak list)."""
    from . import table
    try:
        return table.render(path, type, cols, where, sort, limit, depth, names, width, listcols)
    except (FileNotFoundError, ValueError) as e:
        return str(e)


@mcp.tool()
def dd2_msg(action: str = "search", query: str = "", lang: str = "English", scope: str = "", limit: int = 40) -> str:
    """Game text (.msg, 1060 files, 64k entries; no game needed). 'search': regex over entry names, GUIDs and English text
    ('file | entry name | guid | text'); scope = regex on the file path (e.g. 'itemname', 'quest', 'pawn'). 'get': one entry by
    GUID or exact name in every language (lang='all') plus its attributes. 'rebuild': refresh the cached index (auto when paks change).
    Item names are entries item_name_<id> in message/ui/itemname.msg; descriptions item_detail_<id>."""
    from . import msg
    if action == "rebuild":
        return f"{len(msg.index(True))} entries indexed"
    if action == "get":
        return msg.lookup(query, lang)
    return msg.search(query or ".", lang, scope, limit)


@mcp.tool()
def dd2_rszgen(names: list[str] | None = None, deep: bool = False, hashes: list[str] | None = None, auto: bool = False) -> str:
    """Generate RSZ layouts from the running game for these type names (and their parents) into
    research/rsz/rsz_live_dd2.json; dd2_rsz_file uses them for types the community layouts lack. Type hashes are
    murmur3(utf-8 name): to name an unknown hash, collect candidate names from live objects (e.g. walk the objects
    holding the data) and compare with dd2mcp.murmur.type_hash. deep=True also follows the classes the types'
    serialized fields use (e.g. dd2_rszgen(['app.Job03Parameter'], deep=True) for the Mage's whole parameter file).
    hashes=['3d27943d', ...]: type hashes from a "types not in any layout source" error - they are turned back into names
    by hashing every type name the offline map knows, then generated. auto=True: heal EVERY unknown hash found by the
    dd2_where index in one go (deep). Needs the game running."""
    from . import rszgen
    if auto:
        from . import dataindex
        idx = dataindex.load()
        if idx is None:
            return "auto needs the dd2_where index (action='build')"
        hashes = sorted({t for ty in idx.values() for t in ty if t.startswith("?")})
    if hashes:
        return rszgen.heal(hashes, deep=True) if not names else rszgen.heal(hashes, deep) + "\n" + rszgen.generate(names, deep)
    return rszgen.generate(names or [], deep)


@mcp.tool()
def dd2_pak(action: str = "find", path: str = "", out: str = "") -> str:
    """Game pak files without outside tools (pure-Python reader for DD2's 0x68 paks).
    'list' searches the community file list (regex on paths, e.g. 'gui/ui01/common/job'); 'find' reports which pak
    holds the newest copy of a path; 'extract' writes it to out (a directory, default research/extract) and, for a
    .tex, a PNG next to it."""
    from . import pak
    if action == "list":
        return "\n".join(pak.search_list(path or ".", 200)) or "no match"
    found = pak.find(path)
    if not found:
        return f"{path} is in no pak (paths are case-insensitive but need the .N version suffix)"
    src, data = found
    if action == "find":
        return f"{path}: {src.name}, {len(data)} bytes"
    if action == "extract":
        import pathlib as _p
        base = _p.Path(out or "/mnt/d/DD2Modding/research/extract")
        dst = base / path
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        msg = f"wrote {dst} ({len(data)} bytes, from {src.name})"
        if ".tex." in path:
            from . import tex
            png = dst.with_suffix(dst.suffix + ".png")
            tex.to_image(data).save(png)
            msg += f"\npng: {png}"
        return msg
    return f"unknown action {action!r}"


@mcp.tool()
def dd2_mesh(action: str = "info", src: str = "", out: str = "", colors: bool = False) -> str:
    """Game .mesh files via the headless REE-Lib helper (dotnet/reetool, built on demand with the private .NET SDK in Tools/).
    'import': glTF/GLB -> DD2 .mesh (src=.glb, out=.mesh) using the Content Editor importer plus the fixes the game needs
    (non-null streaming buffers; colors=True adds an opaque white vertex-color stream, only for VFX-style meshes, NOT for weapon-layout
    meshes). 'info': dump a .mesh header/buffers (compare a custom mesh against a game one: element count, flags, bufferCount).
    'roundtrip': read + rewrite a .mesh (is the writer lossless?). 'materials': list the materials of an .mdf2 (src).
    A mesh only LINKS to a game material (mesh:get_MaterialLinked()) when its material names match the .mdf2's, in order."""
    from . import ree
    if action == "import":
        return ree.run("mesh-import", src, out, *(["--colors"] if colors else []))
    if action == "info":
        return ree.run("mesh-info", src)
    if action == "roundtrip":
        return ree.run("mesh-rt", src, out)
    if action == "materials":
        return ree.run("mdf-info", src)
    return f"unknown action {action!r}"


@mcp.tool()
def dd2_material(action: str = "scan", pattern: str = "", per_master: int = 3, path: str = "", full: bool = False) -> str:
    """Game .mdf2 materials. 'extract': copy the .mdf2 files whose path matches the regex `pattern` out of the paks into
    research/mdf (do this once with pattern '\\.mdf2\\.51$' for all ~13k). 'scan': list the master shaders of the extracted
    materials whose master matches the regex `pattern` (e.g. 'Illusion|Transparent'), with `per_master` example files each (the
    file shows its material count). 'info': list the materials of one .mdf2 (path: local, pak or in-game path); full=True also lists every texture slot (index, type, path)
    and every parameter (index, name, components, default value): the indices are what Mesh:setMaterialTexture / setMaterialFloat / setMaterialFloat4 take in Lua."""
    from . import ree
    if action == "extract":
        return ree.extract(pattern or r"\.mdf2\.51$", "mdf")
    if action == "scan":
        return ree.run("mdf-scan", ree.RESEARCH / "mdf", pattern, per_master)
    if action == "info":
        import pathlib as _p
        from . import pak
        local = _p.Path(path)
        if not local.exists():  # a pak path ('natives/stm/character/...mdf2.51') or an in-game path ('Character/ch/ch25_000/ch25_000.mdf2')
            rel = path.replace("\\", "/").lstrip("/")
            rel = rel[len("natives/stm/"):] if rel.lower().startswith("natives/stm/") else rel
            if not rel.endswith(".51"):
                rel += ".51"
            local = ree.RESEARCH / "mdf" / rel.lower()
            if not local.exists():
                found = pak.find("natives/stm/" + rel)
                if not found:
                    return f"{path}: not a local file and in no pak"
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_bytes(found[1])
        return ree.run("mdf-info", str(local), *(["--full"] if full else []))
    return f"unknown action {action!r}"


@mcp.tool()
def dd2_efx(action: str = "info", path: str = "", filter: str = "", all: bool = False, out: str = "", mesh: str = "", material: str = "") -> str:
    """Game effect (.efx) files via REE-Lib, patched at read time for this game build (efx version 5965310 = "DD2New").
    'extract': copy the .efx files whose path matches the regex `path` out of the paks into research/efx (empty = everything the
    community file list knows, ~5000). 'info': entries and attributes of one .efx (mesh/material paths shown; all=True lists every
    attribute). 'scan': find extracted .efx files that have an attribute type containing `filter` (e.g. 'Mesh'); survives files that
    crash the parser. 'debug': header/section offsets of one file. 'normalize': write a copy of `path` to `out` without the
    per-entry extra int32 (see below) so stock REE-Lib can read it.
    FORMAT FINDINGS (2026-10-31, still incomplete): (1) every entry is preceded by one extra int32 REE-Lib 0.8.1 does not know (it is
    NOT the attribute count; meaning unknown) - reetool strips it before parsing. (2) REE-Lib has no attribute-type-id table for
    version DD2New; the ids in this build look like the original DD2 table (env REETOOL_EFX_IDS=DD2, default) for many types but the
    mesh attribute is id 31 (the Pragmata value), so the table is a hybrid. (3) several attribute structs changed size (PtBehavior,
    Spawn...). Net effect: only part of the ~5000 files parse; files with bones/actions are not walked yet. Mesh elements (id 31) store
    their paths as [int byteSize][UTF-16 + NUL] (mesh, mirror mesh, material); 'patch_mesh' copies `path` to `out` with the mesh element's\n    mesh path set to `mesh` (and material to `material` if given), fixing the attribute size - no attribute parsing needed."""
    from . import ree
    if action == "extract":
        return ree.extract(path or r"\.efx\.", "efx")
    if action == "info":
        return ree.run("efx-info", path, *(["--all"] if all else []))
    if action == "scan":
        return ree.scan_efx(path or ree.RESEARCH / "efx", filter)
    if action == "debug":
        return ree.run("efx-debug", path)
    if action == "normalize":
        return ree.run("efx-normalize", path, out)
    if action == "patch_mesh":
        return ree.efx_patch_mesh(path, out, mesh, material)
    return f"unknown action {action!r}"


@mcp.tool()
def dd2_package(project: str, build: bool = True) -> str:
    """Build a mod project's release zip (Fluffy format, also what Vortex installs): runs dd2mod.json package.build
    (e.g. a pak builder), then zips modinfo.ini + build/*.pak + src/reframework/** minus package.exclude into
    <project>/build/release/<file>-<version>.zip. Refuses packages with dev/test files or unreadable paks."""
    from . import package
    return package.build(project, build)


@mcp.tool()
def dd2_fluffy(action: str = "status", name: str = "", row: int = 0) -> str:
    """Fluffy Mod Manager, for release-install tests. 'launch' / 'close'; 'enable' or 'disable' (name = part of the
    mod's file name, e.g. 'Wizard'; row = its position in Fluffy's list, 0 = first) clicks its box and reports the
    files now in the game; 'status' lists the files a mod has in the game (pak_mods + reframework scripts)."""
    from . import fluffy
    if action == "launch":
        return fluffy.launch()
    if action == "close":
        return fluffy.close()
    if action in ("enable", "disable"):
        before = fluffy.installed_files(name)
        if bool(before["pak_mods"] or before["scripts"]) == (action == "enable"):
            return f"already {action}d: {before}"
        msg = fluffy.launch()
        if msg != "Fluffy open":
            return msg
        click = fluffy.toggle(row)
        after = fluffy.installed_files(name)
        ok = bool(after["pak_mods"] or after["scripts"]) == (action == "enable")
        return f"{click}\n{'OK' if ok else 'NOT ' + action + 'd (wrong row? screenshot)'}: {after}"
    return f"{name or '(no name)'} in the game: {fluffy.installed_files(name)}"


@mcp.tool()
def dd2_release(project: str, apply: bool = False) -> str:
    """Publish a mod release: a DRAFT GitHub release v<version> with the release zip (dd2_package) attached.
    Only from a clean, pushed main with a new tag; apply=False (default) just reports. Use only after in-game
    testing passed and the user said release. Nexus uploads stay manual (no upload API)."""
    from . import package
    return package.release(project, apply)


@mcp.tool()
def dd2_crash(dump: str | None = None) -> str:
    """Analyze the last native crash (reframework_crash.dmp, or a given minidump path): exception, registers,
    disassembly around the fault and stack return addresses, mapped to managed methods through the method map
    of this game build (dd2_methodmap). No game needed."""
    from . import crash
    import pathlib
    return crash.analyze(pathlib.Path(dump) if dump else crash.DUMP)


@mcp.tool()
def dd2_methodmap(action: str = "status", pattern: str | None = None) -> str:
    """Map of managed method -> native code RVA for the installed game build (research/methodmap-<build>.tsv).
    'export' builds it from the running game (~1 s, needs bridge 0.6.0+); 'find' lists methods whose
    'Type.method(params)' matches the regex pattern; 'status' says whether a map exists for this build."""
    from . import crash
    if action == "export":
        return crash.export_methodmap()
    if action == "find":
        rows = crash.find_method(pattern or ".", limit=50)
        return "\n".join(f"{a:x}  {n}" for a, n in rows) or "no match (or no map: export first)"
    p = crash.map_path()
    return f"build {crash.build_id()}: " + (f"{p} ({sum(1 for _ in open(p))} methods)" if p.exists() else "no map yet (export)")


@mcp.tool()
def dd2_disasm(target: str, length: int = 0x100) -> str:
    """Disassemble DD2.exe code from disk: target is an RVA in hex ('823c230') or a regex for a method in the
    method map ('JobContext.get_JobIndex'). rip-relative operands are annotated with their target RVA
    (globals the compiled code reads; poke them live with D.poke32(D.module_base() + rva, v))."""
    from . import crash
    if re.fullmatch(r"(0x)?[0-9a-fA-F]+", target):
        rva, head = int(target, 16), ""
    else:
        rows = crash.find_method(target, limit=5)
        if not rows:
            return f"no method matches /{target}/ (export the map with dd2_methodmap)"
        rva, head = rows[0][0], f"{rows[0][1]} @ {rows[0][0]:x}" + (f" (also: {', '.join(n for _, n in rows[1:])})" if len(rows) > 1 else "") + "\n"
    return head + crash.disasm(rva, length)


@mcp.tool()
def dd2_xrefs(target: str, limit: int = 40) -> str:
    """Who references a DD2.exe address: rel32 calls/jumps to it and rip-relative operands pointing at it (globals
    such as the job-count int, static slots), each with its containing method. target is an RVA in hex or a method
    regex as in dd2_disasm. Scans the code section from disk (~2 s, no game needed). Virtual/delegate calls
    are invisible here: a method reached only through a vtable shows no callers."""
    from . import crash
    if re.fullmatch(r"(0x)?[0-9a-fA-F]+", target):
        rva, head = int(target, 16), f"{target}"
    else:
        rows = crash.find_method(target, limit=2)
        if not rows:
            return f"no method matches /{target}/"
        rva, head = rows[0][0], f"{rows[0][1]} @ {rows[0][0]:x}"
    refs = crash.xrefs(rva, limit)
    lines = [f"{a:x}  {kind:28s} {crash.method_at(a) or '?'}" for a, kind in refs]
    return f"{head}: {len(refs)} reference(s){' (limit hit)' if len(refs) >= limit else ''}\n" + "\n".join(lines)


@mcp.tool()
def dd2_weapons(job: int | None = None, weapon_prefix: str | None = None, name: str | None = None) -> str:
    """Weapon item table: id, name, WeaponID name, vocation mask (_Job = 1 << JobEnum) and stats.
    Filter by job (JobEnum 1-10), WeaponID prefix (e.g. 'wp07') or name substring. No filter = counts per type."""
    flt = json.dumps({"job": job, "prefix": weapon_prefix, "name": name})
    code = ("local f = json.load_string(" + repr(flt) + ")\n"
            "local groups, n = {}, 0\n"
            "for _, w in ipairs(D.weapons()) do\n"
            "  local okj = not f.job or (w.job_mask & (1 << f.job)) ~= 0\n"
            "  local okp = not f.prefix or w.weapon:sub(1, #f.prefix) == f.prefix\n"
            "  local okn = not f.name or w.name:lower():find(f.name:lower(), 1, true)\n"
            "  if okj and okp and okn then\n"
            "    n = n + 1\n"
            "    if f.job or f.prefix or f.name then print(w.id, w.name, w.weapon, 'mask ' .. w.job_mask, w.equip, 'lv' .. w.lv, 'P' .. w.phys, 'M' .. w.magic)\n"
            "    else local k = w.weapon:sub(1, 4) .. ' mask ' .. w.job_mask; groups[k] = (groups[k] or 0) + 1 end\n"
            "  end\n"
            "end\n"
            "for k, c in pairs(groups) do print(k, c) end\n"
            "return n .. ' weapons'")
    return game.format_result(game.eval_lua(code, timeout=30))


@mcp.tool()
def dd2_job(who: str = "player", set_job: int | None = None, force: bool = False) -> str:
    """Vocation state of 'player' or 'pawn1'..'pawn3': current job, JobIndex, exp per job, job bits.
    set_job changes the vocation (update phase). Jobs above 10 crash the game unless a mod first grows the
    per-job tables (2026-10-29: Wizard jobtables.lua), so they need force=True and belong only on a test
    character with the save guard armed."""
    if set_job is not None:
        if set_job > 10 and not force:
            return "refused: job > 10 crashed the game; pass force=True on a test character with the save guard armed"
        res = game.eval_lua(f"local c = D.party()[{json.dumps(who)}]\n"
                            f"c:call('get_Human'):call('get_JobContext'):call('set_Job(app.Character.JobEnum)', {int(set_job)})",
                            phase="update", timeout=15)
        if not res.get("ok"):
            return game.format_result(res)
    return game.format_result(game.eval_lua(f"return D.job({json.dumps(who)})", timeout=15))


@mcp.tool()
def dd2_items(name: str) -> str:
    """Find item ids by display-name substring (e.g. 'ferrystone')."""
    res = game.eval_lua(f"for _, i in ipairs(D.find_items({json.dumps(name)})) do "
                        "print(i.id, i.name, 'category ' .. tostring(i.category)) end")
    return game.format_result(res)


@mcp.tool()
def dd2_give_item(item_id: int, count: int = 1) -> str:
    """Give the player an item (update phase) and report the before/after count in the player's storage.
    Works for every category incl. quest letters (verified with Torn Letter 376, 2026-10-26)."""
    res = game.eval_lua(
        f"local before = D.item_count({item_id})\n"
        f"D.call(sdk.get_managed_singleton('app.ItemManager'), 'getItem(System.Int32, System.Int32, app.Character)', {item_id}, {count}, D.player())\n"
        f"return 'before ' .. before .. ', after ' .. D.item_count({item_id})", phase="update")
    return game.format_result(res)


@mcp.tool()
def dd2_recent_items(limit: int = 25, name: str | None = None) -> str:
    """Item log: the most recently changed inventory entries across player, pawn and stash storages,
    newest first (StorageData._UpdateIndex order; DD2 keeps no pickup history). Shows owner, count,
    subcategory, key-item flag (key items sit in their own inventory tab) and the NEW mark."""
    pat = json.dumps(name) if name else "nil"
    res = game.eval_lua(
        f"for _, r in ipairs(D.recent_items({int(limit)}, {pat})) do\n"
        "  print(r.upd, r.owner, r.id, r.name, 'x' .. r.num, r.sub, r.key_item and 'KEY' or '', r.new and 'NEW' or '')\n"
        "end")
    return game.format_result(res)


@mcp.tool()
def dd2_gold(delta: int = 0) -> str:
    """Read the player's gold and Rift Crystals; a non-zero delta adds (or deducts) gold first."""
    code = ("local im = sdk.get_managed_singleton('app.ItemManager')\n"
            + (f"im:call('addVersion', {delta}, false, false)\n" if delta else "")
            + "return 'gold ' .. im:call('get_Version') .. ', rift crystals ' .. im:call('get_Param')")
    return game.format_result(game.eval_lua(code, phase="update" if delta else "frame"))


@mcp.tool()
def dd2_deploy(project: str, undeploy: bool = False) -> str:
    """Copy a mod project's src/reframework/** into the game's reframework folder and its build/*.pak into pak_mods.
    undeploy=True moves those files back out into SaveBackups/undeployed-* (do it after every test session, game
    closed, so dev builds never run on the player's own saves)."""
    return game.undeploy(project) if undeploy else game.deploy(project)


@mcp.tool()
def dd2_install_bridge(reload: bool = True) -> str:
    """Install or update the dd2mcp eval bridge script in reframework/autorun; reload=True also re-runs it in
    the running game (bridge 0.5.0+ in memory), so bridge changes need no restart."""
    return game.reload_bridge() if reload else game.install_bridge()


@mcp.tool()
def dd2_log(pattern: str | None = None, lines: int = 60) -> str:
    """REFramework log for the current game run, optionally filtered by a substring (e.g. '[Bastion]', 'error')."""
    return game.log_tail(pattern, lines)


@mcp.tool()
def dd2_launch() -> str:
    """Start DD2 through Steam (no-op if it is running). Loading a save still needs the player."""
    return game.launch()


@mcp.tool()
def dd2_quit(since: str | None = None) -> str:
    """Close DD2 (hard kill: DD2 has no scripted clean exit yet; with the save guard armed nothing is lost) as soon
    as in-game testing ends. since = 'YYYY-MM-DD HH:MM[:SS]' also reports saves written since then (test units only?)."""
    from . import harness
    if not game.running():
        out = "DD2 is not running"
    else:
        harness._kill_game()
        out = "DD2 closed" if not game.running() else "DD2 still running after kill"
    return out + ("\n" + saves.touched(since) if since else "")


@mcp.tool()
def dd2_screenshot() -> str:
    """Capture the primary screen to a PNG and return its path; open it with an image-capable Read."""
    return game.screenshot()


@mcp.tool()
def dd2_take_control(unpause: bool = True) -> str:
    """Bring DD2 to the front for testing: remembers the window the player is in (their terminal), focuses the
    game and closes the focus-loss pause menu (checked through PauseManager, Esc only while it is open).
    Pair with dd2_hand_back whenever the player needs to read output or answer."""
    from . import control
    return control.take_control(unpause)


@mcp.tool()
def dd2_hand_back() -> str:
    """Return the screen to the window saved by dd2_take_control (the player's terminal); the game pauses itself."""
    from . import control
    return control.hand_back()


@mcp.tool()
def dd2_pause_state() -> str:
    """Whether the game is paused and by which app.PauseManager.PauseType values (5 = InGame pause menu)."""
    from . import control
    return json.dumps(control.pause_state())


@mcp.tool()
def dd2_key(scan: int = 1, focus: bool = True) -> str:
    """Send one key press (hardware scan code: Esc 0x01, Enter 0x1C, Space 0x39, Tab 0x0F) to the game,
    focusing it first unless focus=False."""
    from . import control
    if focus:
        r = control.win("focusgame")
        if not r.startswith("foreground"):
            return r
    return control.key(scan)


KEYS = {"esc": 0x01, "enter": 0x1C, "space": 0x39, "tab": 0x0F, "w": 0x11, "a": 0x1E, "s": 0x1F, "d": 0x20,
        "f": 0x21, "e": 0x12, "q": 0x10, "g": 0x22, "x": 0x2D, "z": 0x2C, "c": 0x2E, "v": 0x2F, "r": 0x13,
        "ctrl": 0x1D, "shift": 0x2A, "1": 0x02, "2": 0x03, "3": 0x04, "4": 0x05}
# app.CharacterInput.Action values = bits of UserInput.ButtonOnFlags (2026-10-31)
ACTIONS = {"attack": 1, "heavy": 2, "jump": 3, "dash": 4, "grab": 5, "interact": 6, "skill1": 7, "skill2": 8,
           "skill3": 9, "skill4": 10, "shift": 14, "come": 17, "go": 18, "help": 19, "wait": 20, "cancel": 21,
           "shoot": 23, "skill5": 27, "special": 29, "switchskill": 32}


@mcp.tool()
def dd2_action(actions: str = "", frames: int = 6, watch: float = 2.0, screenshot: bool = False) -> str:
    """Press game input actions directly (below the key bindings; needs bridge 0.8.0) and trace the player's
    behavior-tree nodes (layer 0 full body | layer 1 upper body) every 0.25 s for `watch` seconds.
    actions: '+'-joined names (attack heavy jump dash grab interact skill1-4 shift come go help wait cancel shoot
    skill5 special switchskill) or numbers (app.CharacterInput.Action); weapon skills need the modifier:
    'shift+skill2'. frames = player-input updates to hold (~60/s). Empty actions = trace only.
    Focuses the game and pages through a pausing popup (tutorials) first."""
    from . import control
    import time
    r = control.win("focusgame")
    if not r.startswith("foreground"):
        return r
    time.sleep(0.3)
    for _ in range(10):
        # F pages tutorials, but on the death screen it picks "Load from Autosave" (2026-10-31): never press there.
        if game.eval_lua("return D.dead and D.dead() or false").get("ret") == ["true"]:
            return "no living player (death screen, loading or title): not pressing anything; reload the scenario"
        st = control.pause_state()
        if not st or not st.get("paused"):
            break
        control.key(0x21)
        time.sleep(0.6)
    nodes = 'return table.concat(D.nodes(), " | ")'
    out = [f"before: {game.format_result(game.eval_lua(nodes))}"]
    if actions:
        bits = []
        for a in actions.lower().split("+"):
            if a not in ACTIONS and not a.isdigit():
                return f"unknown action {a!r}; known: {', '.join(ACTIONS)}"
            bits.append(ACTIONS.get(a, int(a) if a.isdigit() else 0))
        res = game.eval_lua(f"D.press({{{', '.join(map(str, bits))}}}, {frames}) return 'pressed'")
        if not res.get("ok"):
            return game.format_result(res) + "\n(bridge older than 0.8.0? dd2_install_bridge reload)"
    t0, last = time.monotonic(), None
    while time.monotonic() - t0 < watch:
        cur = game.format_result(game.eval_lua(nodes))
        if cur != last:
            out.append(f"+{time.monotonic() - t0:.2f}s {cur}")
            last = cur
        time.sleep(0.25)
    if screenshot:
        out.append(game.screenshot())
    return "\n".join(out)


@mcp.tool()
def dd2_keys(sequence: str, delay: float = 0.35, screenshot: bool = False) -> str:
    """Send a key sequence in one call (focuses the game once). Tokens separated by spaces or commas: key names
    (esc enter space tab w a s d f e q g x z c v r ctrl shift 1-4), 'name*N' to repeat, 'name~SECONDS' to hold (cutscene 'Hold to Skip': 'f~2'), hex scan codes
    ('0x21'), 'wait:SECONDS'.
    delay = pause after each key (menus drop presses that arrive faster than ~0.3 s). screenshot=True captures
    the screen at the end and returns its path. Example: 'esc wait:1 s*4 f wait:1 e'."""
    from . import control
    import time
    r = control.win("focusgame")
    if not r.startswith("foreground"):
        return r
    sent = []
    for tok in re.split(r"[\s,]+", sequence.strip().lower()):
        if not tok:
            continue
        if tok.startswith("wait:"):
            time.sleep(float(tok[5:]))
            sent.append(tok)
            continue
        tok, _, hold = tok.partition("~")
        name, _, rep = tok.partition("*")
        scan = KEYS.get(name) if name in KEYS else int(name, 16) if name.startswith("0x") else None
        if scan is None:
            return f"unknown key {name!r} after sending: {' '.join(sent)}"
        for _ in range(int(rep or 1)):
            control.key(scan, int(float(hold) * 1000) if hold else None)
            time.sleep(delay)
        sent.append(tok + (f"~{hold}" if hold else ""))
    out = "sent: " + " ".join(sent)
    if screenshot:
        out += "\nscreenshot: " + game.screenshot()
    return out


@mcp.tool()
def dd2_saves(action: str = "list", name: str | None = None) -> str:
    """Save snapshots. action: 'list', 'backup' (name = label), 'restore' (name = snapshot folder; game must be closed),
    'touched' (name = 'YYYY-MM-DD HH:MM[:SS]'): save files written since then, flagging any outside the test units."""
    if action == "list":
        return saves.listing()
    if action == "touched":
        return saves.touched(name) if name else "touched needs name = a start time"
    if action == "backup":
        return saves.backup(name or "manual")
    if action == "restore":
        return saves.restore(name) if name else "restore needs name"
    return f"unknown action {action!r}"


@mcp.tool()
def dd2_scenario(action: str = "list", name: str | None = None, slot: int | None = None,
                 area: str = "", notes: str = "", restart: bool = False, deploy: list[str] | None = None) -> str:
    """Test-harness save scenarios (single slots of the test character unit, default unit 0 = slots 0-4).
    action: 'list'; 'shelve' (name, slot, optional area/notes) archives a live slot; 'unshelve' (name) puts it
    back into its own slot (game must be closed); 'load' (name, restart) does it all: quits DD2 if restart=true,
    unshelves, launches, arms the save guard, passes the boot screens, starts the slot and verifies the play time.
    'shelve' also takes deploy: mod project folders 'load' deploys (dd2_deploy) before launch, for saves that
    need a mod (a save made as a modded vocation crashes without the mod).
    'load_slot' (slot, restart) loads any live slot as it is, read-only: the guard allows saves only on the test
    units, so e.g. the play-through can be checked without writing it.
    Slot types: 0 Auto, 1 Last Inn Rest (both auto-loadable), 2 unmoored-world restart, 3 Clear, 4 AddAuto.
    The game takes focus during boot; hand back with dd2_hand_back."""
    if action == "list":
        return saves.scenario_list()
    if action == "shelve":
        return saves.shelve(name, slot, area, notes, deploy) if name and slot is not None else "shelve needs name and slot"
    if action == "unshelve":
        return saves.unshelve(name) if name else "unshelve needs name"
    if action == "load":
        from . import harness
        return harness.load(name, restart) if name else "load needs name"
    if action == "load_slot":
        from . import harness
        return harness.load_slot(slot, restart) if slot is not None else "load_slot needs slot"
    return f"unknown action {action!r}"


@mcp.tool()
def dd2_god(on: bool = True) -> str:
    """Test god mode for the player (bridge 0.8.2+): skips HitController.damageProc and holds IsNoDie/IsDamageZero
    every frame. Knockback still happens. Resets on game restart. Use it for any combat test: enemies killed the
    test character within seconds several times (2026-10-31)."""
    return game.format_result(game.eval_lua(f"return D.god({'true' if on else 'false'})"))


@mcp.tool()
def dd2_watch(action: str = "sessions", session: str = "", stream: str = "node", n: int = 30) -> str:
    """The in-game WATCHER (dd2mcp/lua/dd2mcp_watch.lua): read-only observation of real play, written as JSON lines to reframework/data/dd2mcp/watch_<session>_<stream>.jsonl.
    Streams: meta, state (1 Hz: player pos/job/play time/node), node (action-node changes of the player, pawns and enemies within 60 m), snap (2 s snapshots),
    key (keyboard/mouse), damage (every calcDamageValue: victim, damage, type, element), skill (job selector requests + custom skill id -> action), gui (windows
    opened/closed), item (inventory changes), inventory (one-time field dumps of the player/pawn/enemies: where AI state lives), hook (extra hooks from
    reframework/data/dd2mcp/watch_hooks.txt, 'type::method signature' per line), errors.
    Actions: 'install' / 'uninstall' (autorun script; takes effect at the next game start), 'sessions', 'summary' (session '' = latest), 'tail' (session, stream, n)."""
    from . import watch
    if action == "install":
        return watch.install()
    if action == "uninstall":
        return watch.uninstall()
    if action == "sessions":
        return watch.sessions()
    sess = session or watch.latest()
    if not sess:
        return "no watcher sessions yet"
    if action == "summary":
        return watch.summary(sess)
    if action == "tail":
        return watch.tail(sess, stream, n)
    return "action: install | uninstall | sessions | summary | tail"


_SPAWN_LOAD = 'local S = loadfile("D:/DD2Modding/dd2-mcp/dd2mcp/lua/spawnlib.lua")()\n'
_CATS = {"monster": 3, "enemy": 3, "pawn": 1, "straypawn": 2, "npc": 4}


@mcp.tool()
def dd2_spawn(action: str = "templates", name: str = "", cat: str = "monster", dx: float = 4.0, dz: float = 0.0, wait: float = 40.0, near: float = 25.0) -> str:
    """Spawn / despawn game characters through the game's own GenerateManager (dd2mcp/lua/spawnlib.lua; verified 2026-11-03 for MONSTERS).
    Needs the game focused and running a loaded save (spawning is asynchronous: the instance appears 8-20 s after the request).
      'templates' list the character names you can spawn in `cat` (only characters the loaded world has a saved request for: the request is cloned).
      'spawn'     name = e.g. 'ch221000_00' (goblin), dx/dz = metres from the player (east / south). One-shot (no regenerate, no re-save). Waits up to
                  `wait` s for the instance and reports its name and distance (it may wander: AI active).
      'despawn'   name (or '' = every monster instance within `near` m of the player except the player party): removes it for good.
      'list'      instances of `name` (or all monsters within `near` m) with distances.
    cat: monster (default) | straypawn | pawn. PAWN: the main pawn instance can be created but its position is not controlled yet; NPC: no stored
    requests (NPCs use other tables) - not solved. Freeze the camp's existing enemies first (tests/spawn_lab_setup.py) and turn god mode on (dd2_god)."""
    import time as _t
    c = _CATS.get(cat.lower())
    if c is None:
        return "cat must be one of " + ", ".join(_CATS)
    def run(code, phase="frame"):
        r = game.eval_lua(_SPAWN_LOAD + code, phase=phase)
        return (r.get("ret") or [None])[0] if r.get("ok") else "ERR " + str(r.get("err"))
    if action == "templates":
        return str(run(f"local seen, n = {{}}, {{}} for _, t in ipairs(S.templates({c})) do if not seen[t.name] then seen[t.name] = 1 n[#n+1] = t.name end end table.sort(n) return table.concat(n, ' ')"))
    cid = f"(function() for _, t in ipairs(S.templates({c})) do if t.name == {name!r} then return t.id end end end)()"
    listing = ("local u = D.player_pos() local rows = {} local function add(inf) local go = inf:get_field('<Instance>k__BackingField') if go then "
               "local p = go:get_Transform():get_Position() rows[#rows+1] = go:get_Name() .. string.format(' d=%.1f', math.sqrt((p.x-u.x)^2 + (p.z-u.z)^2)) end end ")
    if action == "list":
        if name:
            return str(run(f"{listing} local id = {cid} if not id then return 'unknown name (see templates)' end for _, i in ipairs(S.list({c}, id)) do add(i) end return table.concat(rows, ', ')"))
        return str(run(f"{listing} local l = S.iim():call('getInstanceInfoList(app.GeneratorCategory)', {c}) for i = 0, l:call('get_Count') - 1 do add(l:call('get_Item', i)) end table.sort(rows) return table.concat(rows, ', ')"))
    if action == "spawn":
        if not name:
            return "name required (see templates)"
        before = str(run(f"local id = {cid} if not id then return 'ERR' end local n = {{}} for _, i in ipairs(S.list({c}, id)) do n[#n+1] = i:get_field('<Instance>k__BackingField'):get_Name() end return table.concat(n, ',')"))
        if before.startswith("ERR"):
            return "unknown name for this category (see templates)"
        known = set(x for x in before.split(",") if x)
        r = run(f"local id = {cid} local ctrl, why = S.spawn({c}, id, {dx}, {dz}, 7) return ctrl and 'requested' or tostring(why)", phase="update")
        if r != "requested":
            return f"spawn failed: {r}"
        deadline = _t.time() + wait
        while _t.time() < deadline:
            _t.sleep(2.5)
            now = str(run(f"{listing} local id = {cid} for _, i in ipairs(S.list({c}, id)) do add(i) end return table.concat(rows, ',')"))
            fresh = [x for x in now.split(",") if x and x.split(" ")[0] not in known]
            if fresh:
                return "spawned: " + ", ".join(fresh)
        return f"requested, but no new instance within {wait:.0f} s"
    if action == "despawn":
        sel = (f"local id = {cid} if not id then return 'unknown name' end local list = S.list({c}, id)" if name else
               f"local list = {{}} local l = S.iim():call('getInstanceInfoList(app.GeneratorCategory)', {c}) for i = 0, l:call('get_Count') - 1 do list[#list+1] = l:call('get_Item', i) end")
        return str(run(f"{sel} local u = D.player_pos() local n = 0 for _, i in ipairs(list) do local go = i:get_field('<Instance>k__BackingField') if go then "
                       f"local p = go:get_Transform():get_Position() if math.sqrt((p.x-u.x)^2 + (p.z-u.z)^2) <= {near} then S.despawn({c}, i) n = n + 1 end end end return 'despawn requested for ' .. n", phase="update"))
    return "action: templates | spawn | despawn | list"


_ENEMIES_LUA = r"""
local pl = sdk.get_managed_singleton('app.CharacterManager'):get_ManualPlayerHuman():get_GameObject():get_Transform():get_Position()
local l = sdk.get_managed_singleton('app.EnemyManager'):get_field('_EnemyList')
local rows = {}
for i = 0, l:call('get_Count') - 1 do
  local go = l:call('get_Item', i):get_GameObject()
  local ok, p = pcall(function() return go:get_Transform():get_Position() end)
  if ok and p then
    local okc, ph = pcall(function() return go:getComponent(sdk.typeof('app.Character')):get_field('PhysiqueData') end)
    rows[#rows+1] = {go:get_Name(), math.sqrt((p.x-pl.x)^2 + (p.y-pl.y)^2 + (p.z-pl.z)^2), okc and ph or 0}
  end
end
table.sort(rows, function(a, b) return a[2] < b[2] end)
local out = {}
local SZ = {[0]='S', 'M', 'L', 'B'}
for i = 1, math.min(#rows, %d) do out[#out+1] = string.format('%%s[%%s] d=%%.0f', rows[i][1], SZ[rows[i][3]] or '?', rows[i][2]) end
return #rows .. ' enemies' .. (#out > 0 and ': ' or '') .. table.concat(out, ', ')
"""

_KEYLOC_LUA = r"""
local td = sdk.find_type_definition('app.AIKeyLocation')
local out = {}
for _, f in ipairs(td:get_fields()) do
  if f:is_static() then out[#out+1] = f:get_data(nil) .. '=' .. f:get_name() end
end
return table.concat(out, ' ')
"""

@mcp.tool()
def dd2_catalog(action: str = "overview", query: str = "", limit: int = 60) -> str:
    """Offline map of the game's systems (no game needed): built from the method map, the IL2CPP enum/inheritance cache and the RSZ dump.
    'overview': subsystems with type/method counts and their managers. 'subsystem' query=name (combat, AI, pawns, enemies, npc, character,
    items, gui, sound, effects, physics, world, save): its biggest types. 'find' query=regex: matching types, enum types, enum VALUES
    (e.g. 'Hitdown', 'Caught') and methods. 'type' query=name (full or trailing): parents, derived types, enum values, nested enums,
    serialized fields, methods. 'export': write the markdown map to /mnt/d/DD2Modding/research/catalog/SYSTEMS.md (outside the repos,
    shared by all mods). Use this BEFORE probing live to find which manager/enum owns a feature."""
    from . import catalog
    try:
        if action == "overview":
            return catalog.overview()
        if action == "subsystem":
            return catalog.subsystem(query, limit)
        if action == "find":
            return catalog.find(query, limit)
        if action == "type":
            return catalog.type_info(query, limit)
        if action == "export":
            return catalog.export_md(query or "/mnt/d/DD2Modding/research/catalog/SYSTEMS.md")
    except FileNotFoundError as e:
        return str(e)
    return "actions: overview, subsystem, find, type, export"


@mcp.tool()
def dd2_warp(action: str = "list", target: str = "", count: int = 8, wait: float = 7.0) -> str:
    """Warp the player to a game AI key location (camps, towns, dungeon entrances) and look at the enemies there.
    'list': AIKeyLocation names matching the lua pattern `target` (e.g. 'Camp_0%d%d'). 'warp': go to `target` (a name like
    Gimmick_Camp_049 or a numeric id); the player appears after a few seconds and a 'Pawn Commands' tutorial popup may
    pause the game (dd2_keys 'f*5'). 'enemies': the `count` nearest enemies of app.EnemyManager with size class S/M/L/B (Character.PhysiqueData) and distances (names look like
    ch221001_00_3: ch221 = ghost, ch252 = rock golem; list in docs). 'survey': warp to every location matching `target`,
    wait `wait` s each, report the nearest enemies (locations that don't move the player are reported as 'no warp') - use it to find a test spot with small enemies (note: set dd2_god first and
    expect long runtimes; one location per ~`wait` seconds). Positions of characters cannot be set by transform
    (controllers override it), warping is the only way to move the player."""
    import time
    if action in ("warp", "survey"):
        from . import control
        control.take_control(True)  # the game pauses while unfocused, and a paused game never runs the warp
    if action == "list":
        pat = target or "."
        r = game.format_result(game.eval_lua(_KEYLOC_LUA))
        import re as _re
        items = [x for x in r.replace("=> ", "").split(" ") if _re.search(pat.replace("%d", r"\d"), x)]
        return " ".join(items)[:6000] or "no match"
    if action == "enemies":
        return game.format_result(game.eval_lua(_ENEMIES_LUA % count))

    import re as _re
    table = {}
    for item in game.format_result(game.eval_lua(_KEYLOC_LUA)).replace("=> ", "").split(" "):
        if "=" in item:
            k, _, v = item.partition("=")
            table[v] = k

    def warp_to(t: str) -> str:
        ident = t if t.isdigit() else table.get(t)
        if ident is None:
            return f"unknown location {t!r} (dd2_warp list)"
        code = ("local crime = sdk.get_managed_singleton('app.NPCManager'):get_field('CrimeManager')\n"
                f"crime:call('warpPlayerToAIKeyLocation(app.AIKeyLocation, System.Int32, System.Action)', {ident}, 0, nil)\nreturn 'warping to {t} ({ident})'")
        return game.format_result(game.eval_lua(code, phase="update"))

    if action == "warp":
        return warp_to(target)
    if action == "survey":
        names = [n for n in table if _re.search((target or ".").replace("%d", r"\d"), n)]
        lines = []

        def where():
            r = game.format_result(game.eval_lua("local p = D.player_pos(); return string.format('%.1f %.1f %.1f', p.ux, p.uy, p.uz)"))
            try:
                return tuple(float(x) for x in r.replace("=> ", "").split())
            except ValueError:
                return None

        control.take_control(True)
        control.key(0x21); time.sleep(0.6); control.key(0x21); time.sleep(0.6)  # clear a pending tutorial popup
        for n in names:
            control.take_control(True)
            before = where()
            warp_to(n)
            time.sleep(wait)
            control.key(0x21)  # 'f': pages the "Pawn Commands" tutorial that pops up after warps
            time.sleep(0.5)
            after = where()
            if before and after and sum((a - b) ** 2 for a, b in zip(before, after)) ** 0.5 < 30:  # a real warp moves the player far
                lines.append(f"{n}: no warp (not a valid target here)")
            else:
                lines.append(f"{n}: " + game.format_result(game.eval_lua(_ENEMIES_LUA % count)))
        return "\n".join(lines) or "no locations match"
    return f"unknown action {action!r}"


@mcp.tool()
def dd2_saveguard(action: str = "status", autosave: bool = False, units: list[int] | None = None) -> str:
    """Bridge save guard. 'arm' refuses every save outside the test units (default [0]) and, unless
    autosave=True, every save to an Auto/AddAuto slot (the game also saves slot 0 on load). 'disarm', 'status'.
    Arm before loading a scenario so the load-time autosave cannot overwrite it. Resets on game restart."""
    if action == "arm":
        opts = json.dumps({"units": units or [0], "autosave": autosave})
        code = f"return D.saveguard.arm(json.load_string({opts!r}))"
    elif action == "disarm":
        code = "return D.saveguard.disarm()"
    else:
        code = "return D.saveguard.status()"
    return game.format_result(game.eval_lua(code, timeout=10))


@mcp.tool()
def dd2_vortex(action: str = "status", project: str | None = None, profile: str | None = None) -> str:
    """Vortex profiles for DD2 (starts Vortex if needed; refuses switches while DD2 runs).
    action: 'status' (active game/profile, enabled mods); 'profiles' (DD2 profiles); 'dev' / 'release' (project =
    mod project dir: switch to its dev or release-test profile from <project>/dd2mod.json, creating it if missing,
    enable its required mods (REFramework) and deploy); 'switch' (profile = name); 'play' (return to the profile
    that was active before the last switch, BG3 included); 'install' (project: build the release zip, import and
    install it in the release profile with Fluffy enabled; then dd2_fluffy 'enable' puts it in the game)."""
    from . import vortex
    if action == "status":
        return vortex.ensure_running() or vortex.status()
    if action == "profiles":
        return vortex.ensure_running() or vortex.profiles()
    if action in ("dev", "release"):
        if not project:
            return f"{action} needs project (the mod project directory)"
        cfg = vortex.project_cfg(project)
        return vortex.switch(cfg[f"{action}_profile"], cfg["requires"], create=True)
    if action == "switch":
        return vortex.switch(profile) if profile else "switch needs profile"
    if action == "play":
        return vortex.back_to_play()
    if action == "install":
        if not project:
            return "install needs project"
        return vortex.ensure_running() or vortex.install_release(project)
    return f"unknown action {action!r}"


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
