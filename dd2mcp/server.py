"""MCP server: Dragon's Dogma 2 modding through REFramework (live Lua eval, introspection, deploy, saves)."""
import json

from mcp.server.mcpserver import MCPServer

from . import game, saves

mcp = MCPServer(
    "dd2",
    instructions=(
        "Dragon's Dogma 2 modding via REFramework. dd2_eval runs Lua in the live game through a file bridge; "
        "use phase='update' for anything that changes game state (spawning prefabs, giving items, gold): "
        "the same call made from the frame phase silently does nothing. Helpers on the Lua global D: "
        "D.methods(type, pat), D.fields(type, pat), D.player(), D.player_pos(), D.find_items(name), "
        "D.item_count(id). Mod globals (e.g. Bastion's H) are reachable too. Snapshot saves (dd2_saves) "
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
    set_job changes the vocation (update phase). Jobs above 10 crashed the game (2026-10-29: c0000005 ~1 min
    after set_Job(11)), so they need force=True and belong only on a test character with the save guard armed."""
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
def dd2_deploy(project: str) -> str:
    """Copy a mod project's src/reframework/** into the game's reframework folder."""
    return game.deploy(project)


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
                 area: str = "", notes: str = "", restart: bool = False) -> str:
    """Test-harness save scenarios (single slots of the test character unit, default unit 0 = slots 0-4).
    action: 'list'; 'shelve' (name, slot, optional area/notes) archives a live slot; 'unshelve' (name) puts it
    back into its own slot (game must be closed); 'load' (name, restart) does it all: quits DD2 if restart=true,
    unshelves, launches, arms the save guard, passes the boot screens, starts the slot and verifies the play time.
    Slot types: 0 Auto, 1 Last Inn Rest (both auto-loadable), 2 unmoored-world restart, 3 Clear, 4 AddAuto.
    The game takes focus during boot; hand back with dd2_hand_back."""
    if action == "list":
        return saves.scenario_list()
    if action == "shelve":
        return saves.shelve(name, slot, area, notes) if name and slot is not None else "shelve needs name and slot"
    if action == "unshelve":
        return saves.unshelve(name) if name else "unshelve needs name"
    if action == "load":
        from . import harness
        return harness.load(name, restart) if name else "load needs name"
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
    that was active before the last switch, BG3 included)."""
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
    return f"unknown action {action!r}"


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
