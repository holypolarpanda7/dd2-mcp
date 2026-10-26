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
def dd2_install_bridge() -> str:
    """Install or update the dd2mcp eval bridge script in reframework/autorun."""
    return game.install_bridge()


@mcp.tool()
def dd2_log(pattern: str | None = None, lines: int = 60) -> str:
    """REFramework log for the current game run, optionally filtered by a substring (e.g. '[Bastion]', 'error')."""
    return game.log_tail(pattern, lines)


@mcp.tool()
def dd2_launch() -> str:
    """Start DD2 through Steam (no-op if it is running). Loading a save still needs the player."""
    return game.launch()


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
    """Save snapshots. action: 'list', 'backup' (name = label), 'restore' (name = snapshot folder; game must be closed)."""
    if action == "list":
        return saves.listing()
    if action == "backup":
        return saves.backup(name or "manual")
    if action == "restore":
        return saves.restore(name) if name else "restore needs name"
    return f"unknown action {action!r}"


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
