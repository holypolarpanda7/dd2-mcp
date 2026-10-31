"""MCP server: Dragon's Dogma 2 modding through REFramework (live Lua eval, introspection, deploy, saves)."""
import json
import pathlib
import re

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
def dd2_rszgen(names: list[str]) -> str:
    """Generate RSZ layouts from the running game for these type names (and their parents) into
    research/rsz/rsz_live_dd2.json; dd2_rsz_file uses them for types the community layouts lack. Type hashes are
    murmur3(utf-8 name): to name an unknown hash, collect candidate names from live objects (e.g. walk the objects
    holding the data) and compare with dd2mcp.murmur.type_hash. Needs the game running."""
    from . import rszgen
    return rszgen.generate(names)


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
